"""Import Telegram packs and export Exchange v1 packages without Qt."""

import os
import re
import unicodedata
from pathlib import Path
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISREG
from tempfile import TemporaryDirectory

from services.cooperative_cancel import CancellationMarker
from services.exchange_export import ExchangeExportService
from services.import_pipeline import ImportJob, RESERVED, category_name
from services.storage import StorageService
from services.tg_batch_export import TGPackageBatchRunner, download_sources, package_filename_stem
from services.tg_package_zip import EmptyTelegramPackage, stage_tg_package


class PackError(ValueError):
    """A message safe to show without exposing HTTP errors or credentials."""


def _prepare_import(stickers, sources):
    files, details = [], []
    for sticker in stickers:
        source = sources.get(sticker.index)
        if isinstance(source, str):
            files.append(source)
        else:
            details.append(f"贴纸 #{sticker.index + 1} 获取失败")
    if not files:
        raise PackError("\n".join(["没有可导出的贴纸，所有贴纸均获取失败。", *details]))
    return files, details


def _import_and_export(root, folder, storage, category, source_url, files, progress, publish, cancel):
    def check_cancel():
        if cancel.is_set():
            raise InterruptedError()

    job = ImportJob(files, storage, category, cancel=cancel.is_set,
                    ready=lambda total: progress("import", 0, total),
                    progress=lambda done, total: progress("import", done, total))
    count, duplicate, failed = job.run()
    check_cancel()
    if failed:
        raise PackError(f"{failed} 张贴纸入库失败，未导出资源包。")
    if count + duplicate == 0:
        raise PackError("收藏夹中没有可导出的资源。")

    def packaging(done, total, _):
        check_cancel()
        progress("export", done, total)

    progress("export", 0, 0)
    try:
        with stage_tg_package(ExchangeExportService(data_dir=root), folder, category, source_url, packaging) as temporary:
            check_cancel()
            output = publish(temporary)
    except EmptyTelegramPackage:
        raise PackError("收藏夹中没有可导出的资源。") from None
    return count, duplicate, output


def _publish_new(temporary, output):
    if os.name == "nt":
        os.rename(temporary, output)  # Same volume, atomic, never replaces another file.
    else:
        os.link(temporary, output)
    return output


