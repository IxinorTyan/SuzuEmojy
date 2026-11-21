import base64
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import zipfile

from PIL import Image
import pytest

from services.gallery_commands import run
from services.gallery_snapshot import snapshot
from services.hasher import compute_sync_key
from services.library_maintenance import backfill
from services.windows_updates import WindowsReleaseCheck
from services.update_service import API_MIRRORS, API_URL, RELEASES_URL, Cancelled


def read_pixels(result):
    return [Image.open(BytesIO(base64.b64decode(entry['png']))).convert('RGBA').getpixel((0, 0))
            for entry in result['frames']]


def test_original_apng_preview_ignores_cover_and_keeps_rgba(tmp_path):
    images = tmp_path / 'images'
    images.mkdir()
    source = images / '原图.apng'
    frame = lambda color: Image.new('RGBA', (26, 36), color)
    frame('white').save(source, 'PNG', save_all=True, default_image=True,
                        append_images=[frame((255, 0, 0, 128)), frame((0, 0, 255, 128))],
                        duration=[120, 180], loop=1, blend=[0, 1], disposal=[0, 0])
    before = source.read_bytes()
    result = run(tmp_path, {'action': 'preview_animation', 'paths': [str(source)], 'limit': 100})['animation']
    assert [entry['ms'] for entry in result['frames']] == [120, 180]
    assert result['plays'] == 1
    assert read_pixels(result) == [(255, 0, 0, 128), (85, 0, 170, 192)]
    assert source.read_bytes() == before and snapshot(tmp_path)['images'][0]['animated']


def test_original_animated_webp_preview_keeps_frame_delays_and_alpha(tmp_path):
    images = tmp_path / 'images'
    images.mkdir()
    source = images / '原图.webp'
    Image.new('RGBA', (30, 20), (255, 0, 0, 160)).save(source, 'WEBP', lossless=True, save_all=True,
        append_images=[Image.new('RGBA', (30, 20), (0, 0, 255, 96))], duration=[110, 200], loop=2)
    result = run(tmp_path, {'action': 'preview_animation', 'paths': [str(source)], 'limit': 15})['animation']
    assert [entry['ms'] for entry in result['frames']] == [110, 200]
    assert result['plays'] == 2
    assert read_pixels(result) == [(255, 0, 0, 160), (0, 0, 255, 96)]


def test_legacy_maintenance_backs_up_json_and_sqlite_and_preserves_images(tmp_path):
    images = tmp_path / 'images'
    images.mkdir()
    source = images / '保留.png'
    Image.new('RGBA', (30, 20), 'green').save(source)
    before = source.read_bytes()
    category = tmp_path / 'categories.json'
    category.write_text(json.dumps({'旧分类': ['保留.png', 'missing.png']}, ensure_ascii=False), encoding='utf-8-sig')
    (tmp_path / 'metadata.json').write_text(json.dumps({'保留.png': '旧关键词'}, ensure_ascii=False), encoding='utf-8')
    with closing(sqlite3.connect(tmp_path / 'features.db')) as db, db:
        db.execute('CREATE TABLE image_features (image_path TEXT PRIMARY KEY, md5 TEXT)')
        db.executemany('INSERT INTO image_features VALUES (?, ?)', [('保留.png', 'keep'), ('missing.png', 'old')])
    assert run(tmp_path, {'action': 'warm_indexes'})['count'] == 1
    assert run(tmp_path, {'action': 'warm_indexes'})['count'] == 0
    assert category.is_file() and not (tmp_path / 'categories.db').exists()

    result = run(tmp_path, {'action': 'maintain_library'})
    backup = Path(result['destination'])
    assert result['failed'] == 0 and (backup / 'categories.json').is_file()
    with closing(sqlite3.connect(backup / 'features.db')) as db:
        assert db.execute('SELECT md5 FROM image_features WHERE image_path=?', ('missing.png',)).fetchone() == ('old',)
    with closing(sqlite3.connect(tmp_path / 'features.db')) as db:
        assert db.execute('SELECT image_path, md5, sync_key FROM image_features').fetchall() == [
            ('保留.png', 'keep', compute_sync_key(source))]
    assert snapshot(tmp_path)['images'][0]['categories'] == ['旧分类']
    assert snapshot(tmp_path)['images'][0]['keywords'] == '旧关键词'
    assert (tmp_path / 'cache' / 'similarity-v1.db').is_file()
    assert source.read_bytes() == before


