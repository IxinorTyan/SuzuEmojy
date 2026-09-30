"""Opt-in native test: set SUZU_TEST_UPDATER to a compiled updater executable."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from services.update_protocol import MANIFEST, atomic_json, read_manifest, sha256
from test_updates import make_release


@unittest.skipUnless(os.name == "nt" and os.environ.get("SUZU_TEST_UPDATER"), "compiled Windows updater not supplied")
class NativeUpdateTests(unittest.TestCase):
    def test_native_helper_waits_installs_and_restarts_root_launcher(self):
        from build_nuitka import build_launcher_stub, find_csc
        with tempfile.TemporaryDirectory(prefix="Suzu 更新验证 ") as directory:
            root = Path(directory)
            make_release(root)
            build_launcher_stub(str(root))
            core_source = root / "marker.cs"
            core_source.write_text('using System.IO; class Marker { static void Main() { File.WriteAllText(Path.Combine(System.Environment.CurrentDirectory, "restarted.txt"), "ok"); } }', encoding="utf-8")
            subprocess.run([find_csc(), "/nologo", "/target:winexe", "/out:" + str(root / "bin/SuzuEmojy.exe"), str(core_source)], check=True, capture_output=True)
            helper = Path(os.environ["SUZU_TEST_UPDATER"]).resolve()
            shutil.copy2(helper, root / "SuzuEmojyUpdater.exe")
            manifest = read_manifest(root)
            manifest["files"] = {name: sha256(root / name) for name in manifest["files"]}
            atomic_json(root / MANIFEST, manifest)
            work = root / ".update"
            stage = work / "stage-native"
            make_release(stage, "1.14.0")
            for name in ("SuzuEmojy.exe", "SuzuEmojyUpdater.exe", "bin/SuzuEmojy.exe"):
                shutil.copy2(root / name, stage / name)
            manifest = read_manifest(stage)
            manifest["files"] = {name: sha256(stage / name) for name in manifest["files"]}
            atomic_json(stage / MANIFEST, manifest)
            atomic_json(work / "ready.json", {"version": "1.14.0", "directory": "stage-native"})
            data = root / "bin/data/config.json"
            data.parent.mkdir(parents=True)
            data.write_text("user data", encoding="utf-8")
            # A separate application process calls the real service, then exits.
            script = "from pathlib import Path; import sys; from services.update_service import UpdateService; UpdateService(Path(sys.argv[1])).launch_installer()"
            app = subprocess.Popen([sys.executable, "-c", script, str(root)], creationflags=subprocess.CREATE_NO_WINDOW,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = app.communicate(timeout=45)
            self.assertEqual(app.returncode, 0, stderr.decode(errors="replace"))
            deadline = time.monotonic() + 30
            while not (root / "restarted.txt").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue((root / "restarted.txt").exists(), "native restart marker missing")
            self.assertEqual(read_manifest(root, True)["version"], "1.14.0")
            self.assertEqual(data.read_text(), "user data")
            self.assertFalse((work / "journal.json").exists())
            self.assertFalse((work / "ready.json").exists())
            time.sleep(1)  # Let the short-lived Windows launcher release file handles.


if __name__ == "__main__":
    unittest.main()
