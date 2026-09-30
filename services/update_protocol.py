"""Update package validation; deliberately standard-library only for the updater."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

MANIFEST = "release-manifest.json"
PROTECTED = {"data", "runtime", "logs", ".update", ".git", "__pycache__"}


def version_key(value):
    # Only stable releases are supported by protocol 1.
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    if not match:
        raise ValueError("Invalid stable version: " + str(value))
    return tuple(map(int, match.groups()))


def safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Invalid package path: " + str(name))
    parts = name.split("/")
    if any(p in ("", ".", "..") or p.rstrip(" .") != p for p in parts):
        raise ValueError("Invalid package path: " + name)
    for part in parts:
        if part.lower() in PROTECTED or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise ValueError("Protected package path: " + name)
        if any(ord(c) < 32 or c in '<>"|?*' for c in part):
            raise ValueError("Invalid package path: " + name)
    return PurePosixPath(name)


def child(root, name):
    safe_name(name)
    root = Path(root).resolve()
    target = root.joinpath(*name.split("/"))
    # Reject symlinks and Windows junctions, including ones pointing inside root.
    node = root
    parts = name.split("/")
    for index, part in enumerate(parts):
        node = node / part
        if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
            raise ValueError("Linked path is not updateable: " + name)
        if index < len(parts) - 1 and node.exists() and not node.is_dir():
            raise ValueError("Package parent path is a file: " + name)
    if not target.resolve().is_relative_to(root):
        raise ValueError("Path escapes installation: " + name)
    return target


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_manifest(root, verify=False):
    root = Path(root)
    manifest = json.loads(child(root, MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("protocol") != 1 or manifest.get("flavor") not in ("standalone", "lightweight"):
        raise ValueError("Unsupported update package")
    version_key(manifest["version"])
    files = manifest["files"]
    if not isinstance(files, dict) or not files or len(files) > 30000:
        raise ValueError("Invalid file manifest")
    seen = set()
    for name, digest in files.items():
        path = child(root, name)
        if name.casefold() in seen or name.casefold() == MANIFEST or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("Invalid or duplicate file manifest entry")
        seen.add(name.casefold())
        if verify and (not path.is_file() or sha256(path) != digest):
            raise ValueError("Package file verification failed: " + name)
    required = {"SuzuEmojy.exe", "SuzuEmojyUpdater.exe", "version.json"}
    required.add("bin/SuzuEmojy.exe" if manifest["flavor"] == "standalone" else "main.py")
    if not required.issubset(files):
        raise ValueError("Incomplete update package")
    if verify:
        version = json.loads((root / "version.json").read_text(encoding="utf-8"))
        if version["version"] != manifest["version"] or version.get("channel") != "stable":
            raise ValueError("Package version mismatch")
    return manifest
