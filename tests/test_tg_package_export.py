import json
import gc
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
import sqlite3
from contextlib import closing

from PIL import Image

from fluent_ui.views.tg_sticker_view import ImportPackThread
from test_storage_single_frame_gif import _make_storage


class TGPackageExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.temp.name)
        self.storage = _make_storage(self.root)
        self.output = self.root / "tg-001.zip"
        payload = BytesIO()
        Image.new("RGB", (12, 10), "red").save(payload, "BMP")
        self.downloader = SimpleNamespace(
            get_file_path=lambda _: "sticker.bmp",
            download_file_bytes=lambda _: payload.getvalue(),
        )

    def worker(self):
        worker = ImportPackThread(
            self.downloader, self.storage,
            [SimpleNamespace(index=i, file_id=str(i)) for i in range(2)],
            "tg-001", export_path=str(self.output),
        )
        self.errors = []
        self.finished = []
        worker.failed.connect(self.errors.append)
        worker.finished_all.connect(lambda *args: self.finished.append(args))
        return worker

    def test_download_clean_deduplicate_and_export_named_category(self):
        self.storage.add_category("unrelated")
        self.worker().run()
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.finished), 1)
        with zipfile.ZipFile(self.output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            catalog = json.loads(archive.read("catalog.json"))
            self.assertEqual(manifest["package_id"], "tg-001")
            self.assertEqual(manifest["export_scope"], "categories")
            self.assertEqual(catalog["categories"], [{"ref": 1, "name": "tg-001"}])
            self.assertEqual(len(catalog["resources"]), 1)
            resource = catalog["resources"][0]
            self.assertEqual(resource["format"], "PNG")
            self.assertEqual(resource["category_refs"], [1])
            self.assertTrue(archive.read(resource["asset_path"]).startswith(b"\x89PNG"))
        self.assertTrue(self.storage.get_images_by_category("tg-001"))

    def test_failed_cleaning_preserves_existing_output(self):
        self.output.write_bytes(b"existing")
        self.storage.save_file = lambda _, **kwargs: (None, False)
        self.worker().run()
        self.assertTrue(self.errors)
        self.assertFalse(self.finished)
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_cancel_during_packaging_preserves_existing_output(self):
        self.output.write_bytes(b"existing")
        worker = self.worker()
        worker.progress.connect(lambda done, total, msg: worker.cancel() if "正在解析表情" in msg else None)
        worker.run()
        self.assertEqual(self.errors, [])
        self.assertFalse(self.finished)
        self.assertEqual(self.output.read_bytes(), b"existing")
        self.assertEqual(list(self.root.glob("tg_package_*")), [])

    def test_transient_windows_lock_is_retried(self):
        real_replace = os.replace
        attempts = []

        def replace(source, target):
            if str(target) == self.storage.hashes_file:
                attempts.append(target)
                if len(attempts) < 3:
                    error = PermissionError("busy")
                    error.winerror = 5
                    raise error
            return real_replace(source, target)

        with patch("services.storage.os.replace", side_effect=replace), patch("services.storage.time.sleep"):
            self.worker().run()
        self.assertEqual(self.errors, [])
        self.assertTrue(self.output.exists())
        self.assertGreaterEqual(len(attempts), 3)
        self.assertEqual(len(list(Path(self.storage.images_dir).iterdir())), 1)

    def test_persistent_lock_then_retry_repairs_orphan(self):
        self.output.write_bytes(b"existing")
        real_replace = os.replace
        attempts = []

        def replace(source, target):
            if str(target) == self.storage.hashes_file:
                attempts.append(target)
                error = PermissionError("busy")
                error.winerror = 5
                raise error
            return real_replace(source, target)

        with patch("services.storage.os.replace", side_effect=replace), patch("services.storage.time.sleep"):
            self.worker().run()
        self.assertTrue(self.errors)
        self.assertIn("索引保存失败", self.errors[0])
        self.assertEqual(len(attempts), 10)  # 两张贴纸各最多尝试五次
        self.assertEqual(self.output.read_bytes(), b"existing")
        self.assertEqual(len(list(Path(self.storage.images_dir).iterdir())), 1)
        self.assertFalse(self.storage.get_images_by_category("tg-001"))
        self.assertEqual(list(Path(self.storage.data_dir).glob("*.tmp")), [])
        self.worker().run()
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.storage.get_images_by_category("tg-001")), 1)
        with closing(sqlite3.connect(self.storage.features_db_path)) as conn:
            rows = conn.execute("SELECT md5, sync_key FROM image_features").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][0])
        self.assertTrue(rows[0][1].startswith("p:"))

    def test_category_write_failure_is_not_success_and_retry_recovers(self):
        real_save = self.storage.save_categories

        def save(categories, strict=False):
            if strict:
                raise OSError("category database unavailable")
            return real_save(categories)

        with patch.object(self.storage, "save_categories", side_effect=save):
            self.worker().run()
        self.assertTrue(self.errors)
        self.assertIn("加入收藏夹失败", self.errors[0])
        self.assertFalse(self.output.exists())
        self.worker().run()
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.storage.get_images_by_category("tg-001")), 1)

    def test_feature_db_initialization_does_not_migrate_live_json(self):
        from services.feature_db import FeatureDB
        with patch.object(FeatureDB, "check_and_migrate_from_json") as migrate:
            FeatureDB(self.storage.features_db_path)
        migrate.assert_not_called()

    def test_restart_recovers_static_and_animated_orphans(self):
        for animated in (False, True):
            with self.subTest(animated=animated):
                orphan_root = self.root / str(animated)
                storage = _make_storage(orphan_root)
                payload = BytesIO()
                first = Image.new("RGB", (8, 8), "red")
                if animated:
                    first.save(payload, "GIF", save_all=True,
                               append_images=[Image.new("RGB", (8, 8), "blue")], duration=100)
                else:
                    first.save(payload, "PNG")
                source = orphan_root / "source.dat"
                source.write_bytes(payload.getvalue())
                with patch.object(storage, "_save_hashes", side_effect=PermissionError("busy")):
                    with self.assertRaisesRegex(RuntimeError, "索引保存失败"):
                        storage.save_file(str(source), strict=True)
                storage = _make_storage(orphan_root)
                path, duplicate = storage.save_file(str(source), strict=True)
                self.assertTrue(duplicate)
                self.assertEqual(storage.add_image_to_category(path, "recovered", strict=True), "success")
                self.assertEqual(len(list(Path(storage.images_dir).iterdir())), 1)

    def test_actual_category_db_failure_propagates(self):
        source = self.root / "source.bmp"
        source.write_bytes(self.downloader.download_file_bytes(""))
        path, _ = self.storage.save_file(str(source), strict=True)
        real_connect = sqlite3.connect

        def connect(database, *args, **kwargs):
            if str(database) == self.storage.categories_db_path:
                raise sqlite3.OperationalError("locked")
            return real_connect(database, *args, **kwargs)

        self.storage.get_all_categories()
        with patch("services.storage.sqlite3.connect", side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                self.storage.add_image_to_category(path, "recovered", strict=True)
        self.storage.add_image_to_category(path, "recovered", strict=True)
        with closing(real_connect(self.storage.categories_db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM category_images").fetchone()[0], 1)
