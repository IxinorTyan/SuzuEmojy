"""Qt-free QQ cache and Telegram sticker operations for the desktop bridge."""
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from io import BytesIO
import os
from pathlib import Path
import re
from shutil import copyfileobj
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISREG
from struct import unpack
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import ZipFile

from PIL import Image

from services.cooperative_cancel import CancellationMarker
from services.import_pipeline import ImportJob


QQ_KINDS = {
    "personal_emoji": "个人表情",
    "emoji-recv": "接收到的表情",
    "marketface": "商店表情",
    "BaseEmojiSyastems": "系统表情",
    "emoji-related": "候选表情",
    "pic": "收藏的图片（含聊天图片）",
}
MAX_PREVIEWS = 18
MAX_ANIMATION_BYTES = 4 * 1024**2
MAX_PREVIEW_FRAME_PIXELS = 32_000_000


def _qq_accounts(directory):
    from services.qq_extractor import QQExtractor

    return sorted(number for number in QQExtractor.get_numeric_subdirectories(directory)
                  if QQExtractor.get_emoji_root(directory, number).is_dir()
                  or QQExtractor.get_pic_root(directory, number).is_dir())


def _qq_discover(directory):
    from services.qq_extractor import QQExtractor

    if not directory:
        directory = QQExtractor.get_userdata_save_path(
            r"C:\Users\Public\Documents\Tencent\QQ\UserDataInfo.ini")
    if not directory or not Path(directory).is_dir():
        return {"sources": []}
    base = Path(directory).resolve()
    accounts = []
    for number in _qq_accounts(base):
        folders = [dict(key=kind, label=label) for kind, label in QQ_KINDS.items()
                   if QQExtractor.get_category_path(base, number, kind).is_dir()]
        emoji = QQExtractor.get_emoji_root(base, number)
        if emoji.is_dir():
            known = {kind.casefold() for kind in QQ_KINDS}
            folders.extend(dict(key=child.name, label=child.name) for child in sorted(emoji.iterdir())
                           if child.is_dir() and child.name.casefold() not in known)
        if folders:
            accounts.append(dict(key=number, options=folders))
    return {"destination": str(base), "sources": accounts}


def _qq_folder(request, *, direct=False):
    from services.qq_extractor import QQExtractor

    base = Path(request["value"]).resolve()
    account, kind = request.get("account", ""), request.get("kind", "")
    if not re.fullmatch(r"\d+", account) or not kind or kind in {".", ".."} or re.search(r'[/\\:]', kind):
        raise ValueError("请选择 QQ 账号和缓存分类")
    expected = QQExtractor.get_category_path(base, account, kind)
    folder = expected.resolve()
    if (not folder.is_relative_to(base / account) or not folder.is_dir()
            or (direct and folder != expected)):
        raise ValueError("QQ 缓存分类不存在")
    return folder, kind


def _qq_path(folder, kind, value):
    path = Path(value)
    boundary = folder / "Ori" if kind == "personal_emoji" else folder
    if (path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(boundary)
            and (kind != "pic" or path.parent.name.lower() == "ori")):
        return path
    raise ValueError("QQ 缓存文件不存在或已移动")


def _file_identity(path):
    info = path.lstat()
    if not S_ISREG(info.st_mode) or info.st_nlink != 1 or (
        getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("不是独立的普通文件")
    return ":".join(map(str, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)))


