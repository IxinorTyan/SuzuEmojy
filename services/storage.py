import os
from datetime import datetime
import shutil
import json
import hashlib

class StorageService:
    def __init__(self):
        import sys
        # 确保数据目录在项目根目录下的 data/images
        if getattr(sys, 'frozen', False):
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

    def _calculate_file_hash(self, filepath):
        """计算文件的 MD5 哈希值"""
        hash_md5 = hashlib.md5()
        try:
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return None

    def _calculate_bytes_hash(self, data_bytes):
        """计算二进制数据的 MD5 哈希值"""
        return hashlib.md5(data_bytes).hexdigest()

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
        if not os.path.exists(self.images_dir):
            return []
            
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
                
                # 构建一个新的排序列表：
                # 1. 过滤掉 JSON 中有但实际硬盘上已经不存在的文件名
                valid_saved_filenames = [f for f in saved_filenames if f in actual_filenames]
                
                # 2. 找出实际硬盘上有，但 JSON 里没记录的新文件（这些排在最前面）
                new_filenames = [f for f in actual_filenames if f not in valid_saved_filenames]
                
                ordered_filenames = new_filenames + valid_saved_filenames
                return [self._to_abspath(f) for f in ordered_filenames]
            except Exception as e:
                print(f"读取排序配置失败: {e}")
                
        return [self._to_abspath(f) for f in actual_filenames]

    def save_order(self, filepaths):
        """保存用户自定义的表情包排序顺序（只保存文件名）"""
        filenames = [self._to_filename(p) for p in filepaths]
        try:
            with open(self.order_file, 'w', encoding='utf-8') as f:
                json.dump(filenames, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存排序失败: {e}")

    # ==========================
    # 分类 (虚拟文件夹) 相关逻辑
    # ==========================
    
    def get_all_categories(self):
        """获取所有分类及其包含的图片绝对路径列表"""
        if not os.path.exists(self.categories_file):
            return {}
        try:
            with open(self.categories_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # 数据迁移和清理：兼容旧版绝对路径，只向外暴露存在的绝对路径
                all_real_images = set(self.get_all_images()) # get_all_images 现在返回规范的绝对路径
                cleaned_data = {}
                for category, paths in data.items():
                    # 把存着的文件名（或旧绝对路径）全部转为当前的绝对路径
                    abs_paths = [self._to_abspath(p) for p in paths]
                    cleaned_data[category] = [p for p in abs_paths if p in all_real_images]
                return cleaned_data
        except Exception as e:
            print(f"[ERROR] StorageService.get_all_categories: 读取分类失败 {e}")
            return {}

    def save_categories(self, categories_data):
        """保存分类数据，内部将绝对路径全部转换为文件名以确保可移植性"""
        # categories_data 传入的是 {category: [abs_path1, abs_path2...]}
        portable_data = {}
        for category, paths in categories_data.items():
            portable_data[category] = [self._to_filename(p) for p in paths]
            
        try:
            with open(self.categories_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
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
            
        if filepath not in categories[category_name]:
            categories[category_name].append(filepath)
            self.save_categories(categories)
            return True
        return False

    def remove_image_from_category(self, filepath, category_name):
        """将图片从指定分类移除"""
        categories = self.get_all_categories()
        if category_name in categories and filepath in categories[category_name]:
            categories[category_name].remove(filepath)
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
        # 为了保证显示顺序跟 "全部" 中一致，我们基于所有图片的顺序来进行过滤
        result = [p for p in all_ordered if p in paths]
        return result

    def get_categories_by_image(self, filepath):
        """反向查询：获取指定图片所属的所有分类名称列表"""
        categories = self.get_all_categories()
        belong_to = []
        for cat_name, paths in categories.items():
            if filepath in paths:
                belong_to.append(cat_name)
        return belong_to

    # ==========================
    # 分类图标 (Category Icons) 相关逻辑
    # ==========================
    
    def get_all_category_icons(self):
        if not os.path.exists(self.icons_file):
            return {}
        try:
            with open(self.icons_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
            
    def save_category_icons(self, icons_data):
        try:
            with open(self.icons_file, 'w', encoding='utf-8') as f:
                json.dump(icons_data, f, indent=4, ensure_ascii=False)
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
        if not os.path.exists(self.metadata_file):
            return {}
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 将存入的文件名转回绝对路径
                return {self._to_abspath(k): v for k, v in data.items()}
        except Exception as e:
            print(f"[ERROR] StorageService.get_all_metadata: 读取元数据失败 {e}")
            return {}

    def save_metadata(self, metadata):
        """保存关键词元数据，内部将绝对路径转换为文件名"""
        portable_data = {self._to_filename(k): v for k, v in metadata.items()}
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
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

    def _detect_real_extension(self, header_bytes, default_ext):
        """根据文件头（魔数）探测真实的文件格式"""
        if header_bytes.startswith(b'GIF'):
            return '.gif'
        elif header_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
            return '.png'
        elif header_bytes.startswith(b'\xff\xd8\xff'):
            return '.jpg'
        elif header_bytes.startswith(b'RIFF') and len(header_bytes) >= 12 and header_bytes[8:12] == b'WEBP':
            return '.webp'
        return default_ext

    def generate_new_filename(self, extension=".png"):
        """生成基于时间戳的新文件名"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"{timestamp}{extension}"

    def save_image(self, qimage):
        """
        保存 QImage 到本地
        :param qimage: PySide6.QtGui.QImage 对象
        :return: (保存后的绝对路径, 是否是已存在的重复图片)
        """
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice
        
        # 先将 QImage 转换为字节流以计算哈希
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        qimage.save(buffer, "PNG")
        image_bytes = byte_array.data()
        
        file_hash = self._calculate_bytes_hash(image_bytes)
        
        # 查重
        if file_hash in self._hashes_cache:
            existing_filename = self._hashes_cache[file_hash]
            existing_path = self._to_abspath(existing_filename)
            if os.path.exists(existing_path):
                return existing_path, True
            else:
                # 文件已丢失，清理无效哈希
                del self._hashes_cache[file_hash]

        filename = self.generate_new_filename()
        filepath = os.path.join(self.images_dir, filename)
        
        try:
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            self._hashes_cache[file_hash] = filename
            self._save_hashes()
            return filepath, False
        except Exception as e:
            print(f"保存图片失败: {e}")
            return None, False

    def save_file(self, source_path):
        """
        直接复制原始文件到存储目录，并通过魔数自动纠正扩展名
        :param source_path: 原始文件路径
        :return: (保存后的绝对路径, 是否是已存在的重复图片)
        """
        if not os.path.exists(source_path):
            return None, False
            
        # 计算哈希查重
        file_hash = self._calculate_file_hash(source_path)
        if file_hash and file_hash in self._hashes_cache:
            existing_filename = self._hashes_cache[file_hash]
            existing_path = self._to_abspath(existing_filename)
            if os.path.exists(existing_path):
                return existing_path, True
            else:
                del self._hashes_cache[file_hash]
            
        # 获取原始扩展名
        _, ext = os.path.splitext(source_path)
        if not ext:
            ext = ".png" # 如果没有扩展名，默认当作 png
            
        # 读取文件头 12 个字节进行真实格式探测，防范 QQ 等软件的“扩展名欺骗”
        try:
            with open(source_path, 'rb') as f:
                header = f.read(12)
            ext = self._detect_real_extension(header, ext)
        except Exception as e:
            print(f"[WARNING] 探测真实文件格式失败: {e}")
            
        filename = self.generate_new_filename(ext)
        filepath = os.path.join(self.images_dir, filename)
        
        try:
            shutil.copy2(source_path, filepath)
            if file_hash:
                self._hashes_cache[file_hash] = filename
                self._save_hashes()
            return filepath, False
        except Exception as e:
            print(f"复制文件失败: {e}")
            return None, False

    def save_downloaded_data(self, url, content):
        """
        保存从网络下载的二进制数据，尝试从 URL 推断扩展名
        :return: (保存后的绝对路径, 是否是已存在的重复图片)
        """
        import urllib.parse
        
        file_hash = self._calculate_bytes_hash(content)
        if file_hash in self._hashes_cache:
            existing_filename = self._hashes_cache[file_hash]
            existing_path = self._to_abspath(existing_filename)
            if os.path.exists(existing_path):
                return existing_path, True
            else:
                del self._hashes_cache[file_hash]
        
        # 尝试从 URL 提取正确的后缀
        parsed_url = urllib.parse.urlparse(url)
        path = parsed_url.path
        _, ext = os.path.splitext(path)
        
        # 如果获取不到或者是非常见后缀，给个默认值（也可以根据 header 决定，但这里从简）
        if ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
            if 'gif' in url.lower():
                ext = '.gif'
            elif 'webp' in url.lower():
                ext = '.webp'
            else:
                ext = '.png'
                
        # 通过二进制数据头再次核实和纠正扩展名
        ext = self._detect_real_extension(content[:12], ext)
                
        filename = self.generate_new_filename(ext)
        filepath = os.path.join(self.images_dir, filename)
        
        try:
            with open(filepath, 'wb') as f:
                f.write(content)
            self._hashes_cache[file_hash] = filename
            self._save_hashes()
            return filepath, False
        except Exception as e:
            print(f"保存下载数据失败: {e}")
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
                    
                return True
        except Exception as e:
            print(f"删除图片失败: {e}")
        return False
