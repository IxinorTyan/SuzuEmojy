"""One request per process. Windows and future clients use the existing library services."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Lock
import time

from services.gallery_snapshot import snapshot


def _images(root, paths):
    folder = (root / "images").resolve()
    result = []
    for value in paths:
        path = Path(value)
        if (path.parent.resolve() != folder or path.is_symlink() or not path.is_file()
                or path.resolve().parent != folder):
            raise ValueError("图片不存在或不在当前图库")
        result.append(str(path.resolve()))
    return list(dict.fromkeys(result))


def _outside(source_root, path):
    candidate = Path(path).resolve()
    if candidate.is_relative_to(source_root):
        raise ValueError("请选择图库以外的位置")
    return candidate


def _export_files(root, paths, folder):
    output = _outside(root, folder)
    if not output.is_dir():
        raise ValueError("请选择导出文件夹")
    count = 0
    for value in paths:
        path = Path(value)
        index = 0
        while True:
            name = path.name if index == 0 else f"{path.stem}_{index}{path.suffix}"
            try:
                stream = (output / name).open("xb")
            except FileExistsError:
                index += 1
                continue
            with stream, path.open("rb") as source:
                shutil.copyfileobj(source, stream)
            count += 1
            break
    return {"count": count}


def _settings(root, values):
    from services.appearance import THEMES
    from services.atomic_json import write_json

    config = root / "config.json"
    current = json.loads(config.read_text(encoding="utf-8-sig")) if config.exists() else {}
    choices = {"appearance_mode": {"system", "light", "dark"}, "light_theme_key": THEMES, "dark_theme_key": THEMES}
    flags = {"always_on_top", "hide_main_after_paste", "show_setting_button", "show_multi_select_button",
             "convert_static_to_gif", "sidebar_is_grid_mode"}
    ranges = {"thumbnail_size": (40, 300), "sidebar_icon_size": (12, 40), "category_grid_icon_size": (20, 200),
              "sidebar_width_list": (60, 400), "sidebar_width_grid": (60, 400), "preview_delay": (0, 5000),
              "preview_size": (80, 800), "quick_panel_width": (250, 800), "quick_panel_height": (150, 1000),
              "quick_panel_thumbnail_size": (40, 200), "recent_limit": (1, 300),
              "wallpaper_blur": (0, 40), "wallpaper_brightness": (-100, 100),
              "wallpaper_overlay_opacity": (0, 100)}
    folders = {"qq_data_path", "qq_save_path", "tg_save_path", "tg_batch_save_path"}
    for key, value in values.items():
        valid = ((key in choices and isinstance(value, str) and value in choices[key])
                 or (key in flags and type(value) is bool)
                 or (key in ranges and type(value) is int and ranges[key][0] <= value <= ranges[key][1])
                 or (key in {"global_hotkey", "quick_panel_hotkey"} and isinstance(value, str) and len(value) <= 60)
                 or (key in folders and isinstance(value, str) and len(value) <= 32767
                     and "\0" not in value and Path(value).is_absolute()))
        if not valid:
            raise ValueError(f"无效的设置项: {key}")
    current.update(values)
    root.mkdir(parents=True, exist_ok=True)
    write_json(config, current)
    return {}


def _import_paths(storage, paths, category, progress, *, consume=False, cancelled=lambda: False):
    from services.import_pipeline import ImportJob
    folders = [] if consume else [p for p in paths if Path(p).is_dir()]
    files = paths if consume else [p for p in paths if not Path(p).is_dir()]
    job = ImportJob(files, storage, category, folders=folders, delete_after=consume, cancel=cancelled,
                    progress=lambda n, total: progress("import", n, total),
                    ready=lambda total: progress("import", 0, total),
                    scan_progress=lambda d, n: progress("folder_scan", d, 0, f"已扫描 {d} 个目录，发现 {n} 张图片"))
    saved, skipped, failed = job.run()
    result = {"count": saved, "skipped": skipped, "failed": failed, "warnings": job.issues[:5]}
    if job.was_cancelled:
        result["cancelled"] = 1
    if job.folder_stats is not None:
        stats = job.folder_stats
        issues = stats["issues"]
        result["folder_stats"] = {
            **stats, "issues": (issues[:10] + job.issues[:10])[:10],
            "issue_count": len(issues) + len(job.issues),
        }
    return result


def run(root, request, progress=lambda *_, **__: None):
    root = Path(root).resolve()
    action = request["action"]
    paths = request.get("paths") or []
    category = request.get("category")
    value = request.get("value")

    def exchange_progress(current, total, detail):
        progress("exchange", current, total, detail)

    if action.startswith(("qq_", "tg_")):
        from services.external_stickers import run_external
        return run_external(root, action, request, progress)
    if action == "settings":
        return _settings(root, request["settings"])
    if action == "preview_animation":
        from services.animation_preview import preview
        paths = _images(root, paths)
        if len(paths) != 1:
            raise ValueError("动画预览需要一张图库图片")
        return {"animation": preview(paths[0], request.get("limit", 320))}
    if action in {"warm_indexes", "maintain_library"}:
        from services.library_maintenance import backfill, maintain
        if action == "warm_indexes":
            return backfill(root)
        return maintain(root, progress)
    if action in {"set_wallpaper", "clear_wallpaper"}:
        from services.window_wallpaper import set_wallpaper
        if action == "set_wallpaper" and not value:
            return {"error": "请选择本地图片"}
        try:
            return set_wallpaper(root, value if action == "set_wallpaper" else None)
        except (OSError, ValueError, RuntimeError):
            return {"error": "无法保存或移除背景图片"}
    if action == "split_zip":
        from services.exchange_split import split_package
        destination, files = split_package(value, _outside(root, request["destination"]), exchange_progress)
        return {"count": len(files), "destination": str(destination)}
    if action == "export_zip":
        from services.exchange_export import ExchangeExportService
        destination = _outside(root, value)
        with NamedTemporaryFile(dir=destination.parent, suffix=".zip", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            result = ExchangeExportService(data_dir=root).export_zip(temporary, request.get("categories"),
                                                                    progress_callback=exchange_progress)
            os.replace(temporary, destination)
            return {"destination": str(destination), "warnings": result.get("warnings", [])}
        finally:
            temporary.unlink(missing_ok=True)
    if action == "similarity":
        from services.similarity import group_features, scan
        candidates = snapshot(root)["images"]
        chosen = [item["path"] for item in candidates if not category or category in item["categories"]]
        features, failures = scan(chosen, root / "cache" / "similarity-v1.db", request.get("kind", "all"),
                                  progress=lambda n, total: progress("scan", n, total))
        groups = group_features(features, request.get("threshold", 6),
                                progress=lambda n, total: progress("match", n, total))
        return {"groups": [[item.path for item in group] for group in groups],
                "similarity_groups": [[dict(path=item.path, width=item.width, height=item.height, size=item.size,
                                            duration=item.duration, animated=item.animated) for item in group] for group in groups],
                "failed": len(failures), "warnings": [f"{Path(path).name}: {error}" for path, error in failures[:20]]}
    if action == "export_files":
        return _export_files(root, _images(root, paths), value)

    from services.storage import StorageService
    if action == "import_zip":
        from services.exchange_import import ExchangeImportService
        StorageService(data_dir=root)  # Exchange files expect the legacy tables.
        service = ExchangeImportService(data_dir=root)
        saved, skipped = service.import_zip(value, progress_callback=exchange_progress)
        return {"count": saved, "skipped": skipped, "warnings": service.warnings}

    if action == "import_link":
        from services.cooperative_cancel import CancellationMarker
        from services.import_pipeline import IMPORT_FORMATS
        from services.network_image import ImageDownloadError, download_image
        cancel = CancellationMarker(request.get("cancel_file"))
        with TemporaryDirectory(prefix="suzu_link_") as directory:
            downloaded = Path(directory) / "image.download"
            progress("download", 0, 0, "正在从网络获取图片，请稍候…")
            try:
                download_image(value, downloaded, IMPORT_FORMATS, cancelled=cancel.is_set)
            except InterruptedError:
                return {"cancelled": 1}
            except ImageDownloadError as error:
                if cancel.is_set():
                    return {"cancelled": 1}
                return {"error": str(error)}
            result = _import_paths(StorageService(data_dir=root), [str(downloaded)], category, progress, cancelled=cancel.is_set)
            if not result.get("cancelled") and result["failed"] and not (result["count"] or result["skipped"]):
                return {"error": "下载的文件无法作为图片导入。"}
            return result

    if action == "import_inbox":
        from services.inbox_import import claim_ready
        paths = claim_ready(root, request.get("inbox_files") or [])
        if not paths:
            return {}
        return _import_paths(StorageService(data_dir=root), paths, None, progress, consume=True)

    if action == "import":
        from services.cooperative_cancel import CancellationMarker
        cancel = CancellationMarker(request.get("cancel_file"))
        if cancel.is_set():
            return {"cancelled": 1}
        return _import_paths(StorageService(data_dir=root), paths, category, progress, cancelled=cancel.is_set)
    storage = StorageService(data_dir=root)
    paths = [os.path.normcase(path) for path in _images(root, paths)]
    if action == "recent":
        storage.add_recent_image(paths[0], request["limit"])
        return {"recent": [Path(path).name for path in storage.get_recent_images(request["limit"])]}
    elif action == "create_category":
        from services.import_pipeline import category_name
        name = category_name(value or "")
        categories = dict(storage.get_all_categories())
        if not name or name in categories:
            raise ValueError("分类已存在或名称不合法")
        categories[name] = []
        storage.save_categories(categories, strict=True)
    elif action == "rename_category":
        # Seed the DB from the legacy JSON before the DB-based rename.
        storage.save_categories(storage.get_all_categories(), strict=True)
        if not storage.rename_category(category, (value or "").strip()):
            raise ValueError("分类已存在或名称不合法")
    elif action == "remove_category":
        categories = storage.get_all_categories()
        removable = []
        if request.get("delete_images"):
            others = {image for name, images in categories.items() if name != category for image in images}
            removable = _images(root, [image for image in categories.get(category, []) if image not in others])
        storage.remove_category(category)
        if removable:
            result = storage.delete_images_batch(removable)
            return {"count": result["deleted"], "failed": result["failed"]}
    elif action == "category_icon":
        if category not in storage.get_all_categories():
            raise ValueError("分类不存在")
        storage.set_category_icon(category, paths[0] if paths else value or "")
    elif action in {"add", "move", "remove"}:
        categories = {name: list(items) for name, items in storage.get_all_categories().items()}
        if action != "add":
            categories[category] = [path for path in categories[category] if path not in paths]
        if action != "remove":
            categories[value] = list(dict.fromkeys(categories[value] + paths))
        storage.save_categories(categories, strict=True)
    elif action in {"tags", "add_tags", "remove_tags"}:
        metadata = dict(storage.get_all_metadata())
        for path in paths:
            previous = metadata.get(path, "")
            metadata[path] = (storage.merge_tags(previous, value) if action == "add_tags" else
                              storage.remove_tags(previous, value) if action == "remove_tags" else value)
        storage.save_metadata(metadata, strict=True)
    elif action == "delete":
        result = storage.delete_images_batch(paths,
                                             progress_callback=lambda n, total: progress("delete", n, total))
        return {"count": result["deleted"], "failed": result["failed"],
                "warnings": list(result.get("failure_details", {}).values())[:20]}
    elif action == "order":
        ordered = storage.get_all_images()
        remove = set(paths)
        indexed = [index for index, path in enumerate(ordered) if path in remove]
        for index, path in zip(indexed, paths):
            ordered[index] = path
        storage.save_order(ordered, strict=True)
    elif action == "category_order":
        categories = storage.get_all_categories()
        order = request["categories"]
        if len(order) != len(categories) or set(order) != set(categories):
            raise ValueError("分类已经变化")
        storage.save_categories({name: categories[name] for name in order}, strict=True)
    else:
        raise ValueError("Unsupported operation")
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    args = parser.parse_args()
    # .NET Framework can write the console encoding's UTF-8 preamble when it opens the redirected pipe.
    sys.stdin.reconfigure(encoding="utf-8-sig")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    request = json.load(sys.stdin)
    last_updates = {}
    progress_lock = Lock()

    def report(phase, current, total, detail=None, *, row=None, name=None):
        with progress_lock:
            previous, last_update = last_updates.get(row, (None, 0.0))
            now = time.monotonic()
            if phase != previous or (total > 0 and current == total) or now - last_update >= .08:
                last_updates[row] = phase, now
                update = dict(phase=phase, current=current, total=total, detail=detail)
                if row is not None:
                    update.update(row=row, name=name)
                print("SUZU_PROGRESS " + json.dumps(update), file=sys.stderr, flush=True)

    with redirect_stdout(sys.stderr):
        result = run(args.data, request, report if request.get("report_progress") else lambda *_, **__: None)
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