def _qq_recycle(root, folder, kind, items, progress):
    from services.qq_extractor import QQExtractor
    from services.windows_recycle import recycle_file

    removed, warnings = [], []
    # Keep order without ever issuing multiple operations for the same file.
    choices = {Path(item["id"]): item for item in items}
    total = len(choices)
    progress("qq_recycle", 0, total)
    for index, (path, item) in enumerate(choices.items(), 1):
        try:
            expected = item.get("file_identity")
            if not path.is_absolute() or path != path.resolve(strict=True) or path.is_relative_to(root):
                raise ValueError("文件不在安全的 QQ 缓存范围")
            _qq_path(folder, kind, path)
            if not expected or _file_identity(path) != expected:
                raise ValueError("文件已变化，请重新扫描")
            if kind == "marketface":
                if QQExtractor.get_marketface_info(path) is None:
                    raise ValueError("不是有效的商店表情")
            else:
                # The fast scan accepts file signatures; do not recycle a
                # non-image (such as a settings XML) misidentified by magic.
                with Image.open(path) as image:
                    image.verify()
            if _file_identity(path) != expected:
                raise ValueError("文件已变化，请重新扫描")
            recycle_file(path)
            removed.append(item["id"])
        except (OSError, ValueError, RuntimeError, SyntaxError, Image.DecompressionBombError) as error:
            if len(warnings) < 5:
                reason = str(error) if isinstance(error, ValueError) else "无法移到回收站"
                warnings.append(f"{path.name}：{reason}")
        progress("qq_recycle", index, total)
    return dict(count=len(removed), failed=total - len(removed), removed_ids=removed, warnings=warnings)


def _preview(image, side=160):
    with image:
        if image.format == "PNG" and image.info.get("default_image"):
            image.seek(1)  # APNG's cover is not necessarily part of the animation.
        tile = image.convert("RGBA")
        tile.thumbnail((side, side), Image.Resampling.LANCZOS)
        output = BytesIO()
        tile.save(output, "PNG")
        return base64.b64encode(output.getvalue()).decode("ascii")


def _dimensions(path, kind):
    try:
        if kind == "marketface":
            from services.marketface_handler import is_gif_header, restore_marketface
            with path.open("rb") as stream:
                header = stream.read(10)
            if not is_gif_header(header):
                header = restore_marketface(header)
            width, height = unpack("<HH", header[6:10])
            return dict(width=width, height=height, animated=True)
        with Image.open(path) as image:
            return dict(width=image.width, height=image.height, animated=getattr(image, "is_animated", False))
    except (OSError, ValueError, Image.DecompressionBombError):
        return {}


def _detail_result(source, still, data=b""):
    motion = (base64.b64encode(data).decode("ascii")
              if len(data) <= MAX_ANIMATION_BYTES and data.startswith((b"GIF87a", b"GIF89a")) else None)
    return {"previews": [dict(id=source["id"], data=still, animation=motion)]}


def _detail(data, source, animated=False):
    return _detail_result(source, _preview(Image.open(BytesIO(data)), 380), data if animated else b"")


def _qq_preview(folder, kind, source):
    from services.qq_extractor import QQExtractor

    path = _qq_path(folder, kind, source["id"])
    if kind == "marketface":
        info = QQExtractor.get_marketface_info(path)
        return _preview(Image.open(BytesIO(info[0]))) if info else None
    return _preview(Image.open(path))


def _qq_detail(folder, kind, source):
    from services.qq_extractor import QQExtractor

    path = _qq_path(folder, kind, source["id"])
    if kind == "marketface":
        info = QQExtractor.get_marketface_info(path)
        return _detail(info[0], source, animated=info[1] > 1) if info else {}
    with Image.open(path) as image:
        fmt = image.format
        animated = getattr(image, "is_animated", False) and path.stat().st_size <= MAX_ANIMATION_BYTES
        if animated and fmt in {"PNG", "WEBP"}:
            animated = image.width * image.height * image.n_frames <= MAX_PREVIEW_FRAME_PIXELS
        still = _preview(image, 380)
    data = b""
    if animated:
        try:
            data = path.read_bytes()
            if fmt == "PNG":
                from services.apng import to_gif
                data = to_gif(data)
            elif fmt == "WEBP":
                from services.webm_converter import convert_animated_webp_to_gif
                data = convert_animated_webp_to_gif(data)
        except (OSError, ValueError, RuntimeError):
            data = b""  # Keep the detailed still when a conversion is unavailable.
    return _detail_result(source, still, data)


