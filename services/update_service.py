"""Manual GitHub release checks and staged downloads. No startup network requests."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid
import zipfile

import requests

from services.update_protocol import (
    MANIFEST, atomic_json, child, read_manifest, safe_name, sha256, version_key,
)

RELEASES_URL = "https://github.com/IxinorTyan/SuzuEmojy/releases"
API_URL = "https://api.github.com/repos/IxinorTyan/SuzuEmojy/releases/latest"
ASSET_NAME = "SuzuEmojy_Release.zip"
MAX_DOWNLOAD = 2 * 1024 ** 3
MAX_EXTRACTED = 8 * 1024 ** 3


def install_root():
    frozen = getattr(sys, "frozen", False) or globals().get("__compiled__")
    base = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent.parent
    if base.name.lower() == "bin" and (base.parent / "version.json").is_file():
        return base.parent
    return base


def current_version(root=None):
    try:
        version = json.loads(((root or install_root()) / "version.json").read_text(encoding="utf-8"))["version"]
        version_key(version)
        return version
    except (OSError, ValueError, KeyError):
        return "unknown"


class Cancelled(Exception):
    pass


class UpdateService:
    def __init__(self, root=None):
        self.root = Path(root or install_root()).resolve()
        self.cancel = threading.Event()

    @property
    def workspace(self):
        path = self.root / ".update"
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Update directory cannot be a link")
        path.mkdir(exist_ok=True)
        return path

    def _cancelled(self):
        if self.cancel.is_set():
            raise Cancelled()

    def check(self):
        self._cancelled()
        with requests.get(API_URL, headers={"User-Agent": "SuzuEmojy-Updater", "Accept": "application/vnd.github+json"}, timeout=(8, 15)) as response:
            response.raise_for_status()
            release = response.json()
        self._cancelled()
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("No stable release available")
        latest = release["tag_name"]
        if version_key(latest) <= version_key(current_version(self.root)):
            return None
        assets = [a for a in release.get("assets", []) if a["name"] == ASSET_NAME and a.get("state") == "uploaded"]
        if len(assets) != 1:
            raise ValueError("Release does not contain " + ASSET_NAME)
        asset = assets[0]
        digest = asset.get("digest") or ""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Release asset has no SHA-256 digest; use the release page")
        url = asset["browser_download_url"]
        if not url.startswith(RELEASES_URL + "/download/"):
            raise ValueError("Unexpected release download URL")
        if not 0 < asset["size"] <= MAX_DOWNLOAD:
            raise ValueError("Invalid release size")
        return {"version": latest.lstrip("v"), "notes": release.get("body") or "", "date": release.get("published_at", ""),
                "size": asset["size"], "url": url, "sha256": digest[7:]}

    def ready(self):
        # Local inspection only; called when the user opens the update dialog.
        if not (self.root / ".update" / "ready.json").is_file():
            return None
        path = self.workspace / "ready.json"
        if not path.is_file():
            return None
        item = json.loads(path.read_text(encoding="utf-8"))
        safe_name(item["directory"])
        if "/" in item["directory"] or not item["directory"].startswith("stage-"):
            raise ValueError("Invalid staged directory")
        if version_key(item["version"]) <= version_key(current_version(self.root)):
            return None
        return item

    def download(self, release, progress):
        self._cancelled()
        local = read_manifest(self.root)
        work = self.workspace
        directory = "stage-" + uuid.uuid4().hex
        stage = work / directory
        archive = work / (directory + ".zip.part")
        stage.mkdir()
        completed = False
        try:
            if shutil.disk_usage(work).free < release["size"] * 2:
                raise OSError("Insufficient disk space")
            received = 0
            with requests.get(release["url"], stream=True, timeout=(8, 15), headers={"User-Agent": "SuzuEmojy-Updater"}) as response:
                response.raise_for_status()
                if not response.url.startswith("https://"):
                    raise ValueError("Insecure download redirect")
                with archive.open("wb") as output:
                    for block in response.iter_content(256 * 1024):
                        self._cancelled()
                        received += len(block)
                        if received > release["size"]:
                            raise ValueError("Download exceeds expected size")
                        output.write(block)
                        progress(min(95, int(received * 95 / release["size"])))
            self._cancelled()
            if received != release["size"] or sha256(archive) != release["sha256"]:
                raise ValueError("Download SHA-256/size verification failed")
            self.extract(archive, stage)
            incoming = read_manifest(stage, verify=True)
            if incoming["version"] != release["version"] or incoming["flavor"] != local["flavor"]:
                raise ValueError("Release version or package type does not match this installation")
            self._cancelled()
            item = {"version": release["version"], "directory": directory}
            previous = self.ready()
            atomic_json(work / "ready.json", item)
            completed = True
            if previous and previous["directory"] != directory:
                previous_stage = child(work, previous["directory"])
                if previous_stage.is_dir():
                    shutil.rmtree(previous_stage, ignore_errors=True)
            progress(100)
            return item
        finally:
            archive.unlink(missing_ok=True)
            if not completed:
                shutil.rmtree(stage)

    def extract(self, archive, stage):
        with zipfile.ZipFile(archive) as package:
            entries = [i for i in package.infolist() if not i.is_dir()]
            if len(entries) > 30001 or sum(i.file_size for i in entries) > MAX_EXTRACTED:
                raise ValueError("Update archive is too large")
            # Accept flat zip or exactly one enclosing directory.
            manifests = [i.filename for i in entries if i.filename == MANIFEST or i.filename.endswith("/" + MANIFEST)]
            if len(manifests) != 1:
                raise ValueError("Missing or ambiguous release manifest")
            prefix = manifests[0][:-len(MANIFEST)]
            if prefix:
                safe_name(prefix.rstrip("/"))
            seen = set()
            needed = sum(i.file_size for i in entries)
            if shutil.disk_usage(stage).free < needed * 2 + 64 * 1024 ** 2:
                raise OSError("Insufficient space to unpack and back up update")
            for entry in entries:
                self._cancelled()
                if not entry.filename.startswith(prefix):
                    raise ValueError("File outside package root")
                name = entry.filename[len(prefix):]
                target = child(stage, name)
                if name.casefold() in seen or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("Duplicate or linked archive entry")
                seen.add(name.casefold())
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("wb") as output:
                    while True:
                        self._cancelled()
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
            manifest = read_manifest(stage, verify=True)
            if seen != {n.casefold() for n in manifest["files"]} | {MANIFEST}:
                raise ValueError("Archive contains files not owned by the release manifest")

    def launch_installer(self):
        item = self.ready()
        if not item:
            raise ValueError("No prepared update")
        stage = child(self.workspace, item["directory"])
        incoming = read_manifest(stage, verify=True)
        local = read_manifest(self.root)
        if incoming["flavor"] != local["flavor"] or incoming["version"] != item["version"]:
            raise ValueError("Prepared package mismatch")
        helper = self.workspace / "SuzuEmojyUpdater.exe"
        shutil.copy2(child(self.root, "SuzuEmojyUpdater.exe"), helper)
        # Never execute an updater copied from the downloaded package before installation.
        if sha256(helper) != local["files"]["SuzuEmojyUpdater.exe"]:
            raise ValueError("Installed updater verification failed")
        handshake = self.workspace / "installer-ready"
        handshake.unlink(missing_ok=True)
        process = subprocess.Popen([str(helper), "--root", str(self.root), "--pid", str(os.getpid())], cwd=str(self.workspace))
        import time
        for _ in range(200):
            if handshake.is_file():
                return
            if process.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("Updater did not become ready; application remains open")
