"""Build-cache isolation, invalidation, and unchanged release compression."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_nuitka as build
from scripts import package_release as packaging


class BuildPackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("updater.py", "services/update_protocol.py", "scripts/package_release.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# original\n", encoding="utf-8")
        (self.root / "version.json").write_text(json.dumps({"version": "1.2.3", "channel": "stable"}), encoding="utf-8")
        self.release = self.root / "dist" / "release"
        self.release.mkdir(parents=True)
        self.enter = contextlib.ExitStack()
        self.addCleanup(self.enter.close)
        self.enter.enter_context(patch.object(build, "ROOT", self.root))
        self.enter.enter_context(patch.object(packaging, "ROOT", self.root))
        self.enter.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def fake_compile(self, command, **kwargs):
        output = Path(command[command.index("--distpath") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "SuzuEmojyUpdater.exe").write_bytes(b"updater-binary" * 100)

    def test_stale_release_dependencies_removed_but_intermediates_retained(self):
        stale = self.root / "dist" / "main.dist" / "unused.dll"
        intermediate = self.root / "dist" / "main.build" / "state"
        for path in (stale, intermediate):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("old")
        build.prepare_build_directory()
        self.assertFalse(stale.exists())
        self.assertTrue(intermediate.exists())
        build.prepare_build_directory(clean=True)
        self.assertFalse(intermediate.exists())

    def test_concurrency_uses_ram_budget_and_supports_override(self):
        with patch.object(build, "memory_capacity", return_value=16 * 1024 ** 3), patch.object(build.os, "cpu_count", return_value=16):
            self.assertEqual(build.parse_args([]).jobs, 6)
            self.assertEqual(build.parse_args(["--jobs", "12"]).jobs, 12)
        with patch.object(build, "memory_capacity", return_value=2 * 1024 ** 3):
            self.assertEqual(build.default_jobs(), 1)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build.parse_args(["--jobs", "0"])

    def test_compiler_optimizations_and_existing_caches_preserved(self):
        command = build.nuitka_command(6, "ffmpeg.exe")
        for option in ("--jobs=6", "--standalone", "--noinclude-qt-translations", "--python-flag=no_docstrings", "--nofollow-import-to=numpy"):
            self.assertIn(option, command)
        self.assertFalse(any(option.startswith(("--lto=", "--disable-cache", "--clean-cache")) for option in command))
        with patch.dict(os.environ, {"CFLAGS": "-O0", "ZIG_GLOBAL_CACHE_DIR": "persist", "NUITKA_CACHE_DIR": "persist-nuitka"}):
            env = build.build_environment()
        self.assertEqual(env["CFLAGS"], "-march=x86_64_v3")
        self.assertEqual(env["ZIG_GLOBAL_CACHE_DIR"], "persist")
        self.assertEqual(env["NUITKA_CACHE_DIR"], "persist-nuitka")

    def test_updater_cache_survives_release_recreation(self):
        with patch.object(packaging.subprocess, "run", side_effect=self.fake_compile) as run:
            packaging.build_updater(self.release)
            original = (self.release / "SuzuEmojyUpdater.exe").read_bytes()
            (self.release / "SuzuEmojyUpdater.exe").unlink()
            packaging.build_updater(self.release)
        self.assertIn("--clean", run.call_args_list[0].args[0])
        self.assertNotIn("--clean", run.call_args_list[1].args[0])
        self.assertEqual((self.release / "SuzuEmojyUpdater.exe").read_bytes(), original)

    def test_changed_source_or_tools_invalidates_updater_cache(self):
        with patch.object(packaging.subprocess, "run", side_effect=self.fake_compile) as run:
            packaging.build_updater(self.release)
            source = self.root / "services" / "update_protocol.py"
            source.write_text("# changed\n")
            os.utime(source, (1, 1))  # A restored old timestamp must not mask edits.
            packaging.build_updater(self.release)
            self.assertIn("--clean", run.call_args.args[0])
            with patch.object(packaging, "distribution_version", return_value="new-tool"):
                packaging.build_updater(self.release)
            self.assertIn("--clean", run.call_args.args[0])
            packaging.build_updater(self.release, clean=True)
            self.assertIn("--clean", run.call_args.args[0])

    def test_failed_updater_build_does_not_ship_cached_executable(self):
        with patch.object(packaging.subprocess, "run", side_effect=self.fake_compile):
            packaging.build_updater(self.release)
        (self.release / "SuzuEmojyUpdater.exe").unlink()
        with patch.object(packaging.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "PyInstaller")):
            with self.assertRaises(subprocess.CalledProcessError):
                packaging.build_updater(self.release)
        self.assertFalse((self.release / "SuzuEmojyUpdater.exe").exists())

    def test_zip_content_and_compression_unchanged_no_build_cache_shipped(self):
        binary = self.release / "bin" / "SuzuEmojy.exe"
        binary.parent.mkdir()
        binary.write_bytes(bytes(range(256)) * 100)
        (self.release / "SuzuEmojy.exe").write_bytes(b"launcher")
        with patch.object(packaging.subprocess, "run", side_effect=self.fake_compile):
            packaging.package_release(self.release, "standalone")
        archive = self.release.parent / "SuzuEmojy_Release.zip"
        baseline = self.root / "baseline.zip"
        with zipfile.ZipFile(archive) as actual, zipfile.ZipFile(baseline, "w", compression=zipfile.ZIP_DEFLATED) as expected:
            names = actual.namelist()
            self.assertEqual(set(names), {"bin/SuzuEmojy.exe", "SuzuEmojy.exe", "SuzuEmojyUpdater.exe", "version.json", "release-manifest.json"})
            for name in names:
                expected.write(self.release / name, name)
        self.assertEqual(archive.stat().st_size, baseline.stat().st_size)
        manifest = packaging.read_manifest(self.release, verify=True)
        self.assertIn("bin/SuzuEmojy.exe", manifest["files"])


if __name__ == "__main__":
    unittest.main()