def _qq_export(root, folder, kind, request, progress):
    from services.apng import to_gif
    from services.qq_extractor import QQExtractor

    destination = request.get("destination")
    if not destination:
        return {"error": "请选择导出文件夹"}
    target = Path(destination).resolve()
    cache = Path(request["value"]).resolve()
    if not target.is_dir() or target.is_relative_to(root) or target.is_relative_to(cache):
        return {"error": "请选择图库和 QQ 缓存以外的文件夹"}
    cancel = CancellationMarker(request.get("cancel_file"))
    choices = list(dict.fromkeys(item["id"] for item in (request.get("items") or [])))
    warnings = []
    saved = failed = completed = 0
    progress("qq_export", 0, len(choices))

    def warn(path, reason):
        if len(warnings) < 5:
            warnings.append(f"{path.name}：{reason}")

    for source in choices:
        if cancel.is_set():
            break
        path = Path(source)
        try:
            path = _qq_path(folder, kind, path)
            payload = None
            if kind == "marketface":
                info = QQExtractor.get_marketface_info(path)
                if not info:
                    raise ValueError("商店表情已损坏")
                payload = info[0]
                name = f"{path.stem}.gif"
            else:
                ext = QQExtractor.get_actual_extension(path)
                if not ext:
                    raise ValueError("无法识别文件格式")
                if ext == "png" and QQExtractor.is_apng_file(path):
                    try:
                        payload = to_gif(path.read_bytes())
                    except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
                        name = f"{path.stem}.png"
                        warn(path, "APNG 转换失败，保留 PNG 原格式")
                    else:
                        name = f"{path.stem}.gif"
                else:
                    name = path.name if path.name.casefold().endswith((f".{ext}", f".{ext}.gif")) else f"{path.name}.{ext}"
            if cancel.is_set():
                break
            if payload is not None:
                _write_unique(target, name, lambda output: output.write(payload))
            else:
                with path.open("rb") as image:
                    _write_unique(target, name, lambda output: copyfileobj(image, output))
            saved += 1
        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError) as error:
            failed += 1
            warn(path, str(error) if isinstance(error, ValueError) else "无法导出")
        completed += 1
        progress("qq_export", completed, len(choices))
    return dict(count=saved, failed=failed, destination=str(target), warnings=warnings,
                cancelled=int(completed < len(choices) and cancel.is_set()))


def _import(root, files, category, progress, job_type=ImportJob):
    from services.storage import StorageService

    if not files:
        return {"count": 0}
    job = job_type(files, StorageService(data_dir=root), category,
                   ready=lambda n: progress("import", 0, n),
                   progress=lambda n, total: progress("import", n, total))
    count, skipped, failed = job.run()
    return dict(count=count, skipped=skipped, failed=failed, warnings=job.issues[:5])


def _qq(root, action, request, progress):
    from services.qq_extractor import QQExtractor

    if action == "qq_discover":
        return _qq_discover(request.get("value"))
    folder, kind = _qq_folder(request, direct=action == "qq_recycle")
    if action == "qq_scan":
        def scan_progress(stage, current, total):
            if stage == "walk":
                progress("qq_walk", current, 0, f"已遍历 {current} 个文件夹，发现 {total} 个候选文件")
            else:
                progress("qq_check", current, total, f"正在检查文件：{current} / {total}")

        candidates = QQExtractor.scan_emojis(folder, kind, progress=scan_progress)
        total = len(candidates)
        items = []
        progress("qq_metadata", 0, total, f"正在准备图片：0 / {total}")
        for index, candidate in enumerate(candidates, 1):
            try:
                path = _qq_path(folder, kind, candidate)
            except ValueError:
                pass
            else:
                try:
                    identity = _file_identity(path)
                except (OSError, ValueError):
                    identity = None
                items.append(dict(id=str(path), index=len(items), label=path.name,
                                  file_identity=identity, **_dimensions(path, kind)))
            progress("qq_metadata", index, total, f"正在准备图片：{index} / {total}")
        return {"items": items}
    if action == "qq_recycle":
        return _qq_recycle(root, folder, kind, request.get("items") or [], progress)
    if action == "qq_export":
        return _qq_export(root, folder, kind, request, progress)
    if action == "qq_previews":
        return _previews(request, lambda source: _qq_preview(folder, kind, source))
    if action == "qq_detail":
        items = request.get("items") or []
        return _qq_detail(folder, kind, items[0]) if items else {}
    if action == "qq_import":
        class QQImportJob(ImportJob):
            def prepare(self, path):
                path = _qq_path(folder, kind, path)
                if kind != "marketface":
                    return super().prepare(str(path))
                data = QQExtractor.get_marketface_info(path)
                if data is None:
                    raise ValueError("商店表情已损坏")
                return self.storage._prepare_image(data[0])

        paths = list(dict.fromkeys(request.get("paths") or []))
        return _import(root, paths, request.get("category"), progress, QQImportJob)
    raise ValueError("未知的 QQ 操作")


