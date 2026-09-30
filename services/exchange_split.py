"""Split existing exchange packages into independently importable, lossless ZIPs."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from services.exchange_import import ExchangeImportService


# Decimal MB also fits websites that interpret their limit as 100 MiB.
MAX_VOLUME_BYTES = 100_000_000


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _entry_size(name, size):
    # Stored ZIP: local header + central directory header + UTF-8 filename twice.
    return size + 76 + 2 * len(name.encode("utf-8"))


def split_package(zip_path, output_dir, progress_callback=None, *, max_bytes=MAX_VOLUME_BYTES):
    """Return a new output directory and ZIP paths; never overwrite the source.

    Images are copied byte for byte, with no image encoder involved. Each volume
    retains the original category names and package ID for normal import merging.
    max_bytes is injectable for boundary tests, but cannot exceed the website cap.
    """
    if not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_VOLUME_BYTES:
        raise ValueError("分卷大小必须在 1 到 100,000,000 字节之间")
    source = Path(zip_path)
    output = Path(output_dir)
    if not source.is_file() or not output.is_dir():
        raise ValueError("请选择有效的资源包和输出文件夹")

    def progress(current, total, message):
        if progress_callback:
            progress_callback(current, total, message)

    service = ExchangeImportService()
    with tempfile.TemporaryDirectory(prefix="suzu-split-source-") as temp:
        staging = Path(temp)
        progress(0, 0, "正在校验资源包")
        service._extract_zip(source, staging, progress_callback)
        manifest = service._load_json(staging / "manifest.json")
        catalog = service._load_json(staging / "catalog.json")
        service._parse_catalog(manifest, catalog, staging)
        service._parse_icons(catalog, staging)
        if service.warnings:
            raise ValueError("资源包图标校验失败：" + "；".join(service.warnings))

        resources = copy.deepcopy(catalog["resources"])
        for resource in resources:
            resource["asset_path"] = resource["asset_path"].replace("\\", "/")
        icons = {}
        for category in catalog["categories"]:
            icon = category.get("icon") or {}
            if icon.get("type") == "image":
                name = icon["asset_path"]
                icons[name] = service._safe_asset_path(staging, name)

        def metadata(items):
            part_manifest = copy.deepcopy(manifest)
            part_manifest.update(
                counts={
                    "resources": len(items), "assets": len(items),
                    "categories": len(catalog["categories"]),
                    "relations": sum(len(r.get("category_refs", [])) for r in items),
                },
                total_asset_bytes=sum(r["asset_size"] for r in items),
                icon_assets=len(icons),
                total_icon_bytes=sum(p.stat().st_size for p in icons.values()),
            )
            part_catalog = dict(catalog, resources=items)
            return {"manifest.json": _json_bytes(part_manifest), "catalog.json": _json_bytes(part_catalog)}

        def entries(items):
            result = dict(icons)
            for resource in items:
                name = resource["asset_path"]
                if name in ("manifest.json", "catalog.json"):
                    raise ValueError("资源路径与清单冲突")
                result[name] = service._safe_asset_path(staging, name)
            return result

        def volume_size(items):
            files = entries(items)
            # Keep classic ZIP limits so the size calculation needs no ZIP64 extras.
            if len(files) + 2 > 65535:
                return max_bytes + 1
            return 22 + sum(_entry_size(n, p.stat().st_size) for n, p in files.items()) + sum(
                _entry_size(n, len(data)) for n, data in metadata(items).items()
            )

        progress(0, 0, "正在计算分卷大小")
        groups = []
        start = 0
        while start < len(resources):
            low, high = start + 1, len(resources)
            end = start
            while low <= high:
                mid = (low + high) // 2
                if volume_size(resources[start:mid]) <= max_bytes:
                    end, low = mid, mid + 1
                else:
                    high = mid - 1
            if end == start:
                name = resources[start].get("display_name", resources[start]["asset_path"])
                raise ValueError(f"图片 {name} 加上资源清单和图标后超过分卷上限，无法在保持图片完整的情况下分卷")
            groups.append(resources[start:end])
            start = end
        if not groups:
            if volume_size([]) > max_bytes:
                raise ValueError("资源清单和图标超过分卷上限")
            groups = [[]]

        # A unique directory prevents overwriting earlier runs or the original ZIP.
        destination = Path(tempfile.mkdtemp(prefix=f"{source.stem[:60]}-volumes-", dir=output))
        paths = []
        try:
            for index, items in enumerate(groups, 1):
                progress(index - 1, len(groups), f"正在写入分卷 {index}/{len(groups)}")
                path = destination / f"{source.stem[:60]}.part{index:03d}.zip"
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
                    for name, data in metadata(items).items():
                        archive.writestr(name, data)
                    for name, asset in entries(items).items():
                        # Explicit ZipInfo avoids inheriting timestamps outside ZIP's range.
                        info = zipfile.ZipInfo(name)
                        info.file_size = asset.stat().st_size
                        with asset.open("rb") as src, archive.open(info, "w") as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                if path.stat().st_size > max_bytes:
                    raise ValueError("分卷文件超过大小上限")
                # Verify the actual output, including byte-level resource integrity.
                with zipfile.ZipFile(path) as archive:
                    for name, asset in entries(items).items():
                        digest = hashlib.sha256()
                        with archive.open(name) as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        if digest.hexdigest() != service._sha256(asset):
                            raise ValueError(f"分卷图片校验失败: {name}")
                paths.append(path)
            progress(len(groups), len(groups), "分卷完成")
            return destination, paths
        except Exception:
            destination.resolve().relative_to(output.resolve())
            shutil.rmtree(destination, ignore_errors=True)
            raise
