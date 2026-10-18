"""Build the isolated updater and a reproducible, manifest-owned release zip."""
import argparse
from importlib.metadata import version as distribution_version
import json
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.update_protocol import MANIFEST, PROTECTED, atomic_json, read_manifest, sha256, version_key


def build_updater(release_dir, clean=False):
    """Let PyInstaller validate dependencies, with a stable output to reuse EXE/PKG.

    Keep this output outside the release: recreating the release directory must
    not force PyInstaller to relink an otherwise unchanged updater.
    """
    work = ROOT / "dist" / "updater-build"
    output = ROOT / "dist" / "updater-dist"
    marker = work / "build-context.json"
    context = {
        "python": [sys.executable, sys.version],
        "tools": {name: distribution_version(name) for name in ("pyinstaller", "pyinstaller-hooks-contrib")},
        # Also detect source checkouts that restore older modification times.
        # Other dependencies are checked by PyInstaller's own Analysis/TOC.
        "inputs": {name: sha256(ROOT / name) for name in (
            "updater.py", "services/update_protocol.py", "scripts/package_release.py",
        )},
    }
    try:
        previous = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    rebuild = clean or previous != context
    if rebuild:
        marker.unlink(missing_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
    if rebuild:
        command.append("--clean")
    command += [
        "--onefile", "--noconsole", "--name", "SuzuEmojyUpdater",
        "--distpath", str(output), "--workpath", str(work),
        "--specpath", str(ROOT / "dist"), str(ROOT / "updater.py"),
    ]
    started = perf_counter()
    # Always run dependency checks; never copy a cached EXE on build failure.
    subprocess.run(command, cwd=ROOT, check=True)
    shutil.copy2(output / "SuzuEmojyUpdater.exe", Path(release_dir) / "SuzuEmojyUpdater.exe")
    work.mkdir(parents=True, exist_ok=True)
    atomic_json(marker, context)
    print(f"Updater ({'rebuild' if rebuild else 'incremental'}): {perf_counter() - started:.1f}s")


def package_release(release_dir, flavor, *, clean=False):
    release_dir = Path(release_dir).resolve()
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    version_key(version["version"])
    if version.get("channel") != "stable":
        raise ValueError("Only stable releases are currently supported")
    # Both packaging modes use an independent updater without Qt dependencies.
    build_updater(release_dir, clean=clean)
    started = perf_counter()
    atomic_json(release_dir / "version.json", version)
    files = {}
    for path in sorted(release_dir.rglob("*")):
        name = path.relative_to(release_dir).as_posix()
        if path.is_file() and name != MANIFEST and not any(p.lower() in PROTECTED for p in path.relative_to(release_dir).parts):
            files[name] = sha256(path)
    atomic_json(release_dir / MANIFEST, {"protocol": 1, "version": version["version"], "flavor": flavor, "files": files})
    read_manifest(release_dir, verify=True)
    print(f"Release manifest and verification: {perf_counter() - started:.1f}s")
    archive = release_dir.parent / "SuzuEmojy_Release.zip"
    started = perf_counter()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name in sorted(files) + [MANIFEST]:
            output.write(release_dir / name, name)
    archive.with_suffix(".zip.sha256").write_text(sha256(archive) + "  " + archive.name + "\n", encoding="ascii")
    print(f"ZIP and checksum: {perf_counter() - started:.1f}s; {archive.stat().st_size / 1024 ** 2:.2f} MiB")
    print("Release v" + version["version"] + ": " + str(archive))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir")
    parser.add_argument("--flavor", choices=["standalone", "lightweight"], required=True)
    parser.add_argument("--clean", action="store_true", help="Force an updater rebuild")
    args = parser.parse_args()
    package_release(args.release_dir, args.flavor, clean=args.clean)
