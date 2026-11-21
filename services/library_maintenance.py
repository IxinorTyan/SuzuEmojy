"""Index existing resources without changing images; back up before legacy maintenance."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import mkdtemp

from services.gallery_snapshot import GALLERY_SUFFIXES

# Exact keys in image_features already serve as durable receipts. This version only
# governs the decisions for unchanged files which cannot have a v1 key.
SYNC_KEY_CHECK_VERSION = 1


def _plain(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError(f"不支持链接类型的图库路径：{path.name}")
    return path


def _images(root):
    folder = _plain(root / "images")
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if path.suffix.lower() in GALLERY_SUFFIXES and not path.is_symlink() and path.is_file())


def backfill(root, progress=lambda *_: None):
    from services.apng import is_apng
    from services.hasher import compute_sync_key

    paths = [p for p in _images(root) if p.suffix.lower() in ('.png', '.gif')]
    if not paths:
        return {"count": 0}
    db_path = _plain(root / "features.db")
    if not db_path.is_file() and (root / "hashes.json").is_file():
        # Do not make an old JSON-only library look migrated before its explicit upgrade.
        return {"count": 0}
    count = 0
    with closing(sqlite3.connect(db_path, timeout=10)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS image_features (
                image_path TEXT PRIMARY KEY, md5 TEXT, dhash TEXT, phash TEXT,
                quality_score REAL DEFAULT 0.0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(image_features)")}
            for name, kind in (("sync_key", "TEXT"), ("file_size", "INTEGER"), ("mtime_ns", "INTEGER")):
                if name not in columns:
                    db.execute(f"ALTER TABLE image_features ADD COLUMN {name} {kind}")
            db.execute("CREATE INDEX IF NOT EXISTS idx_features_sync_key ON image_features(sync_key)")
            db.execute("""CREATE TABLE IF NOT EXISTS sync_key_checks (
                image_path TEXT PRIMARY KEY, rule_version INTEGER NOT NULL,
                file_size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL
            )""")
        records = dict((name, (size, mtime)) for name, size, mtime in db.execute(
            "SELECT image_path, file_size, mtime_ns FROM image_features WHERE sync_key IS NOT NULL"))
        checked = dict((name, (size, mtime)) for name, size, mtime in db.execute(
            "SELECT image_path, file_size, mtime_ns FROM sync_key_checks WHERE rule_version = ?",
            (SYNC_KEY_CHECK_VERSION,)))

        def write_batch(batch):
            with db:
                db.execute("BEGIN IMMEDIATE")
                valid, excluded = [], []
                for path, key, size, mtime in batch:
                    try:
                        status = path.stat()
                        if not path.is_symlink() and (status.st_size, status.st_mtime_ns) == (size, mtime):
                            if key is None:
                                excluded.append((path.name, SYNC_KEY_CHECK_VERSION, size, mtime))
                            else:
                                valid.append((path.name, key, size, mtime))
                    except OSError:
                        continue
                db.executemany("""INSERT INTO image_features (image_path, sync_key, file_size, mtime_ns)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(image_path) DO UPDATE SET sync_key=excluded.sync_key,
                        file_size=excluded.file_size, mtime_ns=excluded.mtime_ns""", valid)
                db.executemany("""INSERT OR REPLACE INTO sync_key_checks
                    (image_path, rule_version, file_size, mtime_ns) VALUES (?, ?, ?, ?)""", excluded)
                db.executemany("DELETE FROM sync_key_checks WHERE image_path = ?", ((name,) for name, *_ in valid))
                return len(valid)

        pending = []
        progress(0, len(paths))
        for i, path in enumerate(paths, 1):
            try:
                status = path.stat()
                signature = (status.st_size, status.st_mtime_ns)
                if records.get(path.name) != signature and checked.get(path.name) != signature and not path.is_symlink():
                    with path.open('rb') as image:
                        # A raw APNG is not equivalent to its first frame or a static PNG.
                        animated_png = path.suffix.lower() == '.png' and is_apng(image)
                    if animated_png:
                        # Persist this intentional exclusion; don't re-open it on every launch.
                        pending.append((path, None, *signature))
                    elif key := compute_sync_key(path):
                        pending.append((path, key, *signature))
            except OSError:
                pass
            if len(pending) == 30 or i == len(paths):
                if pending:
                    count += write_batch(pending)
                    pending.clear()
                progress(i, len(paths))
    return {"count": count}


def maintain(root, progress=lambda *_: None):
    from services.migration_manager import MigrationManager
    from services.similarity import scan
    from services.storage import StorageService

    paths = _images(root)
    migrator = MigrationManager(root)
    mirrors = list(migrator.json_files.values())
    databases = [root / name for name in ("features.db", "metadata.db", "categories.db", "order.db", "recent.db")]
    for path in mirrors + databases:
        _plain(path)
    cache = _plain(root / "cache")
    similarity_db = _plain(cache / "similarity-v1.db")
    # The storage still writes JSON mirrors; they are not all legacy inputs. Use
    # the DB as authority where one already exists instead of unioning stale rows.
    tables = dict(zip(("hashes", "metadata", "categories", "order", "recent"),
                      zip(databases, ("image_features", "image_metadata", "categories", "item_orders", "recent_history"))))
    tables["category_icons"] = tables["categories"]
    legacy = set()
    for key, path in migrator.json_files.items():
        if not path.is_file():
            continue
        database, table = tables[key]
        if database.is_file():
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() \
                        and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    continue
        legacy.add(key)
    for key in legacy:
        path = migrator.json_files[key]
        kind = list if key in {"order", "recent"} else dict
        if not isinstance(json.loads(path.read_text(encoding="utf-8-sig")), kind):
            raise ValueError(f"旧图库文件格式不正确：{path.name}")

    existing = [path for path in mirrors + databases if path.is_file()]
    backup = None
    if existing:
        directory = _plain(root / "maintenance-backups")
        directory.mkdir(exist_ok=True)
        backup = Path(mkdtemp(prefix="library-", dir=directory))
        for source in existing:
            if source.suffix == ".db":
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)) as original, \
                        closing(sqlite3.connect(backup / source.name)) as saved:
                    original.backup(saved)
            else:
                shutil.copy2(source, backup / source.name)

    progress("migration", 0, 100, None)
    if legacy and not migrator.run_fast_migration(lambda detail, done: progress("migration", int(done), 100, detail),
                                                   selected=legacy):
        return {"error": "旧图库没有完全迁移。", "destination": str(backup) if backup else None}
    if not paths:  # An absent or empty images folder is not proof that its records are dead.
        return {"destination": str(backup) if backup else None}
    progress("cleanup", 0, 0, None)
    storage = StorageService(data_dir=root)
    storage.cleanup_dead_links()
    updated = backfill(root, lambda n, total: progress("index", n, total, None))
    _, failures = scan([str(path) for path in paths], similarity_db,
                           progress=lambda n, total: progress("scan", n, total, None))
    return {"count": updated["count"], "failed": len(failures),
            "warnings": [f"{Path(path).name}: {error}" for path, error in failures[:5]],
            "destination": str(backup) if backup else None, "library_changed": True}