def test_startup_key_check_resumes_and_does_not_redecode_unchanged_images(tmp_path, monkeypatch):
    images = tmp_path / 'images'
    images.mkdir()
    for i in range(35):
        Image.new('RGBA', (4, 4), (i, i + 1, 80, 100)).save(images / f'{i:02d}.png')
    animation = images / 'old-animation.png'
    Image.new('RGBA', (4, 4), 'red').save(animation, save_all=True,
                                         append_images=[Image.new('RGBA', (4, 4), 'blue')])
    originals = {path: path.read_bytes() for path in images.iterdir()}
    calls = []
    from services.apng import is_apng

    def probe(stream):
        calls.append(Path(stream.name).name)
        return is_apng(stream)

    monkeypatch.setattr('services.apng.is_apng', probe)

    def interrupt(done, _):
        if done == 30:
            raise InterruptedError()

    with pytest.raises(InterruptedError):
        backfill(tmp_path, interrupt)
    assert len(calls) == 30
    calls.clear()
    assert backfill(tmp_path)['count'] == 5
    assert calls == [f'{i:02d}.png' for i in range(30, 35)] + [animation.name]
    calls.clear()
    assert backfill(tmp_path)['count'] == 0
    assert not calls
    assert {path: path.read_bytes() for path in originals} == originals
    revised = images / '26.png'
    old_stat = revised.stat()
    Image.new('RGBA', (5, 3), 'yellow').save(revised)
    import os
    os.utime(revised, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 2_000_000_000))
    assert backfill(tmp_path)['count'] == 1 and calls == [revised.name]
    with closing(sqlite3.connect(tmp_path / 'features.db')) as db:
        assert db.execute('SELECT COUNT(*) FROM image_features').fetchone() == (35,)
        assert db.execute('SELECT sync_key FROM image_features WHERE image_path=?', (revised.name,)).fetchone() == (
            compute_sync_key(revised),)


def test_release_check_filters_old_and_incompatible_packages():
    service = WindowsReleaseCheck('2.0.0-preview')
    assert list(service._sources(API_URL, API_MIRRORS)) == [('GitHub', API_URL)]
    name = 'SuzuEmojy-Windows-2.0.0-win-x64-system-runtime.zip'
    compatible = {'tag_name': 'v2.0.0', 'body': '更新说明', 'assets': [
        {'name': name, 'state': 'uploaded', 'digest': 'sha256:' + 'a' * 64, 'size': 20,
         'browser_download_url': f'{RELEASES_URL}/download/v2.0.0/{name}'}]}
    release = service._parse_release(compatible)
    assert release['version'] == '2.0.0' and release['notes'] == '更新说明' and release['sha256'] == 'a' * 64
    assert service._parse_release({**compatible, 'tag_name': 'v1.13.3'}) is None
    assert service._parse_release({**compatible, 'prerelease': True}) is None
    assert service._parse_release({**compatible, 'assets': [{'name': 'SuzuEmojy_Release.zip', 'state': 'uploaded'}]}) is None
    assert WindowsReleaseCheck('2.0.0')._parse_release(compatible) is None


def test_update_download_is_published_only_after_complete_verification(tmp_path, monkeypatch):
    import pytest

    service = WindowsReleaseCheck('2.0.0-preview')
    monkeypatch.setattr(service, 'check', lambda: {'version': '2.0.0'})
    destination = tmp_path / '下载.zip'
    destination.write_bytes(b'previous download')

    def download(_, archive, progress, source):
        with zipfile.ZipFile(archive, 'w') as package:
            for name in ('SuzuEmojy.Windows.exe', 'SuzuEmojy.Windows.exe.config',
                         'backend/python/python.exe', 'backend/ffmpeg/ffmpeg.exe'):
                package.writestr(name, b'fixture')

    def cancelled(*args):
        download(*args)
        raise Cancelled()

    monkeypatch.setattr(service, '_download_archive', cancelled)
    with pytest.raises(Cancelled):
        service.save_package('2.0.0', destination, lambda *_: None)
    assert destination.read_bytes() == b'previous download' and not list(tmp_path.glob('*.part'))

    monkeypatch.setattr(service, '_download_archive', download)
    assert service.save_package('2.0.0', destination, lambda *_: None)['destination'] == str(destination)
    assert zipfile.is_zipfile(destination) and not list(tmp_path.glob('*.part'))