def _previews(request, preview):
    def load(source):
        try:
            data = preview(source)
            if data:
                return dict(id=source["id"], data=data)
        except Exception:
            pass  # A missing preview does not make its sticker unselectable.
        return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return {"previews": [item for item in pool.map(load, (request.get("items") or [])[:MAX_PREVIEWS]) if item]}


def _telegram(request):
    from services.tg_downloader import TGStickerDownloader

    token = (request.get("bot_token") or "").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("请输入有效的 Bot Token")
    proxy = (request.get("api_proxy") or "").strip()
    if proxy:
        try:
            url = urlsplit(proxy)
            valid = (url.scheme == "https" and url.hostname and not any(
                (url.username, url.password, url.query, url.fragment, re.search(r"[\s\\]", proxy)))
                and (url.port is None or url.port > 0))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("API 反代地址必须为 HTTPS")
    return TGStickerDownloader(bot_token=token, cf_proxy=proxy, timeout=10, retries=1,
                               prefer_direct=not proxy, allow_proxy_fallback=False, allow_redirects=False)


def _write_unique(directory, name, writer):
    path = Path(name)
    with TemporaryDirectory(prefix=".suzu-export-", dir=directory) as staging:
        temporary = Path(staging) / "content"
        with temporary.open("xb") as handle:
            writer(handle)
        for index in range(10000):
            target = directory / (name if index == 0 else f"{path.stem}_{index}{path.suffix}")
            try:
                if os.name == "nt":
                    temporary.rename(target)  # Move on this volume, without replacement.
                else:
                    os.link(temporary, target)
            except FileExistsError:
                continue
            return target
    raise OSError("文件重名过多")


def _tg_download(root, request, progress):
    from services.tg_downloader import StickerItem

    mode = request.get("kind")
    convert = request.get("output_format") or "auto"
    name = request.get("value") or ""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", name) or convert not in {"auto", "original", "png", "gif"}:
        return {"error": "贴纸包或格式无效"}
    # Do not query the pack again: rows refer to the exact file IDs the user selected.
    items = list({item["index"]: item for item in request.get("items") or []}.values())
    target = None
    if mode == "files":
        target = Path(request["destination"]).resolve()
        if not target.is_dir() or target.is_relative_to(root):
            return {"error": "请选择图库以外的文件夹"}
    elif mode != "library":
        return {"error": "下载方式无效"}

    with TemporaryDirectory(prefix="suzu-tg-") as staging:
        output = target if target is not None else Path(staging)

        def download(item):
            try:
                sticker = StickerItem(index=item["index"], file_id=item["id"], file_unique_id="")
                fmt = ("gif" if item.get("animated") else "png") if convert == "auto" else convert
                downloader = _telegram(request)
                with closing(downloader.session):
                    payload, ext = downloader.download_sticker(sticker, fmt)
                if ext not in {"png", "gif", "webp", "webm", "tgs", "jpg", "jpeg"} or not payload:
                    return None
                if not 0 <= sticker.index < 10000:
                    return None
                filename = f"{name}_{sticker.index + 1:03d}.{ext}"
                return str(_write_unique(output, filename, lambda file: file.write(payload)))
            except Exception:
                return None  # requests errors can include a URL with the Bot Token.

        downloaded = {}
        progress("download", 0, len(items))
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending = {pool.submit(download, item): index for index, item in enumerate(items)}
            for done, task in enumerate(as_completed(pending), 1):
                path = task.result()
                if path:
                    downloaded[pending[task]] = path
                progress("download", done, len(items))
        files = [path for _, path in sorted(downloaded.items())]
        failed = len(items) - len(files)
        if mode == "library":
            result = _import(root, files, f"TG_{name}", progress)
            result["failed"] = result.get("failed", 0) + failed
            return {key: value for key, value in result.items() if key != "warnings"}
        destination = target
        if files and request.get("zip_output"):
            def write_zip(stream):
                with ZipFile(stream, "w") as archive:
                    for path in files:
                        archive.write(path, Path(path).name)
            try:
                destination = _write_unique(target, f"{name}.zip", write_zip)
            except OSError:
                return dict(count=len(files), failed=failed, destination=str(target), warnings=["ZIP 打包失败"])
        return dict(count=len(files), failed=failed, destination=str(destination))


