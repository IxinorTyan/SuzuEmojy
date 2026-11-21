"""Discover and verify stable Windows 2.x downloads without invoking the 1.x installer."""
from pathlib import Path
import os
import re
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

from services.cooperative_cancel import CancellationMarker
from services.update_protocol import version_key
from services.update_service import MAX_DOWNLOAD, RELEASES_URL, UpdateService, Cancelled


class WindowsReleaseCheck(UpdateService):
    def __init__(self, installed, cancel_marker=None):
        super().__init__()
        stable, _, label = installed.partition('-')
        self.installed = (*version_key(stable), not label)
        self.cancel_marker = CancellationMarker(cancel_marker)

    def _cancelled(self):
        super()._cancelled()
        if self.cancel_marker.is_set():
            raise Cancelled()

    def _sources(self, url, _mirrors):
        # The hash itself is part of GitHub's release metadata, not an independent
        # signature. Do not trust third-party mirrors to supply that metadata.
        yield 'GitHub', url

    def _parse_release(self, release):
        if release.get('draft') or release.get('prerelease'):
            return None
        version = version_key(release['tag_name'])
        if version[0] != 2 or (*version, True) <= self.installed:
            return None
        label = release['tag_name'].removeprefix('v')
        name = f'SuzuEmojy-Windows-{label}-win-x64-system-runtime.zip'
        assets = release.get('assets')
        if not isinstance(assets, list):
            return None
        found = [a for a in assets if isinstance(a, dict) and a.get('name') == name and a.get('state') == 'uploaded']
        if len(found) != 1:
            return None
        asset = found[0]
        digest, url, size = (asset.get(key) for key in ('digest', 'browser_download_url', 'size'))
        expected = RELEASES_URL + f"/download/{release['tag_name']}/{name}"
        if (not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
                or url != expected or type(size) is not int or not 0 < size <= MAX_DOWNLOAD):
            return None
        notes = release.get('body')
        return {'version': label, 'notes': notes[:16000] if isinstance(notes, str) else '',
                'name': name, 'url': url, 'size': size, 'sha256': digest[7:]}

    def save_package(self, version, destination, progress):
        # Rediscover the release; never trust an URL/hash supplied by the UI or accept a stale download selection.
        release = self.check()
        if release is None or release['version'] != version:
            return {'error': '可用版本已经变化，请重新检查更新。'}
        destination = Path(destination)
        if not destination.is_absolute() or destination.suffix.lower() != '.zip' or destination.is_symlink():
            raise ValueError('请选择 ZIP 保存位置')
        with NamedTemporaryFile(dir=destination.parent, prefix='.suzu-', suffix='.part', delete=False) as temp:
            archive = Path(temp.name)
        try:
            self._download_archive(release, archive, lambda n: progress('download', n, 100, None),
                                   lambda source: progress('download', 0, 100, source))
            with ZipFile(archive) as package:
                names = set(package.namelist())
                if not {'SuzuEmojy.Windows.exe', 'SuzuEmojy.Windows.exe.config',
                        'backend/python/python.exe', 'backend/ffmpeg/ffmpeg.exe'}.issubset(names):
                    raise ValueError('这不是完整的 Windows 系统运行库包')
            self._cancelled()
            os.replace(archive, destination)
            return {'destination': str(destination)}
        finally:
            archive.unlink(missing_ok=True)
