import os
from datetime import datetime
import shutil
import json
import hashlib
import io
import sqlite3
from PIL import Image


class StorageService:
    SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.webm')

    def __init__(self):
        import sys
        # 确保数据目录在项目根目录下的 data/images
        if getattr(sys, 'frozen', False):
            # 打包后，数据目录直接放在 exe 所在目录（即 bin 目录）下
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        
        self.data_dir = os.path.join(self.base_dir, "data")
        self.images_dir = os.path.join(self.data_dir, "images")
        self.inbox_dir = os.path.join(self.data_dir, "inbox")
        self.inbox_failed_dir = os.path.join(self.inbox_dir, "failed")
        
        # JSON 路径 (用于 JSON 备份/双写双读)
        self.order_file = os.path.join(self.data_dir, "order.json")
        self.categories_file = os.path.join(self.data_dir, "categories.json")
        self.metadata_file = os.path.join(self.data_dir, "metadata.json")
        self.icons_file = os.path.join(self.data_dir, "category_icons.json")
        self.hashes_file = os.path.join(self.data_dir, "hashes.json")
        self.recent_file = os.path.join(self.data_dir, "recent.json")

        # SQLite DB 数据库文件路径 (独立模块化 DB)
        self.features_db_path = os.path.join(self.data_dir, "features.db")
        self.metadata_db_path = os.path.join(self.data_dir, "metadata.db")
        self.categories_db_path = os.path.join(self.data_dir, "categories.db")
        self.order_db_path = os.path.join(self.data_dir, "order.db")
        self.recent_db_path = os.path.join(self.data_dir, "recent.db")
        
        # 自动创建必要目录
        for d in [self.data_dir, self.images_dir, self.inbox_dir, self.inbox_failed_dir]:
            if not os.path.exists(d):
                os.makedirs(d)

        # 初始化与确保 DB 表结构存在
        self._ensure_db_tables()

        self._hashes_cache = self._load_hashes()
        
        # 内存缓存与脏标记
        self._images_cache = []
        self._images_dirty = True
        
        self._categories_cache = {}
        self._image_to_categories_cache = {}
        self._categories_dirty = True
        
        self._metadata_cache = {}
        self._metadata_dirty = True
        
        self._recent_cache = None

    def _ensure_db_tables(self):
        """确保各模块 SQLite 数据库表结构健全"""
        try:
            # 1. features.db
            with sqlite3.connect(self.features_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS image_features (
                        image_path TEXT PRIMARY KEY,
                        md5 TEXT,
                        dhash TEXT,
                        phash TEXT,
                        quality_score REAL DEFAULT 0.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()

            # 2. metadata.db
            with sqlite3.connect(self.metadata_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS image_metadata (
                        image_path TEXT PRIMARY KEY,
                        tags TEXT,
                        keywords TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()

            # 3. categories.db
            with sqlite3.connect(self.categories_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS categories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        icon_path TEXT,
                        sort_order INTEGER DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS category_images (
                        category_name TEXT,
                        image_path TEXT,
                        PRIMARY KEY (category_name, image_path)
                    )
                """)
                conn.commit()

            # 4. order.db
            with sqlite3.connect(self.order_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS item_orders (
                        image_path TEXT PRIMARY KEY,
                        sort_order INTEGER NOT NULL
                    )
                """)
                conn.commit()

            # 5. recent.db
            with sqlite3.connect(self.recent_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS recent_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        image_path TEXT NOT NULL,
                        used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
        except Exception as e:
            print(f"[ERROR] 初始化 DB 表结构失败: {e}")

    # ==========================
    # 哈希缓存 (Hashes) - DB 与 JSON 双写双读
    # ==========================

    def _load_hashes(self):
        hashes = {}
        # 1. 先从 DB (features.db) 读取
        if os.path.exists(self.features_db_path):
            try:
                with sqlite3.connect(self.features_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT image_path, md5 FROM image_features WHERE md5 IS NOT NULL")
                    for row in cursor.fetchall():
                        img_path, md5_val = row[0], row[1]
                        if md5_val:
                            hashes[md5_val] = img_path
            except Exception as e:
                print(f"[WARNING] 从 features.db 读取哈希失败: {e}")

        # 2. 从 JSON (hashes.json) 读取补充
        if os.path.exists(self.hashes_file):
            try:
                with open(self.hashes_file, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                    if isinstance(json_data, dict):
                        for k, v in json_data.items():
                            if isinstance(v, str):
                                if len(k) == 32 and all(c in '0123456789abcdefABCDEF' for c in k):
                                    hashes[k] = v
                                else:
                                    hashes[v] = k
            except Exception as e:
                print(f"[WARNING] 从 hashes.json 读取哈希失败: {e}")

        return hashes

    def _save_hashes(self):
        # 1. 保存到 JSON
        try:
            with open(self.hashes_file, 'w', encoding='utf-8') as f:
                json.dump(self._hashes_cache, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存 hashes.json 失败: {e}")

        # 2. 保存到 SQLite (features.db)
        try:
            records = []
            for h_val, filename in self._hashes_cache.items():
                records.append((os.path.basename(filename), h_val))
            if records:
                with sqlite3.connect(self.features_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executemany("""
                        INSERT INTO image_features (image_path, md5)
                        VALUES (?, ?)
                        ON CONFLICT(image_path) DO UPDATE SET md5 = excluded.md5
                    """, records)
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 保存到 features.db 失败: {e}")

    def _calculate_pixel_hash(self, img):
        """计算图片纯像素数据的 MD5 哈希值，用于精准去重"""
        try:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            return hashlib.md5(img.tobytes()).hexdigest()
        except Exception as e:
            print(f"[WARNING] 计算像素哈希失败: {e}")
            return None

    def _calculate_bytes_hash(self, data_bytes):
        """计算二进制数据的 MD5 哈希值"""
        return hashlib.md5(data_bytes).hexdigest()

    def _to_filename(self, filepath):
        """将绝对路径转换为单纯的文件名"""
        return os.path.basename(filepath)

    def _to_abspath(self, filename):
        """将文件名转换为绝对路径"""
        if os.path.isabs(filename):
            return os.path.normcase(os.path.abspath(filename))
        return os.path.normcase(os.path.abspath(os.path.join(self.images_dir, filename)))

    # ==========================
    # 所有图片与排序 (Order) - DB 与 JSON 双写双读
    # ==========================

    def get_all_images(self):
        """扫描并返回所有保存的图片绝对路径列表（支持自定义排序）"""
        if not self._images_dirty:
            return self._images_cache
            
        if not os.path.exists(self.images_dir):
            self._images_cache = []
            self._images_dirty = False
            return self._images_cache
            
        actual_filenames = []
        for filename in os.listdir(self.images_dir):
            if filename.lower().endswith(self.SUPPORTED_FORMATS):
                actual_filenames.append(filename)
        
        actual_filenames.sort(reverse=True)
        
        saved_filenames = []

        # 1. 尝试从 DB (order.db) 读取排序
        if os.path.exists(self.order_db_path):
            try:
                with sqlite3.connect(self.order_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT image_path FROM item_orders ORDER BY sort_order ASC")
                    rows = cursor.fetchall()
                    if rows:
                        saved_filenames = [self._to_filename(r[0]) for r in rows]
            except Exception as e:
                print(f"[WARNING] 从 order.db 读取失败: {e}")

        # 2. 如果 DB 为空，尝试从 order.json 读取
        if not saved_filenames and os.path.exists(self.order_file):
            try:
                with open(self.order_file, 'r', encoding='utf-8') as f:
                    saved_order = json.load(f)
                    saved_filenames = [self._to_filename(p) for p in saved_order]
            except Exception as e:
                print(f"[WARNING] 从 order.json 读取失败: {e}")

        if saved_filenames:
            actual_set = set(actual_filenames)
            saved_set = set(saved_filenames)
            
            valid_saved_filenames = [f for f in saved_filenames if f in actual_set]
            new_filenames = [f for f in actual_filenames if f not in saved_set]
            
            ordered_filenames = new_filenames + valid_saved_filenames
            self._images_cache = [self._to_abspath(f) for f in ordered_filenames]
            self._images_dirty = False
            return self._images_cache
                
        self._images_cache = [self._to_abspath(f) for f in actual_filenames]
        self._images_dirty = False
        return self._images_cache

    def move_image_to_front(self, filepath, target_category=None):
        """将指定图片在主排序（全部表情）及所有其存在的分类夹（以及目标分类）中提升到最前面"""
        abs_path = self._to_abspath(filepath)

        # 1. 提升在主排序（全部表情）中的位置
        all_images = list(self.get_all_images())
        if abs_path in all_images:
            all_images.remove(abs_path)
            all_images.insert(0, abs_path)
            self.save_order(all_images)

        # 2. 若指定了目标分类，确保包含该图片
        if target_category and target_category not in ("全部表情", "未分类"):
            self.add_image_to_category(abs_path, target_category)

        # 3. 提升在其关联的所有分类夹中的位置
        categories = self.get_all_categories()
        changed = False
        for cat_name, paths in categories.items():
            if abs_path in paths:
                if abs_path in paths:
                    paths.remove(abs_path)
                paths.insert(0, abs_path)
                changed = True
        if changed:
            self.save_categories(categories)

    def save_order(self, filepaths):
        """保存用户自定义的表情包排序顺序（只保存文件名）- DB 与 JSON 双写"""
        filenames = [self._to_filename(p) for p in filepaths]
        
        # 1. 写 JSON
        try:
            with open(self.order_file, 'w', encoding='utf-8') as f:
                json.dump(filenames, f, indent=4, ensure_ascii=False)
            self._images_dirty = True
        except Exception as e:
            print(f"[ERROR] 保存 order.json 失败: {e}")

        # 2. 写 order.db
        try:
            records = [(fname, idx) for idx, fname in enumerate(filenames)]
            with sqlite3.connect(self.order_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM item_orders")
                cursor.executemany("""
                    INSERT INTO item_orders (image_path, sort_order)
                    VALUES (?, ?)
                """, records)
                conn.commit()
            self._images_dirty = True
        except Exception as e:
            print(f"[ERROR] 保存 order.db 失败: {e}")

    # ==========================
    # 分类 (Categories) - DB 与 JSON 双写双读
    # ==========================
    
    def get_all_categories(self):
        """获取所有分类及其包含的图片绝对路径列表"""
        if not self._categories_dirty:
            return self._categories_cache
            
        data = {}

        # 1. 尝试从 categories.db 读取
        if os.path.exists(self.categories_db_path):
            try:
                with sqlite3.connect(self.categories_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM categories ORDER BY sort_order ASC, id ASC")
                    cat_rows = cursor.fetchall()

                    for r in cat_rows:
                        cat_name = r[0]
                        cursor.execute("SELECT image_path FROM category_images WHERE category_name = ?", (cat_name,))
                        img_rows = cursor.fetchall()
                        data[cat_name] = [img_r[0] for img_r in img_rows]
            except Exception as e:
                print(f"[WARNING] 从 categories.db 读取失败: {e}")

        # 2. 如果 DB 无数据且 JSON 存在，补充合并 categories.json
        if not data and os.path.exists(self.categories_file):
            try:
                with open(self.categories_file, 'r', encoding='utf-8') as f:
                    json_cat = json.load(f)
                    if isinstance(json_cat, dict):
                        data.update(json_cat)
            except Exception as e:
                print(f"[WARNING] 从 categories.json 读取失败: {e}")

        try:
            all_real_images = set(self.get_all_images())
            cleaned_data = {}
            reverse_map = {}
            
            for category, paths in data.items():
                abs_paths = [self._to_abspath(p) for p in paths]
                valid_paths = [p for p in abs_paths if p in all_real_images]
                cleaned_data[category] = valid_paths
                
                for p in valid_paths:
                    if p not in reverse_map:
                        reverse_map[p] = []
                    reverse_map[p].append(category)
                    
            self._categories_cache = cleaned_data
            self._image_to_categories_cache = reverse_map
            self._categories_dirty = False
            return self._categories_cache
        except Exception as e:
            print(f"[ERROR] StorageService.get_all_categories 失败: {e}")
            self._categories_cache = {}
            self._image_to_categories_cache = {}
            self._categories_dirty = False
            return self._categories_cache

    def save_categories(self, categories_data):
        """保存分类数据 - DB 与 JSON 双写"""
        portable_data = {}
        for category, paths in categories_data.items():
            portable_data[category] = [self._to_filename(p) for p in paths]
            
        # 1. 写 categories.json
        try:
            with open(self.categories_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
            self._categories_dirty = True
        except Exception as e:
            print(f"[ERROR] StorageService.save_categories JSON 失败: {e}")

        # 2. 写 categories.db
        try:
            with sqlite3.connect(self.categories_db_path) as conn:
                cursor = conn.cursor()
                for idx, (cat_name, filenames) in enumerate(portable_data.items()):
                    cursor.execute("""
                        INSERT INTO categories (name, sort_order)
                        VALUES (?, ?)
                        ON CONFLICT(name) DO UPDATE SET sort_order = excluded.sort_order
                    """, (cat_name, idx))

                    cursor.execute("DELETE FROM category_images WHERE category_name = ?", (cat_name,))
                    if filenames:
                        rel_records = [(cat_name, fname) for fname in filenames]
                        cursor.executemany("""
                            INSERT OR IGNORE INTO category_images (category_name, image_path)
                            VALUES (?, ?)
                        """, rel_records)
                conn.commit()
            self._categories_dirty = True
        except Exception as e:
            print(f"[ERROR] StorageService.save_categories DB 失败: {e}")

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
            
            icons = self.get_all_category_icons()
            if category_name in icons:
                del icons[category_name]
                self.save_category_icons(icons)

            # 同步删 categories.db 中的记录
            try:
                with sqlite3.connect(self.categories_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM categories WHERE name = ?", (category_name,))
                    cursor.execute("DELETE FROM category_images WHERE category_name = ?", (category_name,))
                    conn.commit()
            except Exception:
                pass

            return True
        return False

    def add_image_to_category(self, filepath, category_name):
        """将图片添加到指定分类"""
        categories = self.get_all_categories()
        if category_name not in categories:
            categories[category_name] = []
            
        abs_filepath = self._to_abspath(filepath)
        if abs_filepath not in categories[category_name]:
            categories[category_name].append(abs_filepath)
            try:
                self.save_categories(categories)
                return "success"
            except Exception:
                return "error"
        return "already_exists"

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
        paths = categories.get(category_name, [])
        paths_set = set(paths)
        result = [p for p in all_ordered if p in paths_set]
        return result

    def get_categories_by_image(self, filepath):
        """反向查询：获取指定图片所属的所有分类名称列表"""
        self.get_all_categories()
        return self._image_to_categories_cache.get(filepath, [])

    def get_image_to_categories_map(self):
        """获取图片到分类的反向映射字典"""
        self.get_all_categories()
        return self._image_to_categories_cache

    def is_animated(self, filepath):
        """判断图片是否为动图格式"""
        ext = os.path.splitext(filepath)[1].lower()
        return ext in ['.gif', '.webp']

    # ==========================
    # 最近使用 (Recent) - DB 与 JSON 双写双读
    # ==========================
    
    def get_recent_images(self):
        """获取最近使用的表情列表（绝对路径）"""
        if self._recent_cache is not None:
            return self._recent_cache
            
        recent_filenames = []

        # 1. 尝试从 recent.db 读取
        if os.path.exists(self.recent_db_path):
            try:
                with sqlite3.connect(self.recent_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT image_path FROM recent_history ORDER BY id DESC LIMIT 50")
                    rows = cursor.fetchall()
                    recent_filenames = [r[0] for r in rows]
            except Exception as e:
                print(f"[WARNING] 从 recent.db 读取失败: {e}")

        # 2. 如果 DB 无数据且 recent.json 存在，从 JSON 补充
        if not recent_filenames and os.path.exists(self.recent_file):
            try:
                with open(self.recent_file, 'r', encoding='utf-8') as f:
                    recent_filenames = json.load(f)
            except Exception as e:
                print(f"[ERROR] 读取 recent.json 失败: {e}")
            
        all_images = set(self.get_all_images())
        valid_paths = []
        for fname in recent_filenames:
            abs_path = self._to_abspath(fname)
            if abs_path in all_images and abs_path not in valid_paths:
                valid_paths.append(abs_path)
                
        self._recent_cache = valid_paths
        return self._recent_cache
            
    def add_recent_image(self, filepath, limit=30):
        """添加一条最近使用记录（LRU机制）- DB 与 JSON 双写"""
        recent_paths = self.get_recent_images()
        abs_path = self._to_abspath(filepath)
        
        if abs_path in recent_paths:
            recent_paths.remove(abs_path)
            
        recent_paths.insert(0, abs_path)
        if len(recent_paths) > limit:
            recent_paths = recent_paths[:limit]
            
        self._recent_cache = recent_paths
        rel_filename = self._to_filename(filepath)

        # 1. 保存 JSON
        try:
            filenames = [self._to_filename(p) for p in recent_paths]
            with open(self.recent_file, 'w', encoding='utf-8') as f:
                json.dump(filenames, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存 recent.json 失败: {e}")

        # 2. 保存 DB
        try:
            with sqlite3.connect(self.recent_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO recent_history (image_path) VALUES (?)", (rel_filename,))
                conn.commit()
        except Exception as e:
            print(f"[ERROR] 保存 recent.db 失败: {e}")

    # ==========================
    # 分类图标 (Category Icons) - DB 与 JSON 双写双读
    # ==========================
    
    def get_all_category_icons(self):
        icons = {}

        # 1. 尝试从 categories.db 读取 icon_path
        if os.path.exists(self.categories_db_path):
            try:
                with sqlite3.connect(self.categories_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name, icon_path FROM categories WHERE icon_path IS NOT NULL AND icon_path != ''")
                    for row in cursor.fetchall():
                        cat_name, icon_p = row[0], row[1]
                        if any(icon_p.lower().endswith(ext) for ext in self.SUPPORTED_FORMATS):
                            icons[cat_name] = self._to_abspath(icon_p)
                        else:
                            icons[cat_name] = icon_p
            except Exception as e:
                print(f"[WARNING] 从 categories.db 读取图标失败: {e}")

        # 2. 从 category_icons.json 补充
        if os.path.exists(self.icons_file):
            try:
                with open(self.icons_file, 'r', encoding='utf-8') as f:
                    json_icons = json.load(f)
                    for cat, val in json_icons.items():
                        if cat not in icons:
                            if any(val.lower().endswith(ext) for ext in self.SUPPORTED_FORMATS):
                                icons[cat] = self._to_abspath(val)
                            else:
                                icons[cat] = val
            except Exception:
                pass

        return icons
            
    def save_category_icons(self, icons_data):
        """保存分类图标数据 - DB 与 JSON 双写"""
        portable_data = {}
        for cat, val in icons_data.items():
            if any(val.lower().endswith(ext) for ext in self.SUPPORTED_FORMATS):
                portable_data[cat] = self._to_filename(val)
            else:
                portable_data[cat] = val

        # 1. 写 JSON
        try:
            with open(self.icons_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] StorageService.save_category_icons JSON 失败: {e}")

        # 2. 写 categories.db
        try:
            with sqlite3.connect(self.categories_db_path) as conn:
                cursor = conn.cursor()
                for cat_name, icon_p in portable_data.items():
                    cursor.execute("""
                        INSERT INTO categories (name, icon_path)
                        VALUES (?, ?)
                        ON CONFLICT(name) DO UPDATE SET icon_path = excluded.icon_path
                    """, (cat_name, icon_p))
                conn.commit()
        except Exception as e:
            print(f"[ERROR] StorageService.save_category_icons DB 失败: {e}")

    def set_category_icon(self, category_name, filepath):
        icons = self.get_all_category_icons()
        icons[category_name] = filepath
        self.save_category_icons(icons)
        
    def get_category_icon(self, category_name):
        icons = self.get_all_category_icons()
        return icons.get(category_name, None)

    # ==========================
    # 关键词元数据 (Metadata) - DB 与 JSON 双写双读
    # ==========================
    
    @staticmethod
    def parse_tags(tags_str):
        if not tags_str: return []
        tags = []
        for t in tags_str.split(' '):
            if t and t not in tags:
                tags.append(t)
        return tags

    @staticmethod
    def serialize_tags(tags_list):
        return " ".join(tags_list)

    @staticmethod
    def merge_tags(existing_tags_str, new_tags_str):
        existing_list = StorageService.parse_tags(existing_tags_str)
        new_list = StorageService.parse_tags(new_tags_str)
        for tag in new_list:
            if tag not in existing_list:
                existing_list.append(tag)
        return StorageService.serialize_tags(existing_list)

    @staticmethod
    def remove_tags(existing_tags_str, remove_tags_str):
        existing_list = StorageService.parse_tags(existing_tags_str)
        remove_list = StorageService.parse_tags(remove_tags_str)
        final_list = [tag for tag in existing_list if tag not in remove_list]
        return StorageService.serialize_tags(final_list)

    def get_all_metadata(self):
        """获取所有图片的关键词元数据，并映射回绝对路径 - DB 与 JSON 混合读取"""
        if not self._metadata_dirty:
            return self._metadata_cache
            
        data = {}

        # 1. 尝试从 metadata.db 读取
        if os.path.exists(self.metadata_db_path):
            try:
                with sqlite3.connect(self.metadata_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT image_path, keywords FROM image_metadata")
                    for row in cursor.fetchall():
                        data[row[0]] = row[1] or ""
            except Exception as e:
                print(f"[WARNING] 从 metadata.db 读取失败: {e}")

        # 2. 从 metadata.json 补充
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    json_meta = json.load(f)
                    if isinstance(json_meta, dict):
                        for k, v in json_meta.items():
                            if k not in data:
                                data[k] = v if isinstance(v, str) else str(v)
            except Exception as e:
                print(f"[WARNING] 从 metadata.json 读取失败: {e}")

        self._metadata_cache = {self._to_abspath(k): v for k, v in data.items()}
        self._metadata_dirty = False
        return self._metadata_cache

    def save_metadata(self, metadata):
        """保存关键词元数据 - DB 与 JSON 双写"""
        portable_data = {self._to_filename(k): str(v) for k, v in metadata.items()}

        # 1. 写 JSON
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(portable_data, f, indent=4, ensure_ascii=False)
            self._metadata_dirty = True
        except Exception as e:
            print(f"[ERROR] StorageService.save_metadata JSON 失败: {e}")

        # 2. 写 metadata.db
        try:
            records = [(fname, kw) for fname, kw in portable_data.items()]
            with sqlite3.connect(self.metadata_db_path) as conn:
                cursor = conn.cursor()
                cursor.executemany("""
                    INSERT INTO image_metadata (image_path, keywords)
                    VALUES (?, ?)
                    ON CONFLICT(image_path) DO UPDATE SET keywords = excluded.keywords
                """, records)
                conn.commit()
            self._metadata_dirty = True
        except Exception as e:
            print(f"[ERROR] StorageService.save_metadata DB 失败: {e}")

    def get_image_keywords(self, filepath):
        metadata = self.get_all_metadata()
        return metadata.get(self._to_abspath(filepath), "")

    def set_image_keywords(self, filepath, keywords_str):
        metadata = self.get_all_metadata()
        metadata[self._to_abspath(filepath)] = keywords_str
        self.save_metadata(metadata)

    def search_images(self, keyword, category_name="全部表情"):
        images = self.get_images_by_category(category_name)
        if not keyword or not keyword.strip():
            return images
            
        keyword = keyword.strip().lower()
        metadata = self.get_all_metadata()
        result = []
        for img in images:
            img_kw = metadata.get(img, "").lower()
            if keyword in img_kw:
                result.append(img)
        return result

    def generate_new_filename(self, extension=".png"):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"{timestamp}{extension}"

    def _standardize_and_save(self, data_bytes, original_ext):
        try:
            img = Image.open(io.BytesIO(data_bytes))
            is_animated = getattr(img, "is_animated", False)
            
            if is_animated:
                file_hash = self._calculate_bytes_hash(data_bytes)
                final_bytes = data_bytes
                final_ext = '.gif'
            else:
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                    
                file_hash = self._calculate_pixel_hash(img)
                
                clean_img = Image.new('RGBA', img.size)
                clean_img.paste(img, (0, 0))
                
                output_io = io.BytesIO()
                clean_img.save(output_io, format="PNG", optimize=True)
                final_bytes = output_io.getvalue()
                final_ext = '.png'
                
            if file_hash and file_hash in self._hashes_cache:
                existing_filename = self._hashes_cache[file_hash]
                existing_path = self._to_abspath(existing_filename)
                if os.path.exists(existing_path):
                    self.move_image_to_front(existing_path)
                    return existing_path, True
                else:
                    del self._hashes_cache[file_hash]
                    
            filename = self.generate_new_filename(final_ext)
            filepath = os.path.join(self.images_dir, filename)
            
            with open(filepath, 'wb') as f:
                f.write(final_bytes)
                
            if file_hash:
                self._hashes_cache[file_hash] = filename
                self._save_hashes()
                
            self._images_dirty = True
            return self._to_abspath(filename), False
            
        except Exception as e:
            print(f"[ERROR] 图片标准化保存失败: {e}")
            return None, False

    def save_image(self, qimage):
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice
        
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        qimage.save(buffer, "PNG")
        image_bytes = byte_array.data()
        
        return self._standardize_and_save(image_bytes, ".png")

    def save_file(self, source_path):
        if not os.path.exists(source_path):
            return None, False
            
        try:
            with open(source_path, 'rb') as f:
                data_bytes = f.read()
                
            _, ext = os.path.splitext(source_path)
            ext = ext.lower()
            if not ext:
                ext = ".png"
                
            return self._standardize_and_save(data_bytes, ext)
        except Exception as e:
            print(f"[ERROR] 读取文件失败: {e}")
            return None, False

    def delete_image(self, filepath):
        """从本地删除指定的图片文件，并同步清理分类、元数据、哈希、最近使用（DB+JSON双写双删）"""
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
                
                # 清理最近使用记录
                if self._recent_cache is not None and filepath in self._recent_cache:
                    self._recent_cache.remove(filepath)
                    try:
                        filenames = [self._to_filename(p) for p in self._recent_cache]
                        with open(self.recent_file, 'w', encoding='utf-8') as f:
                            json.dump(filenames, f, indent=4, ensure_ascii=False)
                    except Exception:
                        pass
                
                for h, f in self._hashes_cache.items():
                    if f == filename:
                        hash_to_remove = h
                        break
                if hash_to_remove:
                    del self._hashes_cache[hash_to_remove]
                    self._save_hashes()

                # 从各 DB 中安全彻底清理该文件记录
                try:
                    with sqlite3.connect(self.features_db_path) as conn:
                        conn.execute("DELETE FROM image_features WHERE image_path = ?", (filename,))
                    with sqlite3.connect(self.metadata_db_path) as conn:
                        conn.execute("DELETE FROM image_metadata WHERE image_path = ?", (filename,))
                    with sqlite3.connect(self.categories_db_path) as conn:
                        conn.execute("DELETE FROM category_images WHERE image_path = ?", (filename,))
                    with sqlite3.connect(self.order_db_path) as conn:
                        conn.execute("DELETE FROM item_orders WHERE image_path = ?", (filename,))
                    with sqlite3.connect(self.recent_db_path) as conn:
                        conn.execute("DELETE FROM recent_history WHERE image_path = ?", (filename,))
                except Exception as e:
                    print(f"[WARNING] 数据库删除图记录提示: {e}")

                self._images_dirty = True
                return True
        except Exception as e:
            print(f"删除图片失败: {e}")
        return False