def _tg(root, action, request, progress):
    # Explicit errors only: requests and server descriptions may contain the Bot Token.
    try:
        downloader = _telegram(request)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception:
        return {"error": "无法初始化 Telegram 下载"}
    try:
        with closing(downloader.session):
            if action == "tg_parse":
                value = request.get("value") or ""
                if not downloader.parse_sticker_input(value):
                    return {"error": "请输入 Telegram 贴纸包链接或包名"}
                pack = downloader.get_sticker_set(value)
                token = downloader.bot_token
                if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", pack.name):
                    return {"error": "Telegram 返回了无效的贴纸包名"}
                def file_id(value):
                    return value if value and re.fullmatch(r"[A-Za-z0-9_-]+", value) else None
                return dict(source_name=pack.name, source_title=pack.title.replace(token, "***"),
                            source_url=pack.source_url, items=[
                    dict(id=item_id, index=item.index, label=(item.emoji or "").replace(token, "***"),
                         preview_id=file_id(item.thumbnail_file_id), animated=item.is_animated or item.is_video,
                         width=item.width, height=item.height)
                    for item in pack.stickers if (item_id := file_id(item.file_id))])
            if action == "tg_single_package":
                from services.tg_resource_packages import export_single
                return export_single(root, request, progress, downloader)
        if action == "tg_previews":
            def preview(item):
                file_id = item.get("preview_id") or item["id"]
                worker = _telegram(request)
                with closing(worker.session):
                    path = worker.get_file_path(file_id)
                    data = worker.download_file_bytes(path)
                    if path.rsplit(".", 1)[-1].lower() in {"tgs", "webm"}:
                        data, _ = worker.process_sticker_data(data, path.rsplit(".", 1)[-1], "png")
                return _preview(Image.open(BytesIO(data)))
            return _previews(request, preview)
        if action == "tg_detail":
            items = request.get("items") or []
            if not items:
                return {}
            from services.tg_downloader import StickerItem
            item = items[0]
            worker = _telegram(request)
            with closing(worker.session):
                data, ext = worker.download_sticker(StickerItem(index=item["index"], file_id=item["id"], file_unique_id=""),
                                                    "gif" if item.get("animated") else "png")
            return _detail(data, item, animated=ext == "gif")
        if action == "tg_batch_package":
            from services.tg_resource_packages import export_batch
            return export_batch(root, request, progress, lambda: _telegram(request))
        if action == "tg_download":
            return _tg_download(root, request, progress)
    except Exception:
        return {"error": "无法连接 Telegram 或处理贴纸，请检查网络、Bot Token 和贴纸包"}
    return {"error": "未知的 Telegram 操作"}


def run_external(root, action, request, progress):
    if action.startswith("qq_"):
        try:
            return _qq(root, action, request, progress)
        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
            return {"error": "无法读取 QQ 缓存，请检查目录和账号"}
    return _tg(root, action, request, progress)
