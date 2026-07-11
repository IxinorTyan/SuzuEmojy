import os
from datetime import datetime
import shutil
import json
import hashlib
import io
from PIL import Image

class StorageService:
    def __init__(self):
        import sys
        # 确保数据目录在项目根目录下的 data/images
        if getattr(sys, 'frozen', False):
            # 打包后，数据目录直接放在 exe 所在目录（即 bin 目录）下
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        self.images_dir = os.path.join(self.base_dir, "data", "images")
        self.order_file = os.path.join(self.base_dir, "data", "order.json")
        self.categories_file = os.path.join(self.base_dir, "data", "categories.json")
        self.metadata_file = os.path.join(self.base_dir, "data", "metadata.json")
        self.icons_file = os.path.join(self.base_dir, "data", "category_icons.json")
        self.hashes_file = os.path.join(self.base_dir, "data", "hashes.json")
        
        # 如果目录不存在，自动创建
        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)
            
        self._hashes_cache = self._load_hashes()
        
        # 内存缓存与脏标记
        self._images_cache = []
        self._images_dirty = True
        
        self._categories_cache = {}
        self._image_to_categories_cache = {}
        self._categories_dirty = True
        
        self._metadata_cache = {}
        self._metadata_dirty = True

    def _load_hashes(self):
        if not os.path.exists(self.hashes_file):
            return {}
        try:
            with open(self.hashes_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_hashes(self):
        try:
            with open(self.hashes_file, 'w', encoding='utf-8') as f:
                json.dump(self._hashes_cache, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存哈希缓存失败: {e}")

    def _calculate_pixel_hash(self, img):
        """计算图片纯像素数据的 MD5 哈希值，用于精准去重"""
        try:
            # 统一转换为 RGBA 模式以保证像素数据结构一致
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            return hashlib.md5(img.tobytes()).hexdigest()
        except Exception as e:
            print(f"[WARNING] 计算像素哈希失败: {e}")
            return None

    def _calculate_bytes_hash(self, data_bytes):
        """计算二进制数据的 MD5 哈希值（用于动图等无法提取单帧像素的场景）"""
        return hashlib.md5(data_bytes).hexdigest()

    def _migrate_hashes_if_needed(self):
        """静默迁移：将旧的基于文件二进制的哈希转换为基于像素的哈希"""
        if not os.path.exists(self.images_dir):
            return
            
        migrated = False
        actual_filenames = os.listdir(self.images_dir)
        
        # 检查是否需要迁移（如果缓存为空，但有图片，说明是第一次运行新逻辑）
        if not self._hashes_cache and actual_filenames:
            print("[INFO] 开始静默迁移图片哈希数据...")
            for filename in actual_filenames:
                filepath = os.path.join(self.images_dir, filename)
                try:
                    with Image.open(filepath) as img:
                        # 动图保持二进制哈希，静态图使用像素哈希
                        if getattr(img, "is_animated", False):
                            with open(filepath, "rb") as f:
                                file_hash = self._calculate_bytes_hash(f.read())
                        else:
                            file_hash = self._calculate_pixel_hash(img)
                            
                        if file_hash:
                            self._hashes_cache[file_hash] = filename
                            migrated = True
                except Exception as e:
                    print(f"[WARNING] 迁移图片 {filename} 失败: {e}")
                    
            if migrated:
                self._save_hashes()
                print("[INFO] 哈希数据迁移完成。")

    def _to_filename(self, filepath):
        """将绝对路径转换为单纯的文件名，便于在 JSON 中持久化存储"""
        return os.path.basename(filepath)

    def _to_abspath(self, filename):
        """将 JSON 中读取的文件名转换为当前环境的绝对路径"""
        # 如果历史数据里存了绝对路径，兼容处理一下
        if os.path.isabs(filename):
            return os.path.normcase(os.path.abspath(filename))
        return os.path.normcase(os.path.abspath(os.path.join(self.images_dir, filename)))

    def get_all_images(self):
        """扫描并返回所有保存的图片绝对路径列表（支持自定义排序）"""
        if not self._images_dirty:
            return self._images_cache
            
        if not os.path.exists(self.images_dir):
            self._images_cache = []
            self._images_dirty = False
            return self._images_cache
            
        supported_formats = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        actual_filenames = []
        for filename in os.listdir(self.images_dir):
            if filename.lower().endswith(supported_formats):
                actual_filenames.append(filename)
        
        # 默认按文件名倒序（最新的在前）
        actual_filenames.sort(reverse=True)
        
        # 尝试读取排序配置
        if os.path.exists(self.order_file):
            try:
                with open(self.order_file, 'r', encoding='utf-8') as f:
                    saved_order = json.load(f)
                
                # 兼容旧版本：如果里面是绝对路径，全部提取为纯文件名
                saved_filenames = [self._to_filename(p) for p in saved_order]
                
                # 算法优化：使用 set 进行 O(1) 查找
                actual_set = set(actual_filenames)
                saved_set = set(saved_filenames)
                
                # 1. 过滤掉 JSON 中有但实际硬盘上已经不存在的文件名
                valid_saved_filenames = [f for f in saved_filenames if f in actual_set]
                
                # 2. 找出实际硬盘上有，但 JSON 里没记录的新文件（这些排在最前面）
                new_filenames = [f for f in actual_filenames if f not in saved_set]
                
                ordered_filenames = new_filenames + valid_saved_filenames
                self._images_cache = [self._to_abspath(f) for f in ordered_filenames]
                self._images_dirty = False
                return self._images_cache
            except Exception as e:
                print(f"读取排序配置失败: {e}")
                
        self._images_cache = [self._to_abspath(f) for f in actual_filenames]
        self._images_dirty = False
        return self._images_cache

    def save_order(self, filepaths):
        """保存用户自定义的表情包排序顺序（只保存文件名）"""
        filenames = [self._to_filename(p) for p in filepaths]
        try:
            with open(self.order_file, 'w', encoding='utf-8') as f:
                json.dump(filenames, f, indent=4, ensure_ascii=False)
            self._images_dirty = True
        except Exception as e:
            print(f"保存排序失败: {e}")

    # ==========================
    # 分类 (虚拟文件夹) 相关逻辑
    # ==========================
    
    def get_all_categories(self):
        """获取所有分类及其包含的图片绝对路径列表"""
        if not self._categories_dirty:
            return self._categories_cache
            
        if not os.path.exists(self.categories_file):
            self._categories_cache = {}
            self._image_to_categories_cache = {}
            self._categories_dirty = False
            return self._categories_cache
            
        try:
            with open(self.categories_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # 数据迁移和清理：兼容旧版绝对路径，只向外暴露存在的绝对路径
                all_real_images = set(self.get_all_images()) # get_all_images 现在返回规范的绝对路径
                cleaned_data = {}
                reverse_map = {}
                
                for category, paths in data.items():
                    # 把存着的文件名（或旧绝对路径）全部转为当前的绝对路径
                    abs_paths = [self._to_abspath(p) for p in paths]
                    valid_paths = [p for p in abs_paths if p in all_real_images]
                    cleaned_data[category] = valid_paths
                    
                    # 构建反向映射缓存，用于 O(1) 查找图片所属分类
                    for p in valid_paths:
                        if p not in reverse_map:
                            reverse_map[p] = []
                        reverse_map[p].append(category)
                        
                self._categories_cache = cleaned_data
                self._image_to_categories_cache = reverse_map
                self._categories_dirty = False
                return self._categories_cache
        except Exception as e:
            print(f"[ERROR] StorageService.get_all_categories: 读取分类失败 {e}")
            self._categories_cache = {}
            self._image_to_categories_cache = {}
            self._categories_dirty = False
            return self._categories_cache

    def save_categories(self, categories_data):
        """保存分类数据，内部将绝对路径全部转换为文件名以确保可移植性"""
        # categories_data 传入的是 {category: [abs_path1, abs_path2...]}
        portable_data = {}
        for category, paths in categories_data.items():
            portable_data[category] = [self._to_filename(p) for p in paths]
            
        try:
            with open(self.categories_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
            self._categories_dirty = True
        except Exception as e:
            print(f"[ERROR] StorageService.save_categories: 保存分类失败 {e}")

    def add_category(self, category_name):
        """新建一个分类"""
        categories = self.get_all_categories()
        if category_name not in categories:
            categories[category_name] = []
            self.save_categories(categories)
            return True
        return False

    def remove_category(self, category_name):
        """删除一个分类"""
        categories = self.get_all_categories()
        if category_name in categories:
            del categories[category_name]
            self.save_categories(categories)
            
            # 同步删除对应的自定义图标记录
            icons = self.get_all_category_icons()
            if category_name in icons:
                del icons[category_name]
                self.save_category_icons(icons)
                
            return True
        return False

    def add_image_to_category(self, filepath, category_name):
        """将图片添加到指定分类"""
        categories = self.get_all_categories()
        if category_name not in categories:
            categories[category_name] = []
            
        # 统一转换为绝对路径进行比较，避免路径格式不一致导致重复添加
        abs_filepath = self._to_abspath(filepath)
        if abs_filepath not in categories[category_name]:
            categories[category_name].append(abs_filepath)
            self.save_categories(categories)
            return True
        return False

    def remove_image_from_category(self, filepath, category_name):
        """将图片从指定分类移除"""
        categories = self.get_all_categories()
        abs_filepath = self._to_abspath(filepath)
        if category_name in categories and abs_filepath in categories[category_name]:
            categories[category_name].remove(abs_filepath)
            self.save_categories(categories)
            return True
        return False
        
    def get_images_by_category(self, category_name):
        """获取特定分类下的所有图片"""
        all_ordered = self.get_all_images()
        
        if category_name == "全部表情" or category_name is None:
            return all_ordered
            
        categories = self.get_all_categories()
        
        if category_name == "未分类":
            # 筛选出不属于任何分类的图片
            classified_paths = set()
            for paths in categories.values():
                classified_paths.update(paths)
            return [p for p in all_ordered if p not in classified_paths]
            
        paths = categories.get(category_name, [])
        # 算法优化：使用 set 进行 O(1) 查找
        paths_set = set(paths)
        # 为了保证显示顺序跟 "全部" 中一致，我们基于所有图片的顺序来进行过滤
        result = [p for p in all_ordered if p in paths_set]
        return result

    def get_categories_by_image(self, filepath):
        """反向查询：获取指定图片所属的所有分类名称列表"""
        # 确保缓存已加载
        self.get_all_categories()
        # 算法优化：直接从反向映射缓存中 O(1) 获取
        return self._image_to_categories_cache.get(filepath, [])

    # ==========================
    # 分类图标 (Category Icons) 相关逻辑
    # ==========================
    
    def get_all_category_icons(self):
        if not os.path.exists(self.icons_file):
            return {}
        try:
            with open(self.icons_file, 'r', encoding='utf-8') as f:
                icons = json.load(f)
                for cat, val in icons.items():
                    if any(val.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                        icons[cat] = self._to_abspath(val)
                return icons
        except Exception:
            return {}
            
    def save_category_icons(self, icons_data):
        portable_data = {}
        for cat, val in icons_data.items():
            if any(val.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                portable_data[cat] = self._to_filename(val)
            else:
                portable_data[cat] = val
        try:
            with open(self.icons_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] StorageService.save_category_icons: 保存分类图标失败 {e}")
            
    def set_category_icon(self, category_name, filepath):
        icons = self.get_all_category_icons()
        icons[category_name] = filepath
        self.save_category_icons(icons)
        
    def get_category_icon(self, category_name):
        icons = self.get_all_category_icons()
        return icons.get(category_name, None)

    # ==========================
    # 关键词 (Metadata) 相关逻辑
    # ==========================
    
    def get_all_metadata(self):
        """获取所有图片的关键词元数据，并映射回绝对路径"""
        if not self._metadata_dirty:
            return self._metadata_cache
            
        if not os.path.exists(self.metadata_file):
            self._metadata_cache = {}
            self._metadata_dirty = False
            return self._metadata_cache
            
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 将存入的文件名转回绝对路径
                self._metadata_cache = {self._to_abspath(k): v for k, v in data.items()}
                self._metadata_dirty = False
                return self._metadata_cache
        except Exception as e:
            print(f"[ERROR] StorageService.get_all_metadata: 读取元数据失败 {e}")
            self._metadata_cache = {}
            self._metadata_dirty = False
            return self._metadata_cache

    def save_metadata(self, metadata):
        """保存关键词元数据，内部将绝对路径转换为文件名"""
        portable_data = {self._to_filename(k): v for k, v in metadata.items()}
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
            self._metadata_dirty = True
        except Exception as e:
            print(f"[ERROR] StorageService.save_metadata: 保存元数据失败 {e}")

    def get_image_keywords(self, filepath):
        """获取指定图片的关键词"""
        metadata = self.get_all_metadata()
        return metadata.get(self._to_abspath(filepath), "")

    def set_image_keywords(self, filepath, keywords_str):
        """设置指定图片的关键词"""
        metadata = self.get_all_metadata()
        metadata[self._to_abspath(filepath)] = keywords_str
        self.save_metadata(metadata)

    def search_images(self, keyword, category_name="全部表情"):
        """根据关键词搜索特定分类（或全部）下的图片"""
        images = self.get_images_by_category(category_name)
        if not keyword or not keyword.strip():
            return images
            
        keyword = keyword.strip().lower()
        metadata = self.get_all_metadata()
        result = []
        for img in images:
            # 获取该图片的关键词，转小写进行匹配
            img_kw = metadata.get(img, "").lower()
            if keyword in img_kw:
                result.append(img)
        return result

    def generate_new_filename(self, extension=".png"):
        """生成基于时间戳的新文件名"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"{timestamp}{extension}"

    def _standardize_and_save(self, data_bytes, original_ext):
        """
        核心逻辑：使用 Pillow 对图片进行标准化处理，查重并保存。
        :param data_bytes: 原始图片的二进制数据
        :param original_ext: 原始扩展名（用于动图回退）
        :return: (保存后的绝对路径, 是否是已存在的重复图片)
        """
        try:
            img = Image.open(io.BytesIO(data_bytes))
            is_animated = getattr(img, "is_animated", False)
            
            if is_animated:
                # 动图不进行重编码，直接使用二进制哈希查重并保存原始数据
                file_hash = self._calculate_bytes_hash(data_bytes)
                final_bytes = data_bytes
                final_ext = original_ext if original_ext in ['.gif', '.webp'] else '.gif'
            else:
                # 静态图：强制转换为 RGBA 模式
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                    
                # 计算像素哈希查重
                file_hash = self._calculate_pixel_hash(img)
                
                # 创建一个全新的纯净画布，剥离所有 ICC profile 和 EXIF 等元数据
                # 这是防止 Qt 读取 PNG 失败 (Failed to read image) 的关键步骤
                clean_img = Image.new('RGBA', img.size)
                clean_img.paste(img, (0, 0))
                
                output_io = io.BytesIO()
                # 使用 optimize=True 进行无损压缩优化
                clean_img.save(output_io, format="PNG", optimize=True)
                final_bytes = output_io.getvalue()
                final_ext = '.png'
                
            # 查重逻辑
            if file_hash and file_hash in self._hashes_cache:
                existing_filename = self._hashes_cache[file_hash]
                existing_path = self._to_abspath(existing_filename)
                if os.path.exists(existing_path):
                    return existing_path, True
                else:
                    del self._hashes_cache[file_hash]
                    
            # 保存新文件
            filename = self.generate_new_filename(final_ext)
            filepath = os.path.join(self.images_dir, filename)
            
            with open(filepath, 'wb') as f:
                f.write(final_bytes)
                
            if file_hash:
                self._hashes_cache[file_hash] = filename
                self._save_hashes()
                
            # 统一返回标准化后的绝对路径，防止后续 UI 交互中出现路径字符串匹配失败的 Bug
            self._images_dirty = True
            return self._to_abspath(filename), False
            
        except Exception as e:
            print(f"[ERROR] 图片标准化保存失败: {e}")
            return None, False

    def save_image(self, qimage):
        """
        保存 QImage 到本地
        :param qimage: PySide6.QtGui.QImage 对象
        :return: (保存后的绝对路径, 是否是已存在的重复图片)
        """
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice
        
        # 先将 QImage 转换为字节流
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        qimage.save(buffer, "PNG")
        image_bytes = byte_array.data()
        
        # 统一走标准化流程
        return self._standardize_and_save(image_bytes, ".png")

    def save_file(self, source_path):
        """
        读取本地文件并进行标准化保存
        :param source_path: 原始文件路径
        :return: (保存后的绝对路径, 是否是已存在的重复图片)
        """
        if not os.path.exists(source_path):
            return None, False
            
        try:
            with open(source_path, 'rb') as f:
                data_bytes = f.read()
                
            _, ext = os.path.splitext(source_path)
            ext = ext.lower()
            if not ext:
                ext = ".png"
                
            # 统一走标准化流程
            return self._standardize_and_save(data_bytes, ext)
        except Exception as e:
            print(f"[ERROR] 读取文件失败: {e}")
            return None, False

    def delete_image(self, filepath):
        """
        从本地删除指定的图片文件，并从所有虚拟分类中移除记录
        :param filepath: 文件的绝对路径
        :return: bool 是否成功删除
        """
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                
                # 清理分类中的残留记录
                categories = self.get_all_categories()
                changed = False
                for cat_name, paths in categories.items():
                    if filepath in paths:
                        paths.remove(filepath)
                        changed = True
                if changed:
                    self.save_categories(categories)
                    
                # 清理关键词残留记录
                metadata = self.get_all_metadata()
                if filepath in metadata:
                    del metadata[filepath]
                    self.save_metadata(metadata)
                    
                # 清理哈希缓存
                filename = self._to_filename(filepath)
                hash_to_remove = None
                for h, f in self._hashes_cache.items():
                    if f == filename:
                        hash_to_remove = h
                        break
                if hash_to_remove:
                    del self._hashes_cache[hash_to_remove]
                    self._save_hashes()
                    
                self._images_dirty = True
                return True
        except Exception as e:
            print(f"删除图片失败: {e}")
        return False
