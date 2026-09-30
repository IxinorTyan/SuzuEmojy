"""Build the isolated updater and a reproducible, manifest-owned release zip."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.update_protocol import MANIFEST, PROTECTED, atomic_json, read_manifest, sha256, version_key


def package_release(release_dir, flavor):
    release_dir = Path(release_dir).resolve()
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    version_key(version["version"])
    if version.get("channel") != "stable":
        raise ValueError("Only stable releases are currently supported")
    # Both packaging modes use an independent updater without Qt dependencies.
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", "SuzuEmojyUpdater", "--distpath", str(release_dir),
        "--workpath", str(ROOT / "dist" / "updater-build"),
        "--specpath", str(ROOT / "dist"), str(ROOT / "updater.py"),
    ], cwd=ROOT, check=True)
    atomic_json(release_dir / "version.json", version)
    files = {}
    for path in sorted(release_dir.rglob("*")):
        name = path.relative_to(release_dir).as_posix()
        if path.is_file() and name != MANIFEST and not any(p.lower() in PROTECTED for p in path.relative_to(release_dir).parts):
            files[name] = sha256(path)
    atomic_json(release_dir / MANIFEST, {"protocol": 1, "version": version["version"], "flavor": flavor, "files": files})
    read_manifest(release_dir, verify=True)
    archive = release_dir.parent / "SuzuEmojy_Release.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name in sorted(files) + [MANIFEST]:
            output.write(release_dir / name, name)
    archive.with_suffix(".zip.sha256").write_text(sha256(archive) + "  " + archive.name + "\n", encoding="ascii")
    print("Release v" + version["version"] + ": " + str(archive))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir")
    parser.add_argument("--flavor", choices=["standalone", "lightweight"], required=True)
    args = parser.parse_args()
    package_release(args.release_dir, args.flavor)
