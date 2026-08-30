from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image


@dataclass(frozen=True)
class ExportSkip:
    name: str
    reason: str


@dataclass(frozen=True)
class ExportAsset:
    sync_key: str
    asset_path: str
    asset_size: int
    asset_sha256: str
    pixel_md5: Optional[str]
    file_md5: str
    format: str
    is_animated: bool
    width: int
    height: int
    created_at: int
    display_name: str
    quality_score: float
    keywords: List[str]
    category_refs: Tuple[int, ...]
    payload: bytes


class ExchangeExportService:
    """
    只读导出器：
    - 只读取 images_dir / categories / metadata / features.db
    - 不写入任何现有数据库
    - 不调用 storage.py 内任何 save_* / _save_hashes / move_image_to_front
    """

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        self.base_dir = base_dir
        self.data_dir = os.path.join(self.base_dir, "data")
        self.images_dir = os.path.join(self.data_dir, "images")
        self.features_db_path = os.path.join(self.data_dir, "features.db")
        self.metadata_db_path = os.path.join(self.data_dir, "metadata.db")
        self.categories_db_path = os.path.join(self.data_dir, "categories.db")
        self.metadata_file = os.path.join(self.data_dir, "metadata.json")
        self.categories_file = os.path.join(self.data_dir, "categories.json")
        self.app_version = self._read_app_version()

    def export_zip(self, zip_path: str, selected_categories: Optional[List[str]] = None) -> Dict[str, Any]:
        os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)

        categories, warnings = self._load_categories_readonly()
        categories = self._filter_categories(categories, selected_categories)
        metadata = self._load_metadata_readonly()
        quality_map = self._load_quality_scores_readonly()

        category_name_to_ref = {
            self._normalize_name(name): idx for idx, name in enumerate(categories.keys(), start=1)
        }
        filename_to_category_refs = self._build_filename_category_refs(categories, category_name_to_ref)

        resource_map: Dict[str, Dict[str, Any]] = {}
        skipped: List[ExportSkip] = []

        source_files: List[str] = []
        if os.path.exists(self.images_dir):
            source_files = sorted(
                name for name in os.listdir(self.images_dir)
                if os.path.isfile(os.path.join(self.images_dir, name))
            )

        for name in source_files:
            source_path = os.path.join(self.images_dir, name)
            result = self._inspect_and_pack_asset(source_path)
            if isinstance(result, ExportSkip):
                skipped.append(result)
                continue

            refs = filename_to_category_refs.get(result.display_name, ())
            meta_keywords = metadata.get(result.display_name, "")
            meta_quality = quality_map.get(result.display_name, 0.0)

            existing = resource_map.get(result.sync_key)
            if existing is None:
                resource_map[result.sync_key] = {
                    "sync_key": result.sync_key,
                    "asset_path": result.asset_path,
                    "asset_size": result.asset_size,
                    "asset_sha256": result.asset_sha256,
                    "pixel_md5": result.pixel_md5,
                    "file_md5": result.file_md5,
                    "format": result.format,
                    "is_animated": result.is_animated,
                    "width": result.width,
                    "height": result.height,
                    "quality_score": float(meta_quality or 0.0),
                    "keywords": list(meta_keywords),
                    "created_at": result.created_at,
                    "display_name": result.display_name,
                    "category_refs": set(refs),
                    "_payload": result.payload,
                }
                continue

            existing["category_refs"].update(refs)
            existing["keywords"] = self._merge_keyword_lists(existing["keywords"], meta_keywords)
            existing["quality_score"] = max(float(existing["quality_score"]), float(meta_quality or 0.0))
            existing["created_at"] = min(int(existing["created_at"]), int(result.created_at))

        resources: List[Dict[str, Any]] = []
        total_asset_bytes = 0
        for item in resource_map.values():
            category_refs = sorted(item["category_refs"])
            total_asset_bytes += int(item["asset_size"])
            resources.append({
                "sync_key": item["sync_key"],
                "asset_path": item["asset_path"],
                "asset_size": int(item["asset_size"]),
                "asset_sha256": item["asset_sha256"],
                "pixel_md5": item["pixel_md5"],
                "file_md5": item["file_md5"],
                "format": item["format"],
                "is_animated": bool(item["is_animated"]),
                "width": int(item["width"]),
                "height": int(item["height"]),
                "quality_score": float(item["quality_score"]),
                "keywords": item["keywords"],
                "created_at": int(item["created_at"]),
                "display_name": item["display_name"],
                "category_refs": category_refs,
            })

        resources.sort(key=lambda x: (x["created_at"], x["display_name"], x["sync_key"]))

        categories_json = [{"ref": idx, "name": name} for idx, name in enumerate(categories.keys(), start=1)]
        relations_count = sum(len(resource["category_refs"]) for resource in resources)
        manifest: Dict[str, Any] = {
            "format_version": 1,
            "package_id": str(uuid.uuid4()),
            "exporter": "pc",
            "app_version": self.app_version,
            "exported_at": int(datetime.now(timezone.utc).timestamp() * 1000),
            "hash_rules": {
                "static": "p:md5(RGBA bytes)",
                "animated": "f:md5(file bytes)",
            },
            "counts": {
                "resources": len(resources),
                "categories": len(categories_json),
                "relations": relations_count,
                "assets": len(resources),
            },
            "total_asset_bytes": total_asset_bytes,
            "skipped": [{"name": item.name, "reason": item.reason} for item in skipped],
        }
        if warnings:
            manifest["warnings"] = warnings

        catalog = {
            "categories": categories_json,
            "resources": resources,
        }

        with zipfile.ZipFile(
            zip_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("catalog.json", json.dumps(catalog, ensure_ascii=False, indent=2))
            for item in resources:
                zf.writestr(item["asset_path"], resource_map[item["sync_key"]]["_payload"])

        return manifest

    def _inspect_and_pack_asset(self, source_path: str) -> ExportSkip | ExportAsset:
        display_name = os.path.basename(source_path)
        try:
            with open(source_path, "rb") as f:
                data = f.read()
        except Exception as e:
            return ExportSkip(name=display_name, reason=f"read failed: {e}")

        created_at = int(os.path.getmtime(source_path) * 1000)

        if self._is_png_bytes(data):
            try:
                with Image.open(BytesIO(data)) as img:
                    if getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1:
                        return ExportSkip(
                            name=display_name,
                            reason="animated png (apng) not supported in v0",
                        )
                    rgba = img.convert("RGBA")
                    pixel_md5 = self._md5_hex(rgba.tobytes())
                    width, height = rgba.size
                file_md5 = self._md5_hex(data)
                asset_sha256 = self._sha256_hex(data)
                asset_path = self._asset_path_for("p", pixel_md5, ".png")
                return ExportAsset(
                    sync_key=f"p:{pixel_md5}",
                    asset_path=asset_path,
                    asset_size=len(data),
                    asset_sha256=asset_sha256,
                    pixel_md5=pixel_md5,
                    file_md5=file_md5,
                    format="PNG",
                    is_animated=False,
                    width=width,
                    height=height,
                    created_at=created_at,
                    display_name=display_name,
                    quality_score=0.0,
                    keywords=[],
                    category_refs=(),
                    payload=data,
                )
            except Exception as e:
                return ExportSkip(name=display_name, reason=f"png decode failed: {e}")

        if self._is_gif_bytes(data):
            width = 0
            height = 0
            try:
                with Image.open(BytesIO(data)) as img:
                    width, height = img.size
            except Exception:
                width = 0
                height = 0

            file_md5 = self._md5_hex(data)
            asset_path = self._asset_path_for("f", file_md5, ".gif")
            return ExportAsset(
                sync_key=f"f:{file_md5}",
                asset_path=asset_path,
                asset_size=len(data),
                asset_sha256=self._sha256_hex(data),
                pixel_md5=None,
                file_md5=file_md5,
                format="GIF",
                is_animated=True,
                width=width,
                height=height,
                created_at=created_at,
                display_name=display_name,
                quality_score=0.0,
                keywords=[],
                category_refs=(),
                payload=data,
            )

        return ExportSkip(name=display_name, reason=f"unsupported type: {self._sniff_type(data)}")

    def _load_categories_readonly(self) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
        raw_entries: List[Tuple[str, List[str]]] = []

        if os.path.exists(self.categories_db_path):
            try:
                with sqlite3.connect(f"file:{self.categories_db_path}?mode=ro", uri=True) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM categories ORDER BY sort_order ASC, id ASC")
                    names = [row[0] for row in cur.fetchall()]
                    for cat_name in names:
                        cur.execute(
                            "SELECT image_path FROM category_images WHERE category_name = ?",
                            (cat_name,),
                        )
                        files = [os.path.basename(row[0]) for row in cur.fetchall() if row and row[0]]
                        raw_entries.append((cat_name, files))
                    if raw_entries:
                        return self._merge_category_entries(raw_entries)
            except Exception:
                pass

        if os.path.exists(self.categories_file):
            try:
                with open(self.categories_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for cat_name, paths in data.items():
                        if isinstance(paths, list):
                            files = [os.path.basename(p) for p in paths if isinstance(p, str)]
                        else:
                            files = []
                        raw_entries.append((cat_name, files))
            except Exception:
                pass

        return self._merge_category_entries(raw_entries)

    def _merge_category_entries(self, raw_entries: List[Tuple[str, List[str]]]) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
        merged: Dict[str, Dict[str, Any]] = {}
        warnings: List[Dict[str, Any]] = []

        for raw_name, files in raw_entries:
            normalized = self._normalize_name(raw_name)
            if normalized not in merged:
                merged[normalized] = {
                    "name": normalized,
                    "files": [],
                    "seen_raw_names": [raw_name],
                }
            else:
                if raw_name not in merged[normalized]["seen_raw_names"]:
                    merged[normalized]["seen_raw_names"].append(raw_name)

            existing_files = merged[normalized]["files"]
            for filename in files:
                if filename not in existing_files:
                    existing_files.append(filename)

        for normalized, item in merged.items():
            if len(item["seen_raw_names"]) > 1:
                warnings.append(
                    {
                        "type": "category_name_collision",
                        "normalized": normalized,
                        "merged_from": item["seen_raw_names"],
                    }
                )

        return {name: item["files"] for name, item in merged.items()}, warnings

    def _load_metadata_readonly(self) -> Dict[str, str]:
        result: Dict[str, str] = {}

        if os.path.exists(self.metadata_db_path):
            try:
                with sqlite3.connect(f"file:{self.metadata_db_path}?mode=ro", uri=True) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT image_path, keywords FROM image_metadata")
                    for image_path, keywords in cur.fetchall():
                        result[os.path.basename(image_path)] = str(keywords or "")
                    return result
            except Exception:
                pass

        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        result[os.path.basename(k)] = v if isinstance(v, str) else str(v)
            except Exception:
                pass

        return result

    def _load_quality_scores_readonly(self) -> Dict[str, float]:
        result: Dict[str, float] = {}
        if not os.path.exists(self.features_db_path):
            return result

        try:
            with sqlite3.connect(f"file:{self.features_db_path}?mode=ro", uri=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT image_path, quality_score FROM image_features")
                for image_path, quality_score in cur.fetchall():
                    result[os.path.basename(image_path)] = float(quality_score or 0.0)
        except Exception:
            return result

        return result

    def _filter_categories(
        self,
        categories: Dict[str, List[str]],
        selected_categories: Optional[List[str]],
    ) -> Dict[str, List[str]]:
        if not selected_categories:
            return categories

        selected_set = {
            self._normalize_name(name)
            for name in selected_categories
            if str(name).strip()
        }
        if not selected_set:
            return categories

        filtered: Dict[str, List[str]] = {}
        for category_name, filenames in categories.items():
            normalized = self._normalize_name(category_name)
            if normalized in selected_set:
                filtered[normalized] = list(filenames)
        return filtered

    def _build_filename_category_refs(
        self,
        categories: Dict[str, List[str]],
        category_name_to_ref: Dict[str, int],
    ) -> Dict[str, Tuple[int, ...]]:
        filename_to_refs: Dict[str, set[int]] = {}
        for category_name, filenames in categories.items():
            ref = category_name_to_ref.get(self._normalize_name(category_name))
            if ref is None:
                continue
            for filename in filenames:
                filename_to_refs.setdefault(filename, set()).add(ref)
        return {filename: tuple(sorted(refs)) for filename, refs in filename_to_refs.items()}

    def _read_app_version(self) -> str:
        return "unknown"

    def _asset_path_for(self, prefix: str, key: str, ext: str) -> str:
        return f"assets/{prefix}-{key}{ext}"

    @staticmethod
    def _sniff_type(data: bytes) -> str:
        if ExchangeExportService._is_png_bytes(data):
            return "PNG"
        if ExchangeExportService._is_gif_bytes(data):
            return "GIF"
        return f"UNKNOWN({data[:12].hex()})"

    @staticmethod
    def _normalize_name(value: str) -> str:
        return unicodedata.normalize("NFC", str(value)).strip()

    @staticmethod
    def _parse_keywords(value: Any) -> List[str]:
        if value is None:
            return []
        raw = str(value)
        if not raw.strip():
            return []

        result: List[str] = []
        seen: set[str] = set()
        for token in raw.split():
            normalized = unicodedata.normalize("NFC", token).strip()
            if not normalized:
                continue
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @classmethod
    def _merge_keyword_lists(cls, left: List[str], right: Any) -> List[str]:
        merged: List[str] = []
        seen: set[str] = set()
        for token in left + cls._parse_keywords(right):
            normalized = unicodedata.normalize("NFC", str(token)).strip()
            if not normalized:
                continue
            if normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
        return merged

    @staticmethod
    def _md5_hex(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    @staticmethod
    def _sha256_hex(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _is_png_bytes(data: bytes) -> bool:
        return data.startswith(b"\x89PNG\r\n\x1a\n")

    @staticmethod
    def _is_gif_bytes(data: bytes) -> bool:
        return len(data) >= 6 and (data[:6] == b"GIF87a" or data[:6] == b"GIF89a")


def export_resources(
    zip_path: str,
    base_dir: Optional[str] = None,
    selected_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return ExchangeExportService(base_dir=base_dir).export_zip(
        zip_path,
        selected_categories=selected_categories,
    )