def _existing_output(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        raise PackError("同名路径不是普通 ZIP 文件，无法覆盖。")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def export_single(root, request, progress, downloader):
    from services.tg_downloader import StickerItem

    package_id = request.get("package_id") or ""
    if (not isinstance(package_id, str) or category_name(package_id) != package_id
            or len(package_id.encode("utf-16-le", errors="surrogatepass")) > 502
            or package_filename_stem(package_id) != package_id
            or package_id.split(".")[0].rstrip().upper() in {"CONIN$", "CONOUT$"}):
        return {"error": "资源包 ID 不能作为收藏夹名称或 ZIP 文件名。"}
    name = request.get("value") or ""
    source_url = request.get("source_url")
    if (not re.fullmatch(r"[A-Za-z0-9_]{1,128}", name)
            or source_url not in {f"https://t.me/{route}/{name}" for route in ("addstickers", "addemoji")}):
        return {"error": "贴纸包无效，请重新解析。"}
    items = request.get("items") or []
    if not items or any(type(item.get("index")) is not int or not 0 <= item["index"] < 10000
                        or not re.fullmatch(r"[A-Za-z0-9_-]+", item.get("id") or "") for item in items):
        return {"error": "没有有效的贴纸，请重新解析。"}
    stickers = [StickerItem(index, file_id, "") for index, file_id in
                sorted({item["index"]: item["id"] for item in items}.items())]
    target = Path(request.get("destination") or "")
    folder = target.resolve() if target.is_absolute() else None
    if folder is None or folder.is_relative_to(root) or not folder.is_dir():
        return {"error": "请选择图库以外的文件夹。"}
    output = folder / (package_id + ".zip")
    library_changed = False
    warnings = []
    try:
        existing = _existing_output(output)
        if existing is not None and request.get("overwrite_package") is not True:
            raise PackError("资源包已存在，未覆盖。")

        def publish(temporary):
            if existing is None:
                return _publish_new(temporary, output)
            if _existing_output(output) != existing:
                raise PackError("同名资源包已变化，未覆盖。")
            os.replace(temporary, output)
            return output

        cancel = CancellationMarker(None)
        with TemporaryDirectory(prefix="tg_single_") as staging:
            sources = download_sources(downloader, stickers, Path(staging),
                                       lambda done, total: progress("download", done, total), cancel)
            files, warnings = _prepare_import(stickers, sources)
            storage = StorageService(data_dir=root)
            library_changed = True  # ImportJob may commit before a later import or ZIP failure.
            count, duplicate, output = _import_and_export(
                root, folder, storage, package_id, source_url, files, progress, publish, cancel,
            )
        return dict(count=count, skipped=duplicate, failed=len(warnings), destination=str(output),
                    warnings=warnings, library_changed=library_changed)
    except PackError as exc:
        error = str(exc)
    except FileExistsError:
        error = "资源包已存在，未覆盖。"
    except Exception:
        error = "无法导出资源包，请检查所选目录后重试。"
    return dict(error=error, warnings=warnings, library_changed=library_changed)


def export_batch(root, request, progress, downloader_factory):
    links = [link.strip() for link in (request.get("links") or []) if link.strip()]
    destination = request.get("destination")
    if not links or not destination:
        return {"error": "请输入 TG 链接并选择保存目录。"}
    cancel = CancellationMarker(request.get("cancel_file"))
    if cancel.is_set():
        rows = [dict(index=index, status="cancelled", message="") for index, _ in enumerate(links)]
        for row in rows:
            progress("cancelled", 0, 0, "", row=row["index"])
        return dict(count=0, failed=0, cancelled=len(rows), batch_rows=rows)
    target = Path(destination)
    if not target.is_absolute():
        return {"error": "请选择图库以外的文件夹"}
    folder = target.resolve()
    if folder.is_relative_to(root):
        return {"error": "请选择图库以外的文件夹"}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        used_files = {entry.name.casefold() for entry in folder.iterdir()}
    except OSError:
        return {"error": "无法写入所选保存目录。"}
    token = request["bot_token"].strip()
    storage = None
    used_names = set()

    def check_cancel():
        if cancel.is_set():
            raise InterruptedError()

    def normalized_name(name):
        return unicodedata.normalize("NFC", name).strip().casefold()

    def safe_text(text):
        return str(text).replace(token, "***")

    def report(index, stage, done, total, message):
        progress(stage, done, total, None if stage == "parsed" else message,
                 row=index, name=safe_text(message) if stage == "parsed" else None)

    def publish(temporary, stem):
        for suffix in range(1, 10001):
            filename = (stem if suffix == 1 else f"{stem} ({suffix})") + ".zip"
            if filename.casefold() in used_files:
                continue
            used_files.add(filename.casefold())
            check_cancel()
            output = folder / filename
            try:
                return _publish_new(temporary, output)
            except FileExistsError:
                continue
        raise PackError("资源包文件重名过多。")

    def process(item, _):
        nonlocal storage
        check_cancel()
        pack = item.pack
        if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", pack.name or ""):
            raise PackError("Telegram 返回了无效的贴纸包名。")
        files, details = _prepare_import(pack.stickers, item.sources)

        if storage is None:
            storage = StorageService(data_dir=root)
            used_names.update(normalized_name(name) for name in storage.get_all_categories())
            used_names.update(RESERVED)
        title = category_name(re.sub(r"[\x00-\x1f]", " ", safe_text(pack.title or pack.name)).strip(), pack.name)
        category, suffix = title, 2
        while normalized_name(category) in used_names:
            category = f"{title} ({suffix})"
            suffix += 1
        used_names.add(normalized_name(category))
        count, duplicate, output = _import_and_export(
            root, folder, storage, category, pack.source_url, files,
            lambda phase, done, total: report(item.index, phase, done, total, ""),
            lambda temporary: publish(temporary, package_filename_stem(title[:96])), cancel,
        )
        message = f"已导出：处理成功 {count + duplicate} 张，获取失败跳过 {len(details)} 张。"
        return dict(path=str(output), message="\n".join([message, str(output), *details]))

    runner = TGPackageBatchRunner(
        links, downloader_factory, process, report, cancel,
        format_error=lambda error: str(error) if isinstance(error, PackError) else "无法解析、下载或写入该贴纸包。",
    )
    rows = runner.run()
    return dict(count=sum(row["status"] == "done" for row in rows),
                failed=sum(row["status"] == "failed" for row in rows),
                cancelled=sum(row["status"] == "cancelled" for row in rows),
                batch_rows=rows)
