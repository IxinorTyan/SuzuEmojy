"""Portable category icons shared by package import/export and storage."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3


IMAGE_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp"}


def resolve_icon_path(data_dir, value):
    path = Path(value)
    if path.is_absolute():
        return path
    if value.replace("\\", "/").startswith("category_icons/"):
        return Path(data_dir) / path
    return Path(data_dir) / "images" / path


def portable_icon_path(data_dir, value):
    path = Path(value)
    if not path.is_absolute() and value.replace("\\", "/").startswith("category_icons/"):
        return value.replace("\\", "/")
    try:
        relative = path.resolve().relative_to((Path(data_dir) / "category_icons").resolve())
        return "category_icons/" + relative.as_posix()
    except ValueError:
        return path.name


def load_icons(data_dir):
    data_dir = Path(data_dir).resolve()
    icons = {}
    db = data_dir / "categories.db"
    if db.exists():
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
            icons.update(connection.execute(
                "SELECT name, icon_path FROM categories WHERE icon_path IS NOT NULL AND icon_path != ''"
            ).fetchall())
    source = data_dir / "category_icons.json"
    if source.exists():
        for name, value in json.loads(source.read_text(encoding="utf-8")).items():
            icons.setdefault(name, value)
    return icons


def is_valid_icon(data_dir, value):
    if not value:
        return False
    if Path(value).suffix.lower() not in IMAGE_SUFFIXES:
        return True
    try:
        from PIL import Image
        with Image.open(resolve_icon_path(data_dir, value)) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def pack_icon(data_dir, value, assets):
    if not value:
        return None
    if Path(value).suffix.lower() not in IMAGE_SUFFIXES:
        return {"type": "text", "text": value}
    source = resolve_icon_path(data_dir, value)
    payload = source.read_bytes()
    # Preserve animation; validate the file before advertising it in the catalog.
    from PIL import Image
    with Image.open(source) as image:
        image.verify()
    digest = hashlib.sha256(payload).hexdigest()
    path = f"icons/{digest}{source.suffix.lower()}"
    assets[path] = payload
    return {"type": "image", "asset_path": path, "asset_size": len(payload), "asset_sha256": digest}
