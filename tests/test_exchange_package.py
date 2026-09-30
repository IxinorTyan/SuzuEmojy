from contextlib import closing
import json
import random
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from services.exchange_export import ExchangeExportService
from services.exchange_import import ExchangeImportService
from services.exchange_icons import resolve_icon_path, portable_icon_path
from services.exchange_split import MAX_VOLUME_BYTES, split_package


class ExchangePackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.make_library("source")
        self.target = self.make_library("target")
        Image.new("RGBA", (4, 3), "red").save(self.source / "data/images/emoji.png")
        Image.new("RGBA", (3, 4), "blue").save(self.source / "data/images/icon.png")
        self.sql(self.source, "categories", "INSERT INTO categories(name, icon_path) VALUES (?, ?)", ("收藏", "icon.png"))
        self.sql(self.source, "categories", "INSERT INTO categories(name, icon_path) VALUES (?, ?)", ("空夹", "🌸"))
        self.sql(self.source, "categories", "INSERT INTO category_images VALUES (?, ?)", ("收藏", "emoji.png"))
        self.sql(self.source, "metadata", "INSERT INTO image_metadata VALUES (?, ?, ?)", ("emoji.png", "", "可爱 happy 可爱"))

    def make_library(self, name):
        root = self.root / name
        (root / "data/images").mkdir(parents=True)
        schemas = {
            "categories": "CREATE TABLE categories(id INTEGER PRIMARY KEY, name TEXT UNIQUE, icon_path TEXT, sort_order INTEGER DEFAULT 0); CREATE TABLE category_images(category_name TEXT, image_path TEXT, PRIMARY KEY(category_name,image_path));",
            "metadata": "CREATE TABLE image_metadata(image_path TEXT PRIMARY KEY, tags TEXT, keywords TEXT);",
            "features": "CREATE TABLE image_features(image_path TEXT PRIMARY KEY, md5 TEXT, quality_score REAL);",
            "order": "CREATE TABLE item_orders(image_path TEXT PRIMARY KEY, sort_order INTEGER);",
        }
        for db, schema in schemas.items():
            with closing(sqlite3.connect(root / f"data/{db}.db")) as connection, connection:
                connection.executescript(schema)
        return root

    def sql(self, root, db, query, args=()):
        with closing(sqlite3.connect(root / f"data/{db}.db")) as connection, connection:
            return connection.execute(query, args).fetchall()

    def export(self, selected=None):
        path = self.root / "package.zip"
        ExchangeExportService(str(self.source)).export_zip(str(path), selected)
        return path

    def catalog(self, path):
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read("catalog.json"))

    def test_all_round_trip_and_duplicate_tag_merge(self):
        path = self.export()
        catalog = self.catalog(path)
        emoji = next(r for r in catalog["resources"] if r["display_name"] == "emoji.png")
        self.assertEqual(emoji["keywords"], ["可爱", "happy"])
        service = ExchangeImportService(self.target)
        self.assertEqual(service.import_zip(path), (2, 0))
        self.sql(self.target, "metadata", "UPDATE image_metadata SET keywords = '本地 可爱' WHERE image_path = 'emoji.png'")
        self.assertEqual(service.import_zip(path), (0, 2))
        self.assertEqual(self.sql(self.target, "metadata", "SELECT keywords FROM image_metadata WHERE image_path='emoji.png'")[0][0], "本地 可爱 happy")
        icons = dict(self.sql(self.target, "categories", "SELECT name, icon_path FROM categories"))
        self.assertEqual(icons["空夹"], "🌸")
        icon_path = resolve_icon_path(self.target / "data", icons["收藏"])
        self.assertTrue(icon_path.is_file())
        self.assertEqual(portable_icon_path(self.target / "data", str(icon_path)), icons["收藏"])
        second = self.root / "again.zip"
        ExchangeExportService(str(self.target)).export_zip(str(second))
        self.assertEqual(len(self.catalog(second)["categories"]), 2)
        self.assertEqual(len(list((self.target / "data/images").iterdir())), 2)

    def test_explicit_package_id_and_category_scope(self):
        path = self.root / "custom.zip"
        ExchangeExportService(str(self.source)).export_zip(
            str(path), selected_categories=["收藏"], package_id="my-pack-001",
        )
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["package_id"], "my-pack-001")
        self.assertEqual(manifest["export_scope"], "categories")
        self.assertEqual([c["name"] for c in self.catalog(path)["categories"]], ["收藏"])
        ExchangeImportService(self.target).import_zip(path)

    def test_default_package_id_remains_unique(self):
        with zipfile.ZipFile(self.export()) as archive:
            first = json.loads(archive.read("manifest.json"))["package_id"]
        with zipfile.ZipFile(self.export()) as archive:
            second = json.loads(archive.read("manifest.json"))["package_id"]
        self.assertNotEqual(first, second)

    def test_selected_excludes_tags_and_carries_external_icon(self):
        path = self.export(["收藏", "空夹"])
        catalog = self.catalog(path)
        self.assertEqual(len(catalog["resources"]), 1)
        self.assertNotIn("keywords", catalog["resources"][0])
        ExchangeImportService(self.target).import_zip(path)
        self.assertEqual(len(list((self.target / "data/images").iterdir())), 1)
        self.assertEqual(self.sql(self.target, "metadata", "SELECT keywords FROM image_metadata"), [("",)])
        self.assertEqual(self.sql(self.target, "categories", "SELECT COUNT(*) FROM categories")[0][0], 2)

    def test_existing_icon_and_tags_are_preserved(self):
        ExchangeImportService(self.target).import_zip(self.export())
        self.sql(self.target, "categories", "UPDATE categories SET icon_path='⭐' WHERE name='收藏'")
        self.sql(self.target, "metadata", "UPDATE image_metadata SET keywords='本地' WHERE image_path='emoji.png'")
        ExchangeImportService(self.target).import_zip(self.export(["收藏"]))
        self.assertEqual(self.sql(self.target, "categories", "SELECT icon_path FROM categories WHERE name='收藏'"), [("⭐",)])
        self.assertEqual(self.sql(self.target, "metadata", "SELECT keywords FROM image_metadata WHERE image_path='emoji.png'"), [("本地",)])

    def test_empty_category_only_package(self):
        self.assertEqual(ExchangeImportService(self.target).import_zip(self.export(["空夹"])), (0, 0))
        self.assertEqual(self.sql(self.target, "categories", "SELECT name, icon_path FROM categories"), [("空夹", "🌸")])

    def test_missing_icon_is_warning_not_resource_failure(self):
        original = self.export(["收藏"])
        broken = self.root / "broken.zip"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(broken, "w") as target:
            for name in source.namelist():
                if not name.startswith("icons/"):
                    target.writestr(name, source.read(name))
        service = ExchangeImportService(self.target)
        self.assertEqual(service.import_zip(broken), (1, 0))
        self.assertEqual(len(service.warnings), 1)

    def test_legacy_package_without_extensions(self):
        original = self.export()
        legacy = self.root / "legacy.zip"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(legacy, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(data)
                    for key in ("export_scope", "includes_keywords", "icon_assets", "total_icon_bytes"):
                        manifest.pop(key)
                    data = json.dumps(manifest)
                if name == "catalog.json":
                    catalog = json.loads(data)
                    for category in catalog["categories"]:
                        category.pop("icon", None)
                    data = json.dumps(catalog)
                if not name.startswith("icons/"):
                    target.writestr(name, data)
        self.assertEqual(ExchangeImportService(self.target).import_zip(legacy), (2, 0))

    def test_split_lossless_and_import_in_reverse_order(self):
        rng = random.Random(42)
        for index in range(3):
            name = f"图片{index}.png"
            Image.frombytes("RGB", (90, 90), rng.randbytes(90 * 90 * 3)).save(self.source / "data/images" / name)
            self.sql(self.source, "categories", "INSERT INTO category_images VALUES (?, ?)", ("收藏", name))
        frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
        frames[0].save(self.source / "data/images/animated.gif", save_all=True, append_images=frames[1:], duration=120, loop=0)
        original = self.export()
        original_bytes = original.read_bytes()
        directory, volumes = split_package(original, self.root, max_bytes=32_000)
        self.assertGreater(len(volumes), 1)
        expected, actual = {}, {}
        with zipfile.ZipFile(original) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for item in self.catalog(original)["resources"]:
                expected[item["asset_path"]] = archive.read(item["asset_path"])
        for volume in volumes:
            self.assertLessEqual(volume.stat().st_size, 32_000)
            self.assertEqual(volume.parent, directory)
            with zipfile.ZipFile(volume) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(json.loads(archive.read("manifest.json"))["package_id"], manifest["package_id"])
                for resource in self.catalog(volume)["resources"]:
                    self.assertNotIn(resource["asset_path"], actual)
                    actual[resource["asset_path"]] = archive.read(resource["asset_path"])
        self.assertEqual(actual, expected)
        self.assertEqual(original.read_bytes(), original_bytes)
        service = ExchangeImportService(self.target)
        self.assertEqual(sum(service.import_zip(p)[0] for p in reversed(volumes)), len(expected))
        self.assertEqual(sum(service.import_zip(p)[0] for p in volumes), 0)
        self.assertEqual(self.sql(self.target, "categories", "SELECT COUNT(*) FROM categories"), [(2,)])
        self.assertEqual(self.sql(self.target, "categories", "SELECT COUNT(*) FROM category_images WHERE category_name='收藏'"), [(4,)])
        for source in (self.source / "data/images").iterdir():
            self.assertEqual((self.target / "data/images" / source.name).read_bytes(), source.read_bytes())
        self.assertEqual(self.sql(self.target, "metadata", "SELECT keywords FROM image_metadata WHERE image_path='emoji.png'"), [("可爱 happy",)])
        self.assertFalse(service.warnings)

    def test_split_exact_size_boundary_and_unique_destinations(self):
        original = self.export()
        directory, paths = split_package(original, self.root)
        self.assertEqual(MAX_VOLUME_BYTES, 100_000_000)
        self.assertEqual(len(paths), 1)
        exact = paths[0].stat().st_size
        second, paths2 = split_package(original, self.root, max_bytes=exact)
        self.assertNotEqual(directory, second)
        self.assertEqual(paths2[0].stat().st_size, exact)
        _, paths3 = split_package(original, self.root, max_bytes=exact - 1)
        self.assertGreater(len(paths3), 1)
        self.assertTrue(all(p.stat().st_size < exact for p in paths3))

    def test_split_empty_package_keeps_categories_and_icons(self):
        _, paths = split_package(self.export(["空夹"]), self.root)
        self.assertEqual(len(paths), 1)
        self.assertEqual(ExchangeImportService(self.target).import_zip(paths[0]), (0, 0))
        self.assertEqual(self.sql(self.target, "categories", "SELECT name, icon_path FROM categories"), [("空夹", "🌸")])

    def test_split_rejects_too_small_limit_without_output(self):
        original = self.export()
        before = set(self.root.iterdir())
        with self.assertRaisesRegex(ValueError, "超过分卷上限"):
            split_package(original, self.root, max_bytes=100)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_split_rejects_damaged_image(self):
        original = self.export()
        broken = self.root / "broken.zip"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(broken, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                target.writestr(name, data + b"damage" if name.startswith("assets/") else data)
        before = set(self.root.iterdir())
        with self.assertRaisesRegex(Exception, "校验失败"):
            split_package(broken, self.root)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_split_cleans_partial_output_on_failure(self):
        original = self.export()
        _, paths = split_package(original, self.root)
        limit = paths[0].stat().st_size - 1
        before = set(self.root.iterdir())

        def fail_after_first_volume(current, total, message):
            if current == 1 and message.startswith("正在写入分卷"):
                raise OSError("模拟写入失败")

        with self.assertRaisesRegex(OSError, "模拟写入失败"):
            split_package(original, self.root, fail_after_first_volume, max_bytes=limit)
        self.assertEqual(set(self.root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
