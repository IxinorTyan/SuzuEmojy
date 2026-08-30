from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Callable

from PIL import Image
from io import BytesIO


class ExchangeImportError(RuntimeError):
    """Raised when an exchange archive cannot be imported."""


@dataclass(frozen=True)
class _Resource:
    sync_key: str
    asset_path: str
    asset_size: int
    asset_sha256: str
    file_md5: str
    pixel_md5: str | None
    format: str
    is_animated: bool
    width: int
    height: int
    created_at: int
    display_name: str
    quality_score: float
    keywords: tuple[str, ...]
    category_refs: tuple[int, ...]
    source: Path


class ExchangeImportService:
    """Import a ZIP created by ExchangeExportService without using StorageService."""

    FORMAT_VERSION = 1

    def __init__(self, base_dir: str | os.PathLike[str] | None = None) -> None:
        root = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent.parent
        self.base_dir = root
        self.data_dir = root / "data"
        self.images_dir = self.data_dir / "images"

    def import_zip(
        self,
        zip_path: str | os.PathLike[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> tuple[int, int]:
        archive = Path(zip_path)
        if not archive.is_file():
            raise ExchangeImportError(f"ZIP 文件不存在: {archive}")

        staging = Path(tempfile.mkdtemp(prefix="suzu-exchange-import-"))
        connections: list[sqlite3.Connection] = []
        created_files: list[Path] = []
        try:
            total_zip_files = self._extract_zip(archive, staging, progress_callback)
            total_zip_files = max(total_zip_files, 1)
            manifest = self._load_json(staging / "manifest.json")
            catalog = self._load_json(staging / "catalog.json")
            resources, categories = self._parse_catalog(manifest, catalog, staging)

            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.images_dir.mkdir(parents=True, exist_ok=True)
            db_paths = (
                self.data_dir / "features.db",
                self.data_dir / "metadata.db",
                self.data_dir / "categories.db",
                self.data_dir / "order.db",
            )
            for path in db_paths:
                connection = sqlite3.connect(path)
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN")
                connections.append(connection)

            category_ids = self._load_categories(connections[2])
            existing = self._scan_existing()
            next_order = self._next_order(connections[3])

            occupied_names = set()
            if self.images_dir.is_dir():
                for p in self.images_dir.iterdir():
                    if p.is_file():
                        occupied_names.add(p.name)
            for row in connections[0].execute("SELECT image_path FROM image_features").fetchall():
                if row[0]:
                    occupied_names.add(row[0])
            for row in connections[1].execute("SELECT image_path FROM image_metadata").fetchall():
                if row[0]:
                    occupied_names.add(row[0])
            for row in connections[2].execute("SELECT DISTINCT image_path FROM category_images").fetchall():
                if row[0]:
                    occupied_names.add(row[0])
            for row in connections[3].execute("SELECT image_path FROM item_orders").fetchall():
                if row[0]:
                    occupied_names.add(row[0])

            imported = 0
            skipped = 0

            total_resources = len(resources)
            for idx, resource in enumerate(resources):
                if progress_callback:
                    current_step = total_zip_files + int((idx / total_resources) * 3 * total_zip_files) if total_resources > 0 else total_zip_files
                    progress_callback(current_step, total_zip_files * 4, f"正在导入表情: {resource.display_name}")
                existing_path = existing.get(resource.sync_key)
                if existing_path is not None:
                    self._add_category_relations(
                        connections[2], resource, categories, existing_path.name, category_ids
                    )
                    skipped += 1
                    continue

                base_name = resource.display_name
                path_obj = Path(base_name)
                stem = path_obj.stem
                suffix = path_obj.suffix

                candidate_name = base_name
                counter = 1
                while candidate_name in occupied_names:
                    candidate_name = f"{stem}_{counter}{suffix}"
                    counter += 1

                occupied_names.add(candidate_name)
                destination = self.images_dir / candidate_name

                shutil.copy2(resource.source, destination)
                created_files.append(destination)
                image_path = destination.name
                self._insert_feature(connections[0], image_path, resource)
                self._insert_metadata(connections[1], image_path, resource)
                self._add_category_relations(
                    connections[2], resource, categories, image_path, category_ids
                )
                connections[3].execute(
                    "INSERT INTO item_orders (image_path, sort_order) VALUES (?, ?)",
                    (image_path, next_order),
                )
                next_order += 1
                existing[resource.sync_key] = destination
                imported += 1

            for connection in connections:
                connection.commit()
            if progress_callback:
                progress_callback(total_zip_files * 4, total_zip_files * 4, "导入完成")
            return imported, skipped
        except Exception as exc:
            for connection in connections:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            for path in reversed(created_files):
                try:
                    path.unlink()
                except OSError:
                    pass
            if isinstance(exc, ExchangeImportError):
                raise
            raise ExchangeImportError(str(exc)) from exc
        finally:
            for connection in connections:
                connection.close()
            shutil.rmtree(staging, ignore_errors=True)

    def _extract_zip(
        self,
        archive: Path,
        staging: Path,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> int:
        root = staging.resolve()
        with zipfile.ZipFile(archive) as zip_file:
            names: set[str] = set()
            infolist = zip_file.infolist()
            total = len(infolist)
            for idx, info in enumerate(infolist):
                if progress_callback:
                    progress_callback(idx, total * 4, f"正在解压资源: {info.filename}")
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ExchangeImportError("ZIP 包含非法路径")
                if name in names:
                    raise ExchangeImportError(f"ZIP 包含重复路径: {name}")
                names.add(name)
                destination = (staging / name).resolve()
                if os.path.commonpath((str(root), str(destination))) != str(root):
                    raise ExchangeImportError("ZIP 包含非法路径")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(info) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
            return len(names)

    @staticmethod
    def _load_json(path: Path) -> Mapping[str, Any]:
        if not path.is_file():
            raise ExchangeImportError(f"ZIP 缺少 {path.name}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExchangeImportError(f"{path.name} 无法解析") from exc
        if not isinstance(value, Mapping):
            raise ExchangeImportError(f"{path.name} 顶层必须是对象")
        return value

    def _parse_catalog(
        self, manifest: Mapping[str, Any], catalog: Mapping[str, Any], staging: Path
    ) -> tuple[list[_Resource], dict[int, str]]:
        if manifest.get("format_version") != self.FORMAT_VERSION:
            raise ExchangeImportError("不支持的交换格式版本")

        raw_categories = catalog.get("categories")
        raw_resources = catalog.get("resources")
        if not isinstance(raw_categories, list) or not isinstance(raw_resources, list):
            raise ExchangeImportError("catalog.json 缺少 categories 或 resources 列表")

        categories: dict[int, str] = {}
        for item in raw_categories:
            if not isinstance(item, Mapping) or not isinstance(item.get("ref"), int):
                raise ExchangeImportError("分类声明格式错误")
            ref = int(item["ref"])
            name = str(item.get("name", "")).strip()
            if ref in categories or not name:
                raise ExchangeImportError("分类引用无效或重复")
            categories[ref] = name

        resources: list[_Resource] = []
        total_bytes = 0
        relation_count = 0
        for item in raw_resources:
            if not isinstance(item, Mapping):
                raise ExchangeImportError("资源声明格式错误")
            try:
                sync_key = str(item["sync_key"])
                asset_path = str(item["asset_path"]).replace("\\", "/")
                asset_size = int(item["asset_size"])
                asset_sha256 = str(item["asset_sha256"]).lower()
                file_md5 = str(item["file_md5"]).lower()
                pixel_md5 = item.get("pixel_md5")
                pixel_md5 = str(pixel_md5).lower() if pixel_md5 else None
                category_refs_raw = item.get("category_refs", [])
                if not isinstance(category_refs_raw, list):
                    raise ValueError
                category_refs = tuple(int(ref) for ref in category_refs_raw)
                if any(ref not in categories for ref in category_refs):
                    raise ValueError
                source = self._safe_asset_path(staging, asset_path)
                if not source.is_file():
                    raise ValueError
            except (KeyError, TypeError, ValueError, OSError) as exc:
                raise ExchangeImportError("资源声明无效") from exc

            actual_size = source.stat().st_size
            actual_sha256 = self._sha256(source)
            if actual_size != asset_size or actual_sha256 != asset_sha256:
                raise ExchangeImportError(f"资源校验失败: {asset_path}")
            actual = self._inspect(source)
            if actual["file_md5"] != file_md5 or actual["pixel_md5"] != pixel_md5:
                raise ExchangeImportError(f"资源 MD5 校验失败: {asset_path}")
            expected_sync = f"p:{pixel_md5}" if pixel_md5 else f"f:{file_md5}"
            if sync_key != expected_sync:
                raise ExchangeImportError(f"sync_key 校验失败: {asset_path}")

            keywords = item.get("keywords", [])
            if not isinstance(keywords, list):
                raise ExchangeImportError("keywords 必须是列表")
            resources.append(_Resource(
                sync_key, asset_path, asset_size, asset_sha256, file_md5, pixel_md5,
                str(item.get("format", actual["format"])), bool(item.get("is_animated", False)),
                int(item.get("width", actual["width"])), int(item.get("height", actual["height"])),
                int(item.get("created_at", 0)), str(item.get("display_name") or Path(asset_path).name),
                float(item.get("quality_score", 0.0)), tuple(str(k) for k in keywords),
                category_refs, source,
            ))
            total_bytes += asset_size
            relation_count += len(category_refs)

        counts = manifest.get("counts", {})
        if not isinstance(counts, Mapping):
            raise ExchangeImportError("manifest counts 格式错误")
        expected = {
            "resources": len(resources), "assets": len(resources),
            "categories": len(categories), "relations": relation_count,
        }
        for key, value in expected.items():
            if key in counts and int(counts[key]) != value:
                raise ExchangeImportError(f"manifest {key} 数量校验失败")
        if "total_asset_bytes" in manifest and int(manifest["total_asset_bytes"]) != total_bytes:
            raise ExchangeImportError("资源总大小校验失败")
        return resources, categories

    @staticmethod
    def _safe_asset_path(staging: Path, asset_path: str) -> Path:
        path = Path(asset_path)
        if path.is_absolute() or ".." in path.parts:
            raise ExchangeImportError("资源路径非法")
        result = (staging / path).resolve()
        if os.path.commonpath((str(staging.resolve()), str(result))) != str(staging.resolve()):
            raise ExchangeImportError("资源路径非法")
        return result

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _inspect(path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        file_md5 = hashlib.md5(data).hexdigest()
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            with Image.open(BytesIO(data)) as image:
                if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
                    raise ExchangeImportError("APNG 不受支持")
                rgba = image.convert("RGBA")
                return {
                    "file_md5": file_md5,
                    "pixel_md5": hashlib.md5(rgba.tobytes()).hexdigest(),
                    "format": "PNG", "width": rgba.width, "height": rgba.height,
                }
        if data[:6] in (b"GIF87a", b"GIF89a"):
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
            return {
                "file_md5": file_md5, "pixel_md5": None, "format": "GIF",
                "width": width, "height": height,
            }
        raise ExchangeImportError(f"不支持的资源类型: {path.name}")

    def _scan_existing(self) -> dict[str, Path]:
        result: dict[str, Path] = {}
        if not self.images_dir.is_dir():
            return result

        for path in sorted(self.images_dir.iterdir()):
            if not path.is_file():
                continue
            try:
                with path.open("rb") as f:
                    header = f.read(8)

                if header.startswith(b"\x89PNG\r\n\x1a\n"):
                    data = path.read_bytes()
                    with Image.open(BytesIO(data)) as image:
                        if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
                            continue
                        rgba = image.convert("RGBA")
                        pixel_md5 = hashlib.md5(rgba.tobytes()).hexdigest()
                        sync_key = f"p:{pixel_md5}"
                elif header[:6] in (b"GIF87a", b"GIF89a"):
                    data = path.read_bytes()
                    file_md5 = hashlib.md5(data).hexdigest()
                    sync_key = f"f:{file_md5}"
                else:
                    continue

                result.setdefault(sync_key, path)
            except Exception as e:
                print(f"[WARNING] 扫描物理文件去重信息失败: {path.name}, 错误: {e}")
        return result

    def _resource_sync_key(self, path: Path) -> str:
        inspected = self._inspect(path)
        return f"p:{inspected['pixel_md5']}" if inspected["pixel_md5"] else f"f:{inspected['file_md5']}"

    def _new_image_path(self, resource: _Resource) -> Path:
        candidate = self.images_dir / resource.display_name
        return candidate if not candidate.exists() else self.images_dir / self._asset_filename(resource)

    @staticmethod
    def _asset_filename(resource: _Resource) -> str:
        suffix = ".gif" if resource.format.upper() == "GIF" else ".png"
        return f"{resource.sync_key.replace(':', '-')}{suffix}"

    @staticmethod
    def _next_order(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COALESCE(MAX(sort_order), -1) FROM item_orders").fetchone()
        return int(row[0]) + 1

    @staticmethod
    def _load_categories(connection: sqlite3.Connection) -> dict[int, str]:
        rows = connection.execute("SELECT id, name FROM categories ORDER BY sort_order ASC, id ASC").fetchall()
        return {int(row[0]): str(row[1]) for row in rows}

    def _add_category_relations(
        self, connection: sqlite3.Connection, resource: _Resource, categories: dict[int, str],
        image_path: str, category_ids: dict[int, str]
    ) -> None:
        for ref in resource.category_refs:
            name = categories[ref]
            row = connection.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
            if row is None:
                max_order = connection.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) FROM categories"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO categories (name, sort_order) VALUES (?, ?)",
                    (name, int(max_order) + 1),
                )
            connection.execute(
                "INSERT OR IGNORE INTO category_images (category_name, image_path) VALUES (?, ?)",
                (name, image_path),
            )

    @staticmethod
    def _insert_feature(connection: sqlite3.Connection, image_path: str, resource: _Resource) -> None:
        connection.execute(
            "INSERT INTO image_features "
            "(image_path, md5, quality_score) VALUES (?, ?, ?)",
            (image_path, resource.file_md5, resource.quality_score),
        )

    @staticmethod
    def _insert_metadata(connection: sqlite3.Connection, image_path: str, resource: _Resource) -> None:
        connection.execute(
            "INSERT INTO image_metadata (image_path, tags, keywords) VALUES (?, ?, ?)",
            (image_path, "", " ".join(resource.keywords)),
        )


def import_resources(
    zip_path: str | os.PathLike[str],
    base_dir: str | os.PathLike[str] | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[int, int]:
    return ExchangeImportService(base_dir=base_dir).import_zip(zip_path, progress_callback=progress_callback)
