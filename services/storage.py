import os
import uuid
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
        import threading
        self.lock = threading.RLock()
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
        self._sync_key_index = None

        self._repair_orphaned_resources()

    def _get_sync_key_index(self):
        if self._sync_key_index is not None:
            return self._sync_key_index
        
        self._sync_key_index = {}
        if os.path.exists(self.images_dir):
            from services.hasher import compute_sync_key
            filenames = sorted(os.listdir(self.images_dir))
            for filename in filenames:
                if filename.lower().endswith(self.SUPPORTED_FORMATS):
                    abspath = self._to_abspath(filename)
                    skey = compute_sync_key(abspath)
                    if skey:
                        self._sync_key_index.setdefault(skey, filename)
        return self._sync_key_index

    def _atomic_save_json(self, target_file, data, indent=4):
        """原子写入 JSON，防止文件写入损坏"""
        temp_file = f"{target_file}.{uuid.uuid4()}.tmp"
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, target_file)
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

    # 别名兼容
    _atomic_write_json = _atomic_save_json

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
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS recent_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        image_path TEXT UNIQUE NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # 检查旧表结构，若无 updated_at 字段则在线无缝升级
                cursor.execute("PRAGMA table_info(recent_history)")
                columns = [row[1] for row in cursor.fetchall()]
                if "updated_at" not in columns:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS recent_history_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            image_path TEXT UNIQUE NOT NULL,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    cursor.execute("""
                        INSERT OR REPLACE INTO recent_history_new (image_path, updated_at)
                        SELECT image_path, MAX(used_at) FROM recent_history GROUP BY image_path
                    """)
                    cursor.execute("DROP TABLE recent_history")
                    cursor.execute("ALTER TABLE recent_history_new RENAME TO recent_history")
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
        with self.lock:
            # 1. 保存到 JSON
            self._atomic_write_json(self.hashes_file, self._hashes_cache)

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

    def _repair_orphaned_resources(self):
        """自愈孤儿资源：检查本地图片文件是否缺失索引记录，缺失时自动补齐"""
        with self.lock:
            if not os.path.exists(self.images_dir):
                return
            actual_filenames = sorted(f for f in os.listdir(self.images_dir) if f.lower().endswith(self.SUPPORTED_FORMATS))
            if not actual_filenames:
                return

            all_saved = set(self.get_all_images())
            saved_hashes_filenames = set(os.path.basename(p) for p in self._hashes_cache.values())
            
            repaired = False
            for fname in actual_filenames:
                abspath = self._to_abspath(fname)
                if abspath not in all_saved or fname not in saved_hashes_filenames:
                    try:
                        with open(abspath, 'rb') as f:
                            data_bytes = f.read()
                        _, ext = os.path.splitext(fname)
                        # 计算哈希并补齐 _hashes_cache
                        file_hash = self._calculate_bytes_hash(data_bytes)
                        if file_hash:
                            self._hashes_cache[file_hash] = fname
                        repaired = True
                    except Exception as e:
                        print(f"[WARNING] 自愈孤儿文件 {fname} 失败: {e}")
            if repaired:
                try:
                    self._save_hashes()
                    self._images_dirty = True
                    # 重新生成并保存 order
                    all_imgs = self.get_all_images()
                    self.save_order(all_imgs)
                except Exception as e:
                    print(f"[WARNING] 保存自愈数据失败: {e}")

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
            self._atomic_write_json(self.order_file, filenames)
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
            self._atomic_write_json(self.categories_file, portable_data)
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

    def get_exportable_categories(self):
        """获取可导出的用户分类名称列表，保持当前分类排序"""
        categories = self.get_all_categories()
        return list(categories.keys())

    def add_category(self, category_name):
        """新建一个分类"""
        categories = self.get_all_categories()
        if category_name not in categories:
            categories[category_name] = []
            self.save_categories(categories)
            return True
        return False

    def rename_category(self, old_name, new_name):
        """重命名分类 (同步更新 categories.json、category_icons.json 及 SQLite 数据库事务)"""
        categories = self.get_all_categories()
        if old_name not in categories or new_name in categories:
            return False

        # 1. 更新 categories.json (如果项目使用 JSON 文件)
        if os.path.exists(self.categories_file):
            try:
                with open(self.categories_file, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                if isinstance(json_data, dict) and old_name in json_data:
                    new_json_data = {}
                    for k, v in json_data.items():
                        if k == old_name:
                            new_json_data[new_name] = v
                        else:
                            new_json_data[k] = v
                    self._atomic_write_json(self.categories_file, new_json_data)
            except Exception as e:
                print(f"[ERROR] 重命名更新 categories.json 失败: {e}")

        # 2. 更新 category_icons.json (如果项目使用 JSON 文件)
        if os.path.exists(self.icons_file):
            try:
                with open(self.icons_file, 'r', encoding='utf-8') as f:
                    icons_data = json.load(f)
                if isinstance(icons_data, dict) and old_name in icons_data:
                    icons_data[new_name] = icons_data.pop(old_name)
                    self._atomic_write_json(self.icons_file, icons_data)
            except Exception as e:
                print(f"[ERROR] 重命名更新 category_icons.json 失败: {e}")

        # 3. 在事务中更新 SQLite categories.db
        try:
            with sqlite3.connect(self.categories_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE categories SET name = ? WHERE name = ?", (new_name, old_name))
                cursor.execute("UPDATE category_images SET category_name = ? WHERE category_name = ?", (new_name, old_name))
                conn.commit()
        except Exception as e:
            print(f"[ERROR] 重命名分类更新 DB 事务失败: {e}")
            return False

        self._categories_dirty = True
        return True

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
    
    def _get_recent_limit(self):
        """动态读取用户配置中的最近使用记录数量限制（完全无硬编码）"""
        try:
            from services.config import ConfigService
            cfg = ConfigService()
            val = cfg.get("recent_limit")
            if val is not None:
                return int(val)
        except Exception:
            pass
        return 30

    def get_recent_images(self, limit=None):
        """获取最近使用的表情列表（绝对路径），完全动态绑定配置中的 limit 数量"""
        if limit is None:
            limit = self._get_recent_limit()

        if self._recent_cache is not None:
            return self._recent_cache[:limit]

        recent_filenames = []

        # 1. 尝试从 recent.db 读取（按最新使用时间降序获取 limit 条记录）
        if os.path.exists(self.recent_db_path):
            try:
                with sqlite3.connect(self.recent_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT image_path FROM recent_history ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))
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
        return self._recent_cache[:limit]

    def add_recent_image(self, filepath, limit=None):
        """添加一条最近使用记录（LRU机制）- DB 与 JSON 双写，完全动态绑定 limit 配置"""
        if limit is None:
            limit = self._get_recent_limit()

        recent_paths = self.get_recent_images(limit=limit)
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
            self._atomic_write_json(self.recent_file, filenames)
        except Exception as e:
            print(f"[ERROR] 保存 recent.json 失败: {e}")

        # 2. 保存 DB (UPSERT 刷新时间戳 + 动态限制超限数据清理)
        try:
            with sqlite3.connect(self.recent_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO recent_history (image_path, updated_at)
                    VALUES (?, CURRENT_TIMESTAMP)
                    ON CONFLICT(image_path) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                """, (rel_filename,))
                cursor.execute("""
                    DELETE FROM recent_history
                    WHERE image_path NOT IN (
                        SELECT image_path FROM recent_history ORDER BY updated_at DESC LIMIT ?
                    )
                """, (limit,))
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
            self._atomic_write_json(self.icons_file, portable_data)
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
            self._atomic_write_json(self.metadata_file, portable_data)
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

    @staticmethod
    def detect_format_magic(data_bytes: bytes) -> str:
        """
        根据文件魔数识别真实格式
        返回值: 'webm', 'webp', 'gif', 'png', 'jpeg', 'apng', 'bmp', 'tiff', 或 'unknown'
        """
        if not data_bytes or len(data_bytes) < 4:
            return "unknown"

        # WebM / Matroska 魔数: 1A 45 DF A3
        if data_bytes.startswith(b"\x1a\x45\xdf\xa3"):
            return "webm"

        # RIFF 容器 (WebP): RIFF....WEBP
        if data_bytes.startswith(b"RIFF") and len(data_bytes) >= 12 and data_bytes[8:12] == b"WEBP":
            return "webp"

        # GIF 魔数: GIF87a / GIF89a
        if data_bytes.startswith(b"GIF87a") or data_bytes.startswith(b"GIF89a"):
            return "gif"

        # PNG 魔数: 89 50 4E 47 0D 0A 1A 0A
        if data_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            # 检查是否为 APNG
            if b"acTL" in data_bytes[:1024]:
                return "apng"
            return "png"

        # JPEG 魔数: FF D8 FF
        if data_bytes.startswith(b"\xff\xd8\xff"):
            return "jpeg"

        # BMP 魔数: BM
        if data_bytes.startswith(b"BM"):
            return "bmp"

        # TIFF 魔数: II*\x00 或 MM\x00*
        if data_bytes.startswith(b"II*\x00") or data_bytes.startswith(b"MM\x00*"):
            return "tiff"

        return "unknown"

    def _convert_source_to_standard_bytes(self, data_bytes: bytes, source_path=None) -> tuple[bytes, str, bool]:
        """
        魔数识别并对动态格式进行归一化转码
        返回: (standard_bytes, format_type, is_animated)
        若转码失败则抛出异常或返回 None
        """
        fmt = self.detect_format_magic(data_bytes)

        # 1. 动态 WebM 视频 -> 转为 GIF
        if fmt == "webm":
            from services.webm_converter import is_ffmpeg_available, convert_video_to_gif
            if not is_ffmpeg_available():
                raise RuntimeError("FFmpeg 缺失或不可用，无法转换 WebM 动态贴纸")

            import tempfile
            temp_in = None
            temp_out = None
            try:
                # 写入临时文件供 FFmpeg 读取（若已有 source_path 且格式相符则直接使用，否则写入临时文件）
                if source_path and os.path.exists(source_path):
                    temp_in = source_path
                    need_clean_in = False
                else:
                    fd_in, temp_in = tempfile.mkstemp(suffix=".webm")
                    with os.fdopen(fd_in, "wb") as f:
                        f.write(data_bytes)
                    need_clean_in = True

                fd_out, temp_out = tempfile.mkstemp(suffix=".gif")
                os.close(fd_out)

                convert_video_to_gif(temp_in, temp_out)

                if not os.path.exists(temp_out) or os.path.getsize(temp_out) == 0:
                    raise RuntimeError("FFmpeg 转换输出文件无效或为空")

                with open(temp_out, "rb") as f:
                    gif_bytes = f.read()

                # 校验转换后的 GIF
                with Image.open(io.BytesIO(gif_bytes)) as test_img:
                    n_frames = getattr(test_img, "n_frames", 1)
                    if n_frames < 1:
                        raise RuntimeError("转换后的 GIF 图像帧数无效")

                return gif_bytes, "gif", True
            finally:
                if temp_in and need_clean_in and os.path.exists(temp_in):
                    try:
                        os.remove(temp_in)
                    except Exception:
                        pass
                if temp_out and os.path.exists(temp_out):
                    try:
                        os.remove(temp_out)
                    except Exception:
                        pass

        # 2. WebP 格式：判断是动态还是静态
        if fmt == "webp":
            try:
                with Image.open(io.BytesIO(data_bytes)) as img:
                    is_animated = getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1
            except Exception as e:
                raise RuntimeError(f"解析 WebP 图像失败: {e}")

            if is_animated:
                from services.webm_converter import convert_animated_webp_to_gif
                try:
                    gif_bytes = convert_animated_webp_to_gif(data_bytes)
                    with Image.open(io.BytesIO(gif_bytes)) as test_img:
                        if getattr(test_img, "n_frames", 1) < 1:
                            raise RuntimeError("转换后的 GIF 帧数无效")
                    return gif_bytes, "gif", True
                except Exception as e:
                    raise RuntimeError(f"动态 WebP 转 GIF 失败: {e}")
            else:
                # 静态 WebP 不转为 GIF，保持现有静态处理方式
                return data_bytes, "webp", False

        # 3. APNG 格式 -> 转换为 GIF
        if fmt == "apng":
            try:
                from services.qq_extractor import QQExtractor
                import tempfile
                fd_in, temp_in = tempfile.mkstemp(suffix=".png")
                with os.fdopen(fd_in, "wb") as f:
                    f.write(data_bytes)
                temp_gif = None
                try:
                    temp_gif = QQExtractor.convert_apng_to_gif(temp_in)
                    if temp_gif and os.path.exists(temp_gif):
                        with open(temp_gif, "rb") as f:
                            gif_bytes = f.read()
                        return gif_bytes, "gif", True
                    else:
                        raise RuntimeError("APNG 转换 GIF 失败")
                finally:
                    if os.path.exists(temp_in):
                        try:
                            os.remove(temp_in)
                        except Exception:
                            pass
                    if temp_gif and os.path.exists(temp_gif):
                        try:
                            os.remove(temp_gif)
                        except Exception:
                            pass
            except Exception as e:
                raise RuntimeError(f"APNG 转 GIF 异常: {e}")

        # 4. 普通 GIF / PNG / JPEG / BMP / TIFF
        return data_bytes, fmt, (fmt == "gif")

    def _standardize_and_save(self, data_bytes, original_ext, source_path=None):
        try:
            # 步骤 1：格式归一化预处理（魔数识别、动态 WebM/动态 WebP/APNG 转为标准 GIF）
            norm_bytes, norm_fmt, is_norm_animated = self._convert_source_to_standard_bytes(data_bytes, source_path)

            img = Image.open(io.BytesIO(norm_bytes))
            is_animated = getattr(img, "is_animated", False) or is_norm_animated

            # 步骤 2：清洗与编码
            if is_animated:
                # 动态资源（GIF）保留原始帧与透明通道，对转换后的最终 GIF 执行哈希
                final_bytes = norm_bytes
                final_ext = '.gif'
                file_hash = self._calculate_bytes_hash(final_bytes)
            else:
                # 静态资源（静态 WebP/PNG/JPG/BMP 等）现有处理方式一律不改
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')

                clean_img = Image.new('RGBA', img.size)
                clean_img.paste(img, (0, 0))

                output_io = io.BytesIO()
                clean_img.save(output_io, format="PNG", optimize=True)
                final_bytes = output_io.getvalue()
                final_ext = '.png'

                # 语义钉死：写入 image_features.md5 的值一律是 file_md5
                file_hash = self._calculate_bytes_hash(final_bytes)

            # 步骤 3：哈希查重与去重入库
            # L1 快路径：用最终入库文件的 file_md5 在 _hashes_cache 中查找
            if file_hash and file_hash in self._hashes_cache:
                existing_filename = self._hashes_cache[file_hash]
                existing_path = self._to_abspath(existing_filename)
                if os.path.exists(existing_path):
                    self.move_image_to_front(existing_path)
                    return existing_path, True
                else:
                    del self._hashes_cache[file_hash]

            # L2 像素路径：快路径未命中且为静态图时，与本地 images 目录的 sync_key 索引比对
            if not is_animated:
                pixel_hash = self._calculate_pixel_hash(img)
                if pixel_hash:
                    skey = f"p:{pixel_hash}"
                    sync_index = self._get_sync_key_index()
                    if skey in sync_index:
                        existing_filename = sync_index[skey]
                        existing_path = self._to_abspath(existing_filename)
                        if os.path.exists(existing_path):
                            self._hashes_cache[file_hash] = existing_filename
                            self._save_hashes()
                            self.move_image_to_front(existing_path)
                            return existing_path, True

            # 步骤 4：正式写入磁盘
            filename = self.generate_new_filename(final_ext)
            filepath = os.path.join(self.images_dir, filename)

            with open(filepath, 'wb') as f:
                f.write(final_bytes)

            if file_hash:
                self._hashes_cache[file_hash] = filename
                self._save_hashes()

            # 同步维护内存中的 sync_key 索引
            if not is_animated:
                pixel_hash = self._calculate_pixel_hash(img)
                if pixel_hash:
                    skey = f"p:{pixel_hash}"
                    self._get_sync_key_index().setdefault(skey, filename)
            else:
                skey = f"f:{file_hash}"
                self._get_sync_key_index().setdefault(skey, filename)

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

            return self._standardize_and_save(data_bytes, ext, source_path=source_path)
        except Exception as e:
            print(f"[ERROR] 读取文件失败: {e}")
            return None, False

    def force_reload(self):
        """强制清空内存缓存，从 SQLite DB 重新加载并同步保存到 JSON 备份文件"""
        self._images_dirty = True
        self._categories_dirty = True
        self._metadata_dirty = True
        self._recent_cache = None
        self._hashes_cache = self._load_hashes()
        
        # 从数据库加载最新数据
        all_images = self.get_all_images()
        categories = self.get_all_categories()
        metadata = self.get_all_metadata()
        category_icons = self.get_all_category_icons()

        # 仅将获取到的最新数据写回 JSON 文件，避免再次写入 DB
        # 1. 写 order.json
        try:
            filenames = [self._to_filename(p) for p in all_images]
            self._atomic_write_json(self.order_file, filenames)
        except Exception as e:
            print(f"[ERROR] force_reload 保存 order.json 失败: {e}")

        # 2. 写 categories.json
        try:
            portable_categories = {}
            for category, paths in categories.items():
                portable_categories[category] = [self._to_filename(p) for p in paths]
            self._atomic_write_json(self.categories_file, portable_categories)
        except Exception as e:
            print(f"[ERROR] force_reload 保存 categories.json 失败: {e}")

        # 3. 写 metadata.json
        try:
            portable_meta = {self._to_filename(k): str(v) for k, v in metadata.items()}
            self._atomic_write_json(self.metadata_file, portable_meta)
        except Exception as e:
            print(f"[ERROR] force_reload 保存 metadata.json 失败: {e}")

        # 4. 写 category_icons.json
        try:
            portable_icons = {}
            for cat, val in category_icons.items():
                if any(val.lower().endswith(ext) for ext in self.SUPPORTED_FORMATS):
                    portable_icons[cat] = self._to_filename(val)
                else:
                    portable_icons[cat] = val
            self._atomic_write_json(self.icons_file, portable_icons)
        except Exception as e:
            print(f"[ERROR] force_reload 保存 category_icons.json 失败: {e}")

        # 5. 写 hashes.json
        try:
            self._atomic_write_json(self.hashes_file, self._hashes_cache)
        except Exception as e:
            print(f"[ERROR] force_reload 保存 hashes.json 失败: {e}")

        # 重置脏标记为 False
        self._images_cache = all_images
        self._images_dirty = False
        
        self._categories_cache = categories
        reverse_map = {}
        for category, abs_paths in categories.items():
            for p in abs_paths:
                if p not in reverse_map:
                    reverse_map[p] = []
                reverse_map[p].append(category)
        self._image_to_categories_cache = reverse_map
        self._categories_dirty = False
        
        self._metadata_cache = metadata
        self._metadata_dirty = False

    def cleanup_dead_links(self):
        """清除不存在的文件在排序、分类、元数据、哈希、最近记录及数据库中的残留记录(死链自愈)"""
        with self.lock:
            # A. 扫描实际存在的文件
            if not os.path.exists(self.images_dir):
                return
            actual_filenames = set(f for f in os.listdir(self.images_dir) if f.lower().endswith(self.SUPPORTED_FORMATS))
            actual_paths = set(self._to_abspath(f) for f in actual_filenames)
            
            # B. 自愈 order
            all_images = self.get_all_images()
            cleaned_images = [p for p in all_images if p in actual_paths]
            if len(cleaned_images) != len(all_images):
                self.save_order(cleaned_images)
                
            # C. 自愈 categories
            categories = self.get_all_categories()
            changed_cats = False
            for cat, paths in categories.items():
                cleaned_paths = [p for p in paths if p in actual_paths]
                if len(cleaned_paths) != len(paths):
                    categories[cat] = cleaned_paths
                    changed_cats = True
            if changed_cats:
                self.save_categories(categories)
                
            # D. 自愈 metadata
            metadata = self.get_all_metadata()
            changed_meta = False
            keys_to_del = [p for p in metadata if p not in actual_paths]
            for k in keys_to_del:
                del metadata[k]
                changed_meta = True
            if changed_meta:
                self.save_metadata(metadata)
                
            # E. 自愈 recent
            recent = self.get_recent_images()
            cleaned_recent = [p for p in recent if p in actual_paths]
            if len(cleaned_recent) != len(recent):
                self._recent_cache = cleaned_recent
                try:
                    filenames = [self._to_filename(p) for p in self._recent_cache]
                    self._atomic_write_json(self.recent_file, filenames)
                except Exception:
                    pass
                    
            # F. 自愈 hashes
            changed_hashes = False
            hash_keys_to_remove = []
            for h, f in self._hashes_cache.items():
                if f not in actual_filenames:
                    hash_keys_to_remove.append(h)
            for h in hash_keys_to_remove:
                del self._hashes_cache[h]
                changed_hashes = True
            if changed_hashes:
                self._save_hashes()
                
            # G. 自愈数据库中的记录
            try:
                db_paths = {
                    'features': self.features_db_path,
                    'metadata': self.metadata_db_path,
                    'categories': self.categories_db_path,
                    'order': self.order_db_path,
                    'recent': self.recent_db_path
                }
                for db_name, db_path in db_paths.items():
                    if os.path.exists(db_path):
                        with sqlite3.connect(db_path) as conn:
                            cursor = conn.cursor()
                            table_name = {
                                'features': 'image_features',
                                'metadata': 'image_metadata',
                                'categories': 'category_images',
                                'order': 'item_orders',
                                'recent': 'recent_history'
                            }[db_name]
                            
                            cursor.execute(f"SELECT DISTINCT image_path FROM {table_name}")
                            rows = cursor.fetchall()
                            dead_in_db = []
                            for r in rows:
                                fname = self._to_filename(r[0])
                                if fname not in actual_filenames:
                                    dead_in_db.append((r[0],))
                                    
                            if dead_in_db:
                                cursor.executemany(f"DELETE FROM {table_name} WHERE image_path = ?", dead_in_db)
                                conn.commit()
            except Exception as e:
                print(f"[WARNING] 数据库死链清理提示: {e}")

    def delete_images_batch(self, filepaths, progress_callback=None, cancel_check=None):
        """批量删除指定的图片文件，并同步清理分类、元数据、哈希、最近使用（DB+JSON双写双删）"""
        result = {
            'requested': len(filepaths),
            'deleted': 0,
            'missing_cleaned': 0,
            'failed': 0,
            'cancelled': 0,
            'unprocessed': 0,
            'failure_details': {}
        }
        
        if not filepaths:
            return result
            
        norm_paths = []
        seen = set()
        for p in filepaths:
            if not p:
                continue
            abs_p = self._to_abspath(p)
            if abs_p not in seen:
                seen.add(abs_p)
                norm_paths.append(abs_p)
                
        result['requested'] = len(norm_paths)
        
        valid_paths = []
        for p in norm_paths:
            real_p = os.path.normcase(os.path.abspath(os.path.realpath(p)))
            real_images_dir = os.path.normcase(os.path.abspath(os.path.realpath(self.images_dir)))
            if not real_p.startswith(real_images_dir + os.sep) and real_p != real_images_dir:
                result['failed'] += 1
                result['failure_details'][p] = "拒绝删除：路径越出资源库边界"
            else:
                valid_paths.append(p)
                
        if not valid_paths:
            return result

        CHUNK_SIZE = 50
        total_valid = len(valid_paths)
        
        db_paths = {
            'features': self.features_db_path,
            'metadata': self.metadata_db_path,
            'categories': self.categories_db_path,
            'order': self.order_db_path,
            'recent': self.recent_db_path
        }
        
        all_items_to_clean = []

        with self.lock:
            for chunk_idx in range(0, total_valid, CHUNK_SIZE):
                if cancel_check and cancel_check():
                    result['cancelled'] += len(valid_paths) - chunk_idx
                    result['unprocessed'] = len(valid_paths) - chunk_idx
                    break

                chunk = valid_paths[chunk_idx:chunk_idx + CHUNK_SIZE]

                deleted_in_chunk = []
                missing_in_chunk = []
                failed_in_chunk = []

                for p in chunk:
                    if cancel_check and cancel_check():
                        break

                    if not os.path.exists(p):
                        missing_in_chunk.append(p)
                    else:
                        try:
                            os.remove(p)
                            deleted_in_chunk.append(p)
                        except Exception as e:
                            failed_in_chunk.append(p)
                            result['failure_details'][p] = f"物理删除失败: {str(e)}"

                items_to_clean = deleted_in_chunk + missing_in_chunk
                if items_to_clean:
                    all_items_to_clean.extend(items_to_clean)

                result['deleted'] += len(deleted_in_chunk)
                result['missing_cleaned'] += len(missing_in_chunk)
                result['failed'] += len(failed_in_chunk)

                if progress_callback:
                    progress_callback(result['deleted'] + result['missing_cleaned'] + result['failed'], total_valid)

            # 循环全部结束后，一次性写入 JSON 文件并清空数据库记录，极大地提高大批量删除效率
            if all_items_to_clean:
                filenames_to_clean = [self._to_filename(p) for p in all_items_to_clean]
                filenames_set = set(filenames_to_clean)
                filepaths_set = set(all_items_to_clean)

                # 1. 批量清理并保存分类 JSON
                categories = self.get_all_categories()
                changed_cats = False
                for cat_name, paths in categories.items():
                    new_paths = [path for path in paths if path not in filepaths_set]
                    if len(new_paths) != len(paths):
                        categories[cat_name] = new_paths
                        changed_cats = True
                if changed_cats:
                    self.save_categories(categories)

                # 2. 批量清理并保存元数据 JSON
                metadata = self.get_all_metadata()
                changed_meta = False
                for p in all_items_to_clean:
                    if p in metadata:
                        del metadata[p]
                        changed_meta = True
                if changed_meta:
                    self.save_metadata(metadata)

                # 3. 批量清理并保存最近缓存/JSON
                if self._recent_cache is not None:
                    new_recent = [p for p in self._recent_cache if p not in filepaths_set]
                    if len(new_recent) != len(self._recent_cache):
                        self._recent_cache = new_recent
                        try:
                            filenames = [self._to_filename(p) for p in self._recent_cache]
                            self._atomic_write_json(self.recent_file, filenames)
                        except Exception:
                            pass

                # 4. 批量清理并保存哈希缓存/JSON
                changed_hashes = False
                hash_keys_to_remove = []
                for h, f in self._hashes_cache.items():
                    if f in filenames_set:
                        hash_keys_to_remove.append(h)
                for h in hash_keys_to_remove:
                    del self._hashes_cache[h]
                    changed_hashes = True
                if changed_hashes:
                    self._save_hashes()

                # 5. 批量清理并重建同步键索引缓存
                if self._sync_key_index is not None:
                    keys_to_del = [k for k, v in self._sync_key_index.items() if v in filenames_set]
                    for k in keys_to_del:
                        del self._sync_key_index[k]

                    if keys_to_del:
                        from services.hasher import compute_sync_key
                        all_images_remaining = []
                        if os.path.exists(self.images_dir):
                            all_images_remaining = sorted(os.listdir(self.images_dir))
                        for other_name in all_images_remaining:
                            if other_name not in filenames_set and other_name.lower().endswith(self.SUPPORTED_FORMATS):
                                other_path = self._to_abspath(other_name)
                                other_skey = compute_sync_key(other_path)
                                if other_skey in keys_to_del:
                                    self._sync_key_index[other_skey] = other_name

                # 6. 批量清理并保存全局列表顺序 JSON
                all_images = self.get_all_images()
                new_all_images = [p for p in all_images if p not in filepaths_set]
                if len(new_all_images) != len(all_images):
                    self.save_order(new_all_images)

                # 7. 批量提交 SQLite 数据库删除事务
                try:
                    for db_name, db_path in db_paths.items():
                        if os.path.exists(db_path):
                            with sqlite3.connect(db_path) as conn:
                                if db_name == 'features':
                                    conn.executemany("DELETE FROM image_features WHERE image_path = ?", [(f,) for f in filenames_to_clean])
                                elif db_name == 'metadata':
                                    conn.executemany("DELETE FROM image_metadata WHERE image_path = ?", [(f,) for f in filenames_to_clean])
                                elif db_name == 'categories':
                                    conn.executemany("DELETE FROM category_images WHERE image_path = ?", [(f,) for f in filenames_to_clean])
                                elif db_name == 'order':
                                    conn.executemany("DELETE FROM item_orders WHERE image_path = ?", [(f,) for f in filenames_to_clean])
                                elif db_name == 'recent':
                                    conn.executemany("DELETE FROM recent_history WHERE image_path = ?", [(f,) for f in filenames_to_clean])
                                conn.commit()
                except Exception as e:
                    print(f"[FATAL] 数据库批量删除事务失败: {e}")
                    raise e

        self._images_dirty = True
        self._categories_dirty = True
        self._metadata_dirty = True
        return result

    def delete_image(self, filepath):
        """从本地删除指定的图片文件，并同步清理分类、元数据、哈希、最近使用（DB+JSON双写双删）"""
        result = self.delete_images_batch([filepath])
        return result.get('deleted', 0) > 0 or result.get('missing_cleaned', 0) > 0
