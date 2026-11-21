from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys

from services.gallery_snapshot import snapshot


def _save_json(root, file, value):
    (root / file).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_gallery_snapshot_honors_db_sorting_icons_and_keyword_fallback(tmp_path):
    root = tmp_path / "表情 data!"
    images = root / "images"
    images.mkdir(parents=True)
    for filename in ("旧.png", "animated.GIF", "新.png", "ignore.txt"):
        (images / filename).touch()
    (root / "category_icons").mkdir()
    (root / "category_icons" / "folder.png").touch()

    _save_json(root, "metadata.json", {"旧.png": "我爱", "images/新.png": "旧值"})
    _save_json(root, "category_icons.json", {"动图": "🌸"})
    _save_json(root, "config.json", {"appearance_mode": "dark", "dark_theme_key": "sage"})
    with closing(sqlite3.connect(root / "categories.db")) as db:
        db.executescript("""
            CREATE TABLE categories (id INTEGER, name TEXT, icon_path TEXT, sort_order INTEGER);
            CREATE TABLE category_images (category_name TEXT, image_path TEXT);
            INSERT INTO categories VALUES (1, '其他', 'category_icons/folder.png', 1);
            INSERT INTO categories VALUES (2, '动图', '', 0);
            INSERT INTO categories VALUES (3, '全部表情', '', 2);
        """)
        db.executemany("INSERT INTO category_images VALUES (?, ?)", [
            ("动图", r"old\images\animated.GIF"), ("其他", "新.png"), ("全部表情", "旧.png"),
        ])
        db.commit()
    with closing(sqlite3.connect(root / "order.db")) as db:
        db.executescript("""
            CREATE TABLE item_orders (image_path TEXT, sort_order INTEGER);
            INSERT INTO item_orders VALUES ('旧.png', 1);
            INSERT INTO item_orders VALUES ('animated.GIF', 2);
            INSERT INTO item_orders VALUES ('removed.png', 3);
        """)
    with closing(sqlite3.connect(root / "metadata.db")) as db:
        db.executescript("""
            CREATE TABLE image_metadata (image_path TEXT, keywords TEXT);
            INSERT INTO image_metadata VALUES ('新.png', '新关键词');
        """)

    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = snapshot(root)
    assert result["settings"]["dark_theme_key"] == "sage"
    assert result["themes"]["sage"]["dark"]["bg"] == "#161F19"
    assert [item["name"] for item in result["images"]] == ["新.png", "旧.png", "animated.GIF"]
    assert [item["keywords"] for item in result["images"]] == ["新关键词", "我爱", ""]
    assert result["images"][2]["animated"] is True
    assert result["images"][2]["categories"] == ["动图"]
    assert result["images"][1]["categories"] == []
    assert result["categories"] == [
        {"name": "动图", "icon": "🌸", "iconPath": None},
        {"name": "其他", "icon": None, "iconPath": str(root / "category_icons" / "folder.png")},
    ]
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_gallery_snapshot_uses_legacy_json_and_does_not_load_qt(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("b.png", "c.png", "a.png"):
        (images / name).touch()
    _save_json(tmp_path, "order.json", ["a.png", "b.png"])
    _save_json(tmp_path, "categories.json", {"收藏": ["a.png"]})
    output = subprocess.check_output([
        sys.executable, "-B", "-c",
        "import sys; from services.gallery_snapshot import main; main(); "
        "assert not any(m.startswith(('PySide', 'qfluentwidgets')) for m in sys.modules)",
        os.fspath(tmp_path)],
    )
    result = json.loads(output)
    assert [item["name"] for item in result["images"]] == ["c.png", "a.png", "b.png"]
    assert result["images"][1]["categories"] == ["收藏"]


def test_snapshot_reads_recent_history_without_migrating_legacy_db(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        (images / name).touch()
    _save_json(tmp_path, "recent.json", ["c.png", r"images\b.png", "missing.png", "c.png"])
    assert snapshot(tmp_path)["recent"] == ["c.png", "b.png"]

    db_file = tmp_path / "recent.db"
    with closing(sqlite3.connect(db_file)) as db:
        db.executescript("""
            CREATE TABLE recent_history (id INTEGER PRIMARY KEY, image_path TEXT, used_at TEXT);
            INSERT INTO recent_history (image_path, used_at) VALUES
                ('missing.png', '2026-01-04'), ('a.png', '2026-01-03'),
                ('b.png', '2026-01-02'), ('a.png', '2026-01-01');
        """)
    original_db = db_file.read_bytes()
    assert snapshot(tmp_path)["recent"] == ["a.png", "b.png"]
    assert db_file.read_bytes() == original_db
