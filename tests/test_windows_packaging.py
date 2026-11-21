import hashlib
import subprocess
import sys
from zipfile import ZipFile

import pytest

from build_windows import ROOT, SERVICES, read_version, zip_preview


def test_preview_uses_separate_version_and_dry_run_is_readonly(tmp_path):
    output = tmp_path / "预览"
    done = subprocess.run([sys.executable, "-B", "build_windows.py", "--dry-run", "--output-dir", str(output),
                           "--self-contained"], cwd=ROOT, check=True, capture_output=True)
    assert f"{read_version()}-win-x64-self-contained.zip".encode() in done.stdout
    assert not output.exists()
    assert all((ROOT / "services" / f"{name}.py").is_file() for name in SERVICES)
    assert not {"qt_import_thread", "clipboard", "update_service", "autostart"}.intersection(SERVICES)


def test_preview_zip_keeps_layout_and_matches_sidecar(tmp_path):
    package = tmp_path / "package"
    backend = package / "backend"
    backend.mkdir(parents=True)
    (package / "SuzuEmojy.Windows.exe").write_bytes(b"test executable")
    (backend / "windows-preview.json").write_bytes(b"{}")
    archive = tmp_path / "preview.zip"
    zip_preview(package, archive)
    with ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {"SuzuEmojy.Windows.exe", "backend/windows-preview.json"}
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert archive.with_suffix(".zip.sha256").read_text().strip() == f"{digest}  {archive.name}"
    with pytest.raises(FileExistsError):
        zip_preview(package, archive)


def test_preview_does_not_package_user_data(tmp_path):
    package = tmp_path / "package"
    (package / "data").mkdir(parents=True)
    (package / "data" / "config.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="Protected package path"):
        zip_preview(package, tmp_path / "preview.zip")
