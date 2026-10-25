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
from urllib.parse import urlsplit

import requests

from services.update_errors import UpdateError, UpdateNetworkError, request_error
from services.update_protocol import (
    MANIFEST, atomic_json, child, read_manifest, safe_name, sha256, version_key,
)

RELEASES_URL = "https://github.com/IxinorTyan/SuzuEmojy/releases"
API_URL = "https://api.github.com/repos/IxinorTyan/SuzuEmojy/releases/latest"
ASSET_NAME = "SuzuEmojy_Release.zip"
MAX_DOWNLOAD = 2 * 1024 ** 3
MAX_EXTRACTED = 8 * 1024 ** 3
# These proxies support release downloads; ghfast does not proxy the API.
API_MIRRORS = ("https://gh-proxy.com/",)
DOWNLOAD_MIRRORS = ("https://gh-proxy.com/", "https://ghfast.top/")
REQUEST_TIMEOUT = (5, 12)


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
    except (OSError, ValueError, KeyError, TypeError):
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

    def _sources(self, url, mirrors):
        yield "GitHub", url
        for mirror in mirrors:
            yield urlsplit(mirror).hostname, mirror + url

    def check(self, report=None):
        attempts = []
        for source, url in self._sources(API_URL, API_MIRRORS):
            self._cancelled()
            if report:
                report(source)
            try:
                with requests.get(url, headers={"User-Agent": "SuzuEmojy-Updater", "Accept": "application/vnd.github+json"}, timeout=REQUEST_TIMEOUT) as response:
                    response.raise_for_status()
                    if not response.url.startswith("https://"):
                        raise UpdateError("redirect")
                    release = response.json()
                    if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
                        raise UpdateError("metadata", "Missing release tag_name")
            except (requests.RequestException, ValueError) as error:
                attempts.append((source, url, request_error(error)))
                continue
            self._cancelled()
            result = self._parse_release(release)
            if result:
                result["source"] = source
            return result
        self._cancelled()
        raise UpdateNetworkError("check", attempts)

    def _parse_release(self, release):
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("No stable release available")
        latest = release["tag_name"]
        installed_version = current_version(self.root)
        if installed_version == "unknown":
            raise UpdateError("version", str(self.root / "version.json"))
        if version_key(latest) <= version_key(installed_version):
            return None
        assets = release.get("assets")
        if not isinstance(assets, list) or not all(isinstance(asset, dict) for asset in assets):
            raise UpdateError("release", "Invalid release assets")
        assets = [a for a in assets if a.get("name") == ASSET_NAME and a.get("state") == "uploaded"]
        if len(assets) != 1:
            raise ValueError("Release does not contain " + ASSET_NAME)
        asset = assets[0]
        digest = asset.get("digest") or ""
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Release asset has no SHA-256 digest; use the release page")
        url = asset.get("browser_download_url")
        if not isinstance(url, str) or not url.startswith(RELEASES_URL + "/download/"):
            raise ValueError("Unexpected release download URL")
        if type(asset.get("size")) is not int or not 0 < asset["size"] <= MAX_DOWNLOAD:
            raise ValueError("Invalid release size")
        notes, date = release.get("body") or "", release.get("published_at") or ""
        if not isinstance(notes, str) or not isinstance(date, str):
            raise UpdateError("release", "Invalid release notes or publication date")
        return {"version": latest.lstrip("v"), "notes": notes, "date": date,
                "size": asset["size"], "url": url, "sha256": digest[7:]}

    def installation_manifest(self):
        try:
            manifest = read_manifest(self.root)
            if not child(self.root, "SuzuEmojyUpdater.exe").is_file():
                raise FileNotFoundError(str(self.root / "SuzuEmojyUpdater.exe"))
            return manifest
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise UpdateError("installation", str(error)) from error

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

    def _download_archive(self, release, archive, progress, report):
        # Only project release URLs are sent to third-party mirrors.
        mirrors = DOWNLOAD_MIRRORS if release["url"].startswith(RELEASES_URL + "/download/") else ()
        attempts = []
        for source, url in self._sources(release["url"], mirrors):
            self._cancelled()
            if report:
                report(source)
            progress(0)
            try:
                received = 0
                with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "SuzuEmojy-Updater"}) as response:
                    response.raise_for_status()
                    if not response.url.startswith("https://"):
                        raise UpdateError("redirect")
                    content_type = response.headers.get("Content-Type", "")
                    if isinstance(content_type, str) and any(kind in content_type.lower() for kind in ("text/html", "application/json")):
                        raise UpdateError("content", content_type)
                    # Each source starts afresh; a failed source's partial bytes
                    # must never be appended to the next source's download.
                    with archive.open("wb") as output:
                        for block in response.iter_content(256 * 1024):
                            self._cancelled()
                            received += len(block)
                            if received > release["size"]:
                                raise UpdateError("size", expected=release["size"], actual=received)
                            output.write(block)
                            progress(min(95, int(received * 95 / release["size"])))
                self._cancelled()
                if received != release["size"]:
                    raise UpdateError("size", expected=release["size"], actual=received)
                actual_hash = sha256(archive)
                if actual_hash != release["sha256"]:
                    raise UpdateError("hash", f'Expected SHA-256: {release["sha256"]}\nActual SHA-256: {actual_hash}')
                return
            except (requests.RequestException, UpdateError) as error:
                attempts.append((source, url, request_error(error)))
        self._cancelled()
        raise UpdateNetworkError("download", attempts)

    def download(self, release, progress, report=None):
        self._cancelled()
        local = self.installation_manifest()
        work = self.workspace
        directory = "stage-" + uuid.uuid4().hex
        stage = work / directory
        archive = work / (directory + ".zip.part")
        stage.mkdir()
        completed = False
        try:
            if shutil.disk_usage(work).free < release["size"] * 2:
                raise OSError("Insufficient disk space")
            self._download_archive(release, archive, progress, report)
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
            # Cleanup must not replace the original network/disk diagnostic.
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                pass
            if not completed:
                shutil.rmtree(stage, ignore_errors=True)

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
        local = self.installation_manifest()
        if incoming["flavor"] != local["flavor"] or incoming["version"] != item["version"]:
            raise ValueError("Prepared package mismatch")
        helper = self.workspace / "SuzuEmojyUpdater.exe"
        shutil.copy2(child(self.root, "SuzuEmojyUpdater.exe"), helper)
        # Never execute an updater copied from the downloaded package before installation.
        if sha256(helper) != local["files"]["SuzuEmojyUpdater.exe"]:
            raise ValueError("Installed updater verification failed")
        handshake = self.workspace / "installer-ready"
        handshake.unlink(missing_ok=True)
        error_log = self.workspace / "last-error.txt"
        error_log.unlink(missing_ok=True)
        process = subprocess.Popen([str(helper), "--root", str(self.root), "--pid", str(os.getpid())], cwd=str(self.workspace))
        import time
        for _ in range(200):
            if handshake.is_file():
                return
            if process.poll() is not None:
                break
            time.sleep(0.1)
        detail = ""
        if process.poll() is None:
            # A late helper must not keep the executable locked or install later
            # after the UI has already reported that preparation failed.
            detail = "Installer readiness handshake was not received within 20 seconds.\n"
            try:
                process.terminate()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as error:
                detail += f"Could not stop the unready updater: {error}\n"
        detail += f"Updater exit code: {process.poll()}"
        if error_log.is_file():
            try:
                detail += "\n" + error_log.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                detail += f"\nCould not read installer log: {error}"
        raise UpdateError("installer", detail)
