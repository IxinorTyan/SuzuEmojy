import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class MigrationManager:
    """
    极速多 DB 独立数据迁移管理器:
    将原本分散存储在 data/ 目录下的旧 JSON 配置文件解耦写入对应的 SQLite 数据库文件中。

    架构设计与对应关系:
    图片文件存在 data/images/，配置文件在 data/ 目录下：
    1. hashes.json ➔ data/features.db (表: image_features)
    2. metadata.json ➔ data/metadata.db (表: image_metadata)
    3. categories.json + category_icons.json ➔ data/categories.db (表: categories, category_images)
    4. order.json ➔ data/order.db (表: item_orders)
    5. recent.json ➔ data/recent.db (表: recent_history)
    注：config.json 保持为 JSON 不迁移。
    """

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            # 1. 路径定位：使用 pathlib.Path(__file__).resolve().parent.parent / "data" 确定绝对路径
            self.data_dir = Path(__file__).resolve().parent.parent / "data"
        else:
            self.data_dir = Path(data_dir).resolve()

        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 待迁移检测的旧 JSON 配置文件列表
        self.json_files = {
            "hashes": self.data_dir / "hashes.json",
            "metadata": self.data_dir / "metadata.json",
            "categories": self.data_dir / "categories.json",
            "category_icons": self.data_dir / "category_icons.json",
            "order": self.data_dir / "order.json",
            "recent": self.data_dir / "recent.json",
        }

    def needs_migration(self) -> bool:
        """
        2. 方法 needs_migration()：
        检查上述待迁移的旧 JSON 文件是否存在任意一个。若存在返回 True，否则返回 False。
        """
        return any(path.exists() for path in self.json_files.values())

    def run_fast_migration(self, progress_callback: Optional[Callable[[str, float], None]] = None) -> bool:
        """
        3. 方法 run_fast_migration(progress_callback=None)：
        逐个检查旧 JSON，若存在：
        - 读取数据，使用 INSERT OR IGNORE 快速批量写入对应的 .db 文件（只搬运已有字段，不耗时计算哈希）。
        - 提交 SQLite 事务 (commit)。
        - 极速清理：提交成功后，直接删除该旧 JSON 文件 (unlink(missing_ok=True))。
        - 如果提供了 progress_callback(msg, percentage)，向其汇报当前进度的文本和百分比。
        """
        steps = [
            ("哈希索引数据 (features.db)", self._migrate_hashes, 20.0),
            ("关键词元数据 (metadata.db)", self._migrate_metadata, 40.0),
            ("分类与图标数据 (categories.db)", self._migrate_categories, 60.0),
            ("自定义排序数据 (order.db)", self._migrate_order, 80.0),
            ("最近使用历史 (recent.db)", self._migrate_recent, 100.0),
        ]

        overall_success = True

        for msg, migrate_func, percentage in steps:
            if progress_callback:
                try:
                    progress_callback(msg, percentage)
                except Exception as e:
                    logger.warning(f"[MigrationManager] 进度回调触发异常: {e}")

            try:
                success = migrate_func()
                if not success:
                    overall_success = False
            except Exception as e:
                logger.error(f"[MigrationManager] 步骤 '{msg}' 发生未捕获异常: {e}")
                overall_success = False

        return overall_success

    def run_migration(self, progress_callback: Optional[Callable[[str, float], None]] = None) -> bool:
        """兼容性别名入口"""
        return self.run_fast_migration(progress_callback)

    def _migrate_hashes(self) -> bool:
        """1. hashes.json -> data/features.db (表: image_features)"""
        hashes_file = self.json_files["hashes"]
        if not hashes_file.exists():
            return True

        db_path = self.data_dir / "features.db"
        try:
            with open(hashes_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, dict) and data:
                records = []
                for key, val in data.items():
                    if isinstance(val, str):
                        if len(key) == 32 and all(c in '0123456789abcdefABCDEF' for c in key):
                            md5, image_path = key, val
                        else:
                            image_path, md5 = key, val
                        records.append((os.path.basename(image_path), md5))

                if records:
                    with sqlite3.connect(db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            CREATE TABLE IF NOT EXISTS image_features (
                                image_path TEXT PRIMARY KEY,
                                md5 TEXT,
                                dhash TEXT,
                                phash TEXT,
                                quality_score REAL DEFAULT 0.0,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        cursor.executemany("""
                            INSERT OR IGNORE INTO image_features (image_path, md5)
                            VALUES (?, ?)
                        """, records)
                        conn.commit()

            hashes_file.unlink(missing_ok=True)
            logger.info(f"[MigrationManager] {hashes_file.name} 已成功极速迁移至 features.db 并删除。")
            return True
        except Exception as e:
            logger.error(f"[MigrationManager] _migrate_hashes 失败: {e}")
            return False

    def _migrate_metadata(self) -> bool:
        """2. metadata.json -> data/metadata.db (表: image_metadata)"""
        metadata_file = self.json_files["metadata"]
        if not metadata_file.exists():
            return True

        db_path = self.data_dir / "metadata.db"
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, dict) and data:
                records = []
                for path_key, val in data.items():
                    fname = os.path.basename(path_key)
                    if isinstance(val, str):
                        records.append((fname, "", val))
                    elif isinstance(val, dict):
                        tags = val.get("tags", "")
                        kw = val.get("keywords", val.get("keyword", ""))
                        tags_str = tags if isinstance(tags, str) else json.dumps(tags)
                        records.append((fname, tags_str, str(kw)))

                if records:
                    with sqlite3.connect(db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            CREATE TABLE IF NOT EXISTS image_metadata (
                                image_path TEXT PRIMARY KEY,
                                tags TEXT,
                                keywords TEXT,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        cursor.executemany("""
                            INSERT OR IGNORE INTO image_metadata (image_path, tags, keywords)
                            VALUES (?, ?, ?)
                        """, records)
                        conn.commit()

            metadata_file.unlink(missing_ok=True)
            logger.info(f"[MigrationManager] {metadata_file.name} 已成功极速迁移至 metadata.db 并删除。")
            return True
        except Exception as e:
            logger.error(f"[MigrationManager] _migrate_metadata 失败: {e}")
            return False

    def _migrate_categories(self) -> bool:
        """3. categories.json + category_icons.json -> data/categories.db (表: categories, category_images)"""
        cat_file = self.json_files["categories"]
        icons_file = self.json_files["category_icons"]

        if not cat_file.exists() and not icons_file.exists():
            return True

        db_path = self.data_dir / "categories.db"
        try:
            cat_data = {}
            if cat_file.exists():
                with open(cat_file, 'r', encoding='utf-8') as f:
                    cat_data = json.load(f)

            icon_data = {}
            if icons_file.exists():
                with open(icons_file, 'r', encoding='utf-8') as f:
                    icon_data = json.load(f)

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS categories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        icon_path TEXT,
                        sort_order INTEGER DEFAULT 0
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS category_images (
                        category_name TEXT,
                        image_path TEXT,
                        PRIMARY KEY (category_name, image_path)
                    )
                """)

                all_cats = set()
                if isinstance(cat_data, dict):
                    all_cats.update(cat_data.keys())
                if isinstance(icon_data, dict):
                    all_cats.update(icon_data.keys())

                for idx, cat_name in enumerate(all_cats):
                    icon_p = ""
                    if isinstance(icon_data, dict):
                        icon_p = icon_data.get(cat_name, "")
                        if icon_p and any(icon_p.lower().endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.webm')):
                            icon_p = os.path.basename(icon_p)

                    cursor.execute("""
                        INSERT OR IGNORE INTO categories (name, icon_path, sort_order)
                        VALUES (?, ?, ?)
                    """, (cat_name, icon_p, idx))

                    if icon_p:
                        cursor.execute("""
                            UPDATE categories SET icon_path = ? WHERE name = ?
                        """, (icon_p, cat_name))

                if isinstance(cat_data, dict):
                    for cat_name, img_paths in cat_data.items():
                        if isinstance(img_paths, list):
                            rel_records = [
                                (cat_name, os.path.basename(p)) for p in img_paths
                            ]
                            cursor.executemany("""
                                INSERT OR IGNORE INTO category_images (category_name, image_path)
                                VALUES (?, ?)
                            """, rel_records)

                conn.commit()

            if cat_file.exists():
                cat_file.unlink(missing_ok=True)
            if icons_file.exists():
                icons_file.unlink(missing_ok=True)

            logger.info("[MigrationManager] categories.json 及 category_icons.json 已成功极速迁移至 categories.db 并删除。")
            return True
        except Exception as e:
            logger.error(f"[MigrationManager] _migrate_categories 失败: {e}")
            return False

    def _migrate_order(self) -> bool:
        """4. order.json -> data/order.db (表: item_orders)"""
        order_file = self.json_files["order"]
        if not order_file.exists():
            return True

        db_path = self.data_dir / "order.db"
        try:
            with open(order_file, 'r', encoding='utf-8') as f:
                order_list = json.load(f)

            if isinstance(order_list, list) and order_list:
                records = [
                    (os.path.basename(p), idx) for idx, p in enumerate(order_list)
                ]
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS item_orders (
                            image_path TEXT PRIMARY KEY,
                            sort_order INTEGER NOT NULL
                        )
                    """)
                    cursor.executemany("""
                        INSERT OR IGNORE INTO item_orders (image_path, sort_order)
                        VALUES (?, ?)
                    """, records)
                    conn.commit()

            order_file.unlink(missing_ok=True)
            logger.info(f"[MigrationManager] {order_file.name} 已成功极速迁移至 order.db 并删除。")
            return True
        except Exception as e:
            logger.error(f"[MigrationManager] _migrate_order 失败: {e}")
            return False

    def _migrate_recent(self) -> bool:
        """5. recent.json -> data/recent.db (表: recent_history)"""
        recent_file = self.json_files["recent"]
        if not recent_file.exists():
            return True

        db_path = self.data_dir / "recent.db"
        try:
            with open(recent_file, 'r', encoding='utf-8') as f:
                recent_list = json.load(f)

            if isinstance(recent_list, list) and recent_list:
                records = [
                    (os.path.basename(p),) for p in recent_list
                ]
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS recent_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            image_path TEXT NOT NULL,
                            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    cursor.executemany("""
                        INSERT OR IGNORE INTO recent_history (image_path) VALUES (?)
                    """, records)
                    conn.commit()

            recent_file.unlink(missing_ok=True)
            logger.info(f"[MigrationManager] {recent_file.name} 已成功极速迁移至 recent.db 并删除。")
            return True
        except Exception as e:
            logger.error(f"[MigrationManager] _migrate_recent 失败: {e}")
            return False


if __name__ == '__main__':
    print("=== MigrationManager 极速迁移测试 ===")
    manager = MigrationManager()
    print(f"是否有需要迁移的旧 JSON 文件: {manager.needs_migration()}")

    if manager.needs_migration():
        def on_progress(msg, pct):
            print(f"进度 [{pct}%]: {msg}")

        res = manager.run_fast_migration(progress_callback=on_progress)
        print(f"迁移结果: {'全部成功' if res else '部分失败'}")
