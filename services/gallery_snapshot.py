"""Read the existing desktop library for a frontend without importing Qt."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

from services.appearance import THEMES
from services.exchange_icons import IMAGE_SUFFIXES, resolve_icon_path
from services.window_wallpaper import snapshot_wallpaper


GALLERY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".webm", ".tgs"})
RESERVED_CATEGORIES = {"全部表情", "未分类", "新建分类"}


def _json(path, kind):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return kind()
    return value if isinstance(value, kind) else kind()


def _rows(path, *queries):
    if not path.is_file():
        return [[] for _ in queries]
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        return [db.execute(query).fetchall() for query in queries]


def _name(path):
    return path.replace("\\", "/").rsplit("/", 1)[-1] if isinstance(path, str) else ""


def _recent(root, filenames):
    try:
        rows, = _rows(root / "recent.db", "SELECT image_path FROM recent_history ORDER BY updated_at DESC, id DESC")
    except sqlite3.Error:
        # Older archives may still have used_at; this bridge must not migrate them.
        try:
            rows, = _rows(root / "recent.db", "SELECT image_path FROM recent_history ORDER BY used_at DESC, id DESC")
        except sqlite3.Error:
            rows = []
    values = [row[0] for row in rows] or _json(root / "recent.json", list)
    ordered = dict.fromkeys(os.path.normcase(_name(value)) for value in values)
    return [filenames[name].name for name in ordered if name in filenames]


def snapshot(data_dir):
    root = Path(data_dir).resolve()
    images = root / "images"
    paths = sorted((p for p in images.iterdir() if p.is_file() and p.suffix.lower() in GALLERY_SUFFIXES),
                   key=lambda p: p.name, reverse=True) if images.is_dir() else []
    filenames = {os.path.normcase(p.name): p for p in paths}
    order, = _rows(root / "order.db", "SELECT image_path FROM item_orders ORDER BY sort_order ASC")
    order = [row[0] for row in order] or _json(root / "order.json", list)
    ordered = dict.fromkeys(os.path.normcase(_name(p)) for p in order)
    paths = ([p for p in paths if os.path.normcase(p.name) not in ordered]
             + [filenames[name] for name in ordered if name in filenames])

    categories, relations = _rows(
        root / "categories.db",
        "SELECT name, icon_path FROM categories ORDER BY sort_order ASC, id ASC",
        "SELECT category_name, image_path FROM category_images",
    )
    icons = _json(root / "category_icons.json", dict)
    if categories:
        groups = {name: [] for name, _ in categories}
        icons.update({name: icon for name, icon in categories if icon})
        for name, filename in relations:
            if name in groups:
                groups[name].append(filename)
    else:
        groups = _json(root / "categories.json", dict)

    by_image = {}
    category_list = []
    for name, members in groups.items():
        if name in RESERVED_CATEGORIES:
            continue
        icon = icons.get(name)
        icon_path = None
        if isinstance(icon, str) and Path(icon).suffix.lower() in IMAGE_SUFFIXES:
            candidate = resolve_icon_path(root, icon)
            icon_path = str(candidate) if candidate.is_file() else None
            icon = None
        category_list.append({"name": name, "icon": icon if isinstance(icon, str) else None,
                              "iconPath": icon_path})
        for member in members if isinstance(members, list) else ():
            groups_for_image = by_image.setdefault(os.path.normcase(_name(member)), [])
            if name not in groups_for_image:
                groups_for_image.append(name)

    db_metadata, = _rows(root / "metadata.db", "SELECT image_path, keywords FROM image_metadata")
    keywords = {os.path.normcase(_name(path)): str(value or "")
                for path, value in _json(root / "metadata.json", dict).items()}
    keywords.update({os.path.normcase(_name(path)): value or "" for path, value in db_metadata})
    return {
        "settings": _json(root / "config.json", dict),
        "wallpaper": snapshot_wallpaper(root),
        "themes": THEMES,
        "categories": category_list,
        "recent": _recent(root, filenames),
        "images": [
            {"path": str(p), "name": p.name, "keywords": keywords.get(os.path.normcase(p.name), ""),
             "categories": by_image.get(os.path.normcase(p.name), []),
             "animated": p.suffix.lower() in (".gif", ".webp")}
            for p in paths
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(snapshot(args.data_dir), sys.stdout, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
