import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from services.update_protocol import MANIFEST, atomic_json, child, read_manifest, safe_name, sha256, version_key
from services.update_service import Cancelled, UpdateService
import updater


def make_release(root, version="1.13.0", extra=None, flavor="standalone"):
    root.mkdir(parents=True, exist_ok=True)
    files = {"SuzuEmojy.exe": b"launcher", "SuzuEmojyUpdater.exe": b"updater",
             "bin/SuzuEmojy.exe" if flavor == "standalone" else "main.py": version.encode(),
             "version.json": json.dumps({"version": version, "channel": "stable"}).encode()}
    files.update(extra or {})
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    atomic_json(root / MANIFEST, {"protocol": 1, "version": version, "flavor": flavor,
                                "files": {n: sha256(root / n) for n in files}})


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "应用 with spaces"
        make_release(self.root, extra={"bin/removed.py": b"old"})
        self.service = UpdateService(self.root)
        self.stage = self.service.workspace / "stage-test"
        make_release(self.stage, "1.14.0", {"bin/new.py": b"new"})

    def tearDown(self):
        self.temp.cleanup()

    def test_numeric_versions(self):
        self.assertGreater(version_key("v1.13.0"), version_key("1.9.9"))
        for invalid in ("1.13", "1.13.0-fix", "01.2.3", "1.2.3-beta.1"):
            with self.assertRaises(ValueError):
                version_key(invalid)

    def test_protected_and_traversal_paths(self):
        for name in ("../file", "/tmp/file", "bin/../../file", "bin/data/config.json", "DATA/x", "runtime/x",
                     "C:/file", "a\\b", "bin/file:stream", "bin/NUL.txt", "bin/file.", "x//y"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)

    def test_success_preserves_data_and_unowned_files(self):
        for name in ("data/config.json", "bin/data/images/user.png", "runtime/local.txt", "custom.txt"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"user")
        updater.install(self.root, self.stage)
        self.assertEqual(read_manifest(self.root, True)["version"], "1.14.0")
        self.assertFalse((self.root / "bin/removed.py").exists())
        for name in ("data/config.json", "bin/data/images/user.png", "runtime/local.txt", "custom.txt"):
            self.assertEqual((self.root / name).read_bytes(), b"user")

    def test_failure_mid_install_rolls_back_all_files(self):
        original = updater.replace_file
        failed = False

        def fail_once(source, target, work):
            nonlocal failed
            if str(source).startswith(str(self.stage)) and target.name == "new.py" and not failed:
                failed = True
                raise PermissionError("simulated locked file")
            original(source, target, work)

        with patch.object(updater, "replace_file", side_effect=fail_once), self.assertRaises(PermissionError):
            updater.install(self.root, self.stage)
        self.assertEqual(read_manifest(self.root, True)["version"], "1.13.0")
        self.assertTrue((self.root / "bin/removed.py").is_file())
        self.assertFalse((self.root / "bin/new.py").exists())
        self.assertFalse((self.service.workspace / "journal.json").exists())

    def test_interrupted_replacement_recovers_next_launch(self):
        original = updater.replace_file

        def power_loss(source, target, work):
            original(source, target, work)
            raise KeyboardInterrupt("simulated power failure")

        with patch.object(updater, "replace_file", side_effect=power_loss), self.assertRaises(KeyboardInterrupt):
            updater.install(self.root, self.stage)
        self.assertTrue((self.service.workspace / "journal.json").exists())
        updater.recover(self.root)
        self.assertEqual(read_manifest(self.root, True)["version"], "1.13.0")

    def test_unowned_collision_aborts_without_modifying_files(self):
        (self.root / "bin/new.py").write_bytes(b"user file")
        with self.assertRaises(ValueError):
            updater.install(self.root, self.stage)
        self.assertEqual((self.root / "bin/new.py").read_bytes(), b"user file")
        self.assertEqual(read_manifest(self.root, True)["version"], "1.13.0")

    def test_file_to_directory_transition_aborts_before_install(self):
        make_release(self.stage, "1.14.0", {"bin/removed.py/child.txt": b"new"})
        with self.assertRaises(ValueError):
            updater.install(self.root, self.stage)
        self.assertEqual(read_manifest(self.root, True)["version"], "1.13.0")
        self.assertFalse((self.service.workspace / "journal.json").exists())

    def test_damaged_package_aborts_before_replacement(self):
        (self.stage / "bin/new.py").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            updater.install(self.root, self.stage)
        self.assertEqual(read_manifest(self.root, True)["version"], "1.13.0")

    def test_flavor_change_rejected(self):
        manifest = json.loads((self.stage / MANIFEST).read_text())
        manifest["flavor"] = "lightweight"
        atomic_json(self.stage / MANIFEST, manifest)
        with self.assertRaises(ValueError):
            updater.install(self.root, self.stage)

    def zip_stage(self, prefix=""):
        archive = Path(self.temp.name) / "test.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for file in self.stage.rglob("*"):
                if file.is_file():
                    output.write(file, prefix + file.relative_to(self.stage).as_posix())
        return archive

    def test_extract_flat_and_wrapped_packages(self):
        for i, prefix in enumerate(("", "SuzuEmojy_Release/")):
            target = self.service.workspace / str(i)
            target.mkdir()
            self.service.extract(self.zip_stage(prefix), target)
            self.assertEqual(read_manifest(target, True)["version"], "1.14.0")

    def test_archive_cannot_write_user_data_or_outside_stage(self):
        for i, name in enumerate(("../escape.txt", "bin/data/config.json", "unexpected.txt")):
            archive = self.zip_stage()
            with zipfile.ZipFile(archive, "a") as output:
                output.writestr(name, "bad")
            target = self.service.workspace / ("unsafe" + str(i))
            target.mkdir()
            with self.assertRaises(ValueError):
                self.service.extract(archive, target)
        self.assertFalse((self.service.workspace / "escape.txt").exists())

    def test_cancel_prevents_network_request(self):
        self.service.cancel.set()
        with patch("services.update_service.requests.get") as get, self.assertRaises(Cancelled):
            self.service.check()
        get.assert_not_called()

    def test_check_selects_fixed_asset_and_compares_tag(self):
        release = {"tag_name": "v1.14.0", "assets": [{"name": "SuzuEmojy_Release.zip", "state": "uploaded", "size": 100,
                   "digest": "sha256:" + "a" * 64, "browser_download_url": "https://github.com/IxinorTyan/SuzuEmojy/releases/download/v1.14.0/SuzuEmojy_Release.zip"}]}
        with patch("services.update_service.requests.get") as get:
            get.return_value.__enter__.return_value.json.return_value = release
            self.assertEqual(self.service.check()["version"], "1.14.0")
            release["tag_name"] = "v1.12.3"
            self.assertIsNone(self.service.check())
            release["tag_name"] = "v1.14.0"
            release["assets"][0]["digest"] = None
            with self.assertRaises(ValueError):
                self.service.check()

    def test_download_hash_mismatch_leaves_installation_intact(self):
        release = {"url": "https://example.invalid/update", "size": 4, "sha256": "a" * 64, "version": "1.14.0"}
        with patch("services.update_service.requests.get") as get:
            response = get.return_value.__enter__.return_value
            response.url = release["url"]
            response.iter_content.return_value = [b"oops"]
            with self.assertRaises(ValueError):
                self.service.download(release, lambda value: None)
        self.assertEqual(read_manifest(self.root, True)["version"], "1.13.0")
        self.assertFalse((self.service.workspace / "ready.json").exists())
        self.assertFalse(list(self.service.workspace.glob("*.part")))

    def test_ready_download_survives_new_service_without_installing(self):
        archive = self.zip_stage()
        content = archive.read_bytes()
        release = {"url": "https://example.invalid/update", "size": len(content),
                   "sha256": hashlib.sha256(content).hexdigest(), "version": "1.14.0"}
        with patch("services.update_service.requests.get") as get:
            response = get.return_value.__enter__.return_value
            response.url = release["url"]
            response.iter_content.return_value = [content]
            prepared = self.service.download(release, lambda value: None)
        self.assertEqual(UpdateService(self.root).ready(), prepared)
        self.assertEqual(read_manifest(self.root, True)["version"], "1.13.0")


if __name__ == "__main__":
    unittest.main()
