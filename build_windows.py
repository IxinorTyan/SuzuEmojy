"""Build a local, portable Windows x64 2.0 preview ZIP. Never publishes."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from build_nuitka import FFMPEG_SOURCE_ARCHIVE, ROOT, minimal_ffmpeg_path
from services.update_protocol import child, sha256


PROJECT = ROOT / "win_frontend" / "SuzuEmojy.Windows.csproj"
PYTHON_VERSION = "3.13.17"
PYTHON_ARCHIVE = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ARCHIVE}"
PYTHON_SHA256 = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
SERVICES = (
    "apng", "appearance", "atomic_json", "config", "cooperative_cancel", "exchange_export",
    "exchange_icons", "exchange_import", "exchange_split", "external_stickers", "gallery_commands",
    "gallery_snapshot", "hasher", "import_pipeline", "inbox_import", "marketface_handler",
    "network_image", "qq_extractor", "send_media", "similarity", "storage", "tg_batch_export",
    "tg_downloader", "tg_package_zip", "tg_resource_packages", "tgs_converter", "webm_converter",
    "window_wallpaper", "windows_recycle",
)


def read_version(project=PROJECT):
    version = ElementTree.parse(project).getroot().findtext("./PropertyGroup/Version")
    if not version or not re.fullmatch(r"\d+\.\d+\.\d+-preview(?:\.\d+)?", version):
        raise ValueError("预览包版本必须是 X.Y.Z-preview 或 X.Y.Z-preview.N，不能打成正式更新包。")
    return version


def download_python(cache):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / PYTHON_ARCHIVE
    if not archive.is_file() or sha256(archive) != PYTHON_SHA256:
        with TemporaryDirectory(prefix=".python-", dir=cache) as stage:
            temporary = Path(stage) / archive.name
            print(f"下载 Python {PYTHON_VERSION}（Windows x64）...", flush=True)
            with urlopen(PYTHON_URL, timeout=45) as source, temporary.open("xb") as output:
                shutil.copyfileobj(source, output)
            if sha256(temporary) != PYTHON_SHA256:
                raise ValueError("Python 下载内容未通过 SHA-256 校验。")
            temporary.replace(archive)
    return archive


def bundle_python(backend, cache):
    runtime = backend / "python"
    runtime.mkdir()
    with ZipFile(download_python(cache)) as archive:
        for entry in archive.infolist():
            # The official embeddable distribution contains only root files.
            target = child(runtime, entry.filename)
            if target.parent != runtime or entry.is_dir():
                raise ValueError("Python 封包布局不符。")
            with archive.open(entry) as stream, target.open("xb") as output:
                shutil.copyfileobj(stream, output)
    # Explicit paths keep the interpreter independent of system/user packages and PATH.
    (runtime / "python313._pth").write_text("python313.zip\n.\nsite-packages\n..\n", encoding="ascii")
    subprocess.run([
        sys.executable, "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-input",
        "install", "--index-url", "https://pypi.org/simple", "--cache-dir", str(cache / "pip"),
        "--require-hashes", "--only-binary=:all:", "--no-compile", "--ignore-installed", "--no-deps",
        "--implementation", "cp",
        "--python-version", "3.13", "--abi", "cp313", "--platform", "win_amd64",
        "--target", str(runtime / "site-packages"), "-r", str(ROOT / "scripts" / "windows-packages.lock"),
    ], cwd=ROOT, check=True)
    return runtime / "python.exe"


def zip_preview(package, archive):
    with ZipFile(archive, "x", compression=ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(package.rglob("*")):
            name = path.relative_to(package).as_posix()
            target = child(package, name)  # No user data, cache, junctions or traversal in the ZIP.
            if target.is_file():
                output.write(target, name)
    archive.with_suffix(".zip.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii")


def build(output_dir, ffmpeg_dir, self_contained, dry_run):
    version = read_version()
    mode = "self-contained" if self_contained else "desktop-runtime"
    name = f"SuzuEmojy-Windows-{version}-win-x64-{mode}"
    destination = output_dir / name
    if dry_run:
        print(f"{destination / (name + '.zip')}\nFFmpeg: {ffmpeg_dir}\nPython: {PYTHON_VERSION} (x64)")
        return
    if sys.platform != "win32":
        raise RuntimeError("请在 Windows 上运行封包和离线验收。")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"输出已存在，未覆盖：{destination}。请换一个 --output-dir。")
    ffmpeg = minimal_ffmpeg_path(output_dir=ffmpeg_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "dist" / "windows-package-cache"
    with TemporaryDirectory(prefix=".windows-build-", dir=output_dir) as scratch:
        work = Path(scratch)
        package = work / "app"
        subprocess.run([
            "dotnet", "publish", str(PROJECT), "-c", "Release", "-r", "win-x64",
            "--self-contained", str(self_contained).lower(), "-p:DebugType=None", "-p:DebugSymbols=false",
            "-o", str(package),
        ], cwd=ROOT, check=True)
        backend = package / "backend"
        modules = backend / "services"
        modules.mkdir(parents=True)
        for module in SERVICES:
            source = ROOT / "services" / f"{module}.py"
            shutil.copyfile(source, modules / source.name)
        converter = backend / "ffmpeg"
        converter.mkdir()
        shutil.copyfile(ffmpeg, converter / ffmpeg.name)
        shutil.copyfile(ffmpeg.parent / "THIRD_PARTY.txt", converter / "THIRD_PARTY.txt")
        shutil.copyfile(ROOT / "LICENSE", package / "LICENSE")
        shutil.copyfile(ROOT / "docs" / "windows-packaging.md", package / "Windows-README.md")
        (backend / "windows-preview.json").write_text(json.dumps({
            "version": version, "architecture": "win-x64", "python": PYTHON_VERSION,
            "desktop_runtime_included": self_contained,
        }, indent=2) + "\n", encoding="utf-8")
        python = bundle_python(backend, cache)
        subprocess.run([
            str(python), "-B", str(ROOT / "scripts" / "check_windows_package.py"), str(work / "smoke"),
        ], cwd=backend, check=True)
        output = work / "output"
        output.mkdir()
        zip_preview(package, output / (name + ".zip"))
        shutil.copyfile(ffmpeg.parent / FFMPEG_SOURCE_ARCHIVE, output / FFMPEG_SOURCE_ARCHIVE)
        output.rename(destination)  # Atomic on this volume; Windows refuses an existing target.
    finished = destination / (name + ".zip")
    print(f"本地预览封包：{finished}（{finished.stat().st_size / 1024 ** 2:.2f} MiB）")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "windows-packages")
    parser.add_argument("--ffmpeg-dir", type=Path, default=ROOT / "dist" / "ffmpeg-minimal")
    parser.add_argument("--self-contained", action="store_true", help="Also bundle .NET Desktop Runtime (larger ZIP)")
    parser.add_argument("--dry-run", action="store_true", help="Show package path without writing or downloading")
    args = parser.parse_args()
    try:
        build(args.output_dir.resolve(), args.ffmpeg_dir.resolve(), args.self_contained, args.dry_run)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"封包失败：{exc}\n")


if __name__ == "__main__":
    main()
