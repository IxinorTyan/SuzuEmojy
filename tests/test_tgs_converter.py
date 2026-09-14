import gzip
import io
import json
from pathlib import Path

import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageSequence

from services.storage import StorageService
from services.tg_downloader import TGStickerDownloader
from services.tgs_converter import convert_tgs, read_tgs_json
from test_storage_single_frame_gif import _make_storage


def sticker_bytes(*, frames=60, opacity=100):
    # 红色方块在透明画布上移动；真实 rlottie 渲染，不模拟转码器。
    document = {
        "v": "5.7.4", "fr": 60, "ip": 0, "op": frames, "w": 64, "h": 64,
        "layers": [{
            "ty": 4, "ind": 1, "ip": 0, "op": frames, "st": 0,
            "ks": {
                "o": {"a": 0, "k": opacity}, "r": {"a": 0, "k": 0},
                "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]},
                "p": {"a": 1, "k": [
                    {"t": 0, "s": [16, 32, 0], "e": [48, 32, 0],
                     "o": {"x": 0.33, "y": 0.33}, "i": {"x": 0.67, "y": 0.67}},
                    {"t": frames, "s": [48, 32, 0]},
                ]},
            },
            "shapes": [
                {"ty": "rc", "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [12, 12]}, "r": {"a": 0, "k": 0}},
                {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}, "r": 1},
            ],
        }],
    }
    return gzip.compress(json.dumps(document).encode(), mtime=0)


def _check_non_tgs_is_not_identified(data):
    assert StorageService.detect_format_magic(data) == "unknown"
    with unittest.TestCase().assertRaises(ValueError):
        read_tgs_json(data)


def _check_oversized_json_is_rejected():
    with patch("services.tgs_converter.MAX_JSON_BYTES", 32):
        with unittest.TestCase().assertRaises(ValueError):
            read_tgs_json(gzip.compress(b" " * 100))


def _check_gif_preserves_motion_transparency_and_duration():
    data = sticker_bytes()
    assert StorageService.detect_format_magic(data) == "tgs"
    with Image.open(io.BytesIO(convert_tgs(data))) as gif:
        assert gif.format == "GIF"
        assert gif.n_frames > 1
        assert gif.info["loop"] == 0
        frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(gif)]
        assert all(frame.getpixel((0, 0))[3] == 0 for frame in frames)
        assert frames[0].getpixel((16, 32)) == (255, 0, 0, 255)
        assert frames[-1].getpixel((16, 32))[3] == 0  # 没有残影
        assert frames[0].tobytes() != frames[-1].tobytes()
        assert sum(frame.info["duration"] for frame in ImageSequence.Iterator(gif)) == 1000


def _check_png_unpremultiplies_alpha():
    with Image.open(io.BytesIO(convert_tgs(sticker_bytes(opacity=50), "png"))) as png:
        assert png.format == "PNG"
        red, green, blue, alpha = png.getpixel((16, 32))
        assert red >= 250 and green == blue == 0
        assert 125 <= alpha <= 128


def _check_storage_converts_and_deduplicates(tmp_path, extension):
    storage = _make_storage(tmp_path)
    source = tmp_path / ("sticker" + extension)
    data = sticker_bytes()
    source.write_bytes(data)
    saved, duplicate = storage.save_file(str(source))
    assert saved and not duplicate
    assert Path(saved).suffix == ".gif"
    assert storage.save_file(str(source)) == (saved, True)
    assert source.read_bytes() == data


def _check_single_frame_tgs_normalizes_to_png(tmp_path):
    storage = _make_storage(tmp_path)
    source = tmp_path / "single.tgs"
    source.write_bytes(sticker_bytes(frames=1))
    saved, duplicate = storage.save_file(str(source))
    assert saved and not duplicate
    assert Path(saved).suffix == ".png"


def _check_downloader_returns_requested_format(target):
    downloader = TGStickerDownloader.__new__(TGStickerDownloader)
    source = sticker_bytes()
    output, extension = downloader.process_sticker_data(source, ".TGS", target)
    if target == "original":
        assert (output, extension) == (source, "tgs")
    else:
        assert extension == target
        with Image.open(io.BytesIO(output)) as image:
            assert image.format.lower() == target


@patch("rlottie_python.rlottie_wrapper.RLOTTIE_LIB", None)
def _check_missing_renderer_does_not_save_raw_tgs(tmp_path):
    with unittest.TestCase().assertRaisesRegex(RuntimeError, "rlottie-python"):
        convert_tgs(sticker_bytes())
    storage = _make_storage(tmp_path)
    source = tmp_path / "source.tgs"
    source.write_bytes(sticker_bytes())
    assert storage.save_file(str(source)) == (None, False)
    assert source.exists()
    assert not list(Path(storage.images_dir).iterdir())



class TestTgsConverter(unittest.TestCase):
    def setUp(self):
        import gc
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.tmp_path = Path(directory.name)

    def test_invalid_input(self):
        for data in (b"not an image", gzip.compress(b"hello"), gzip.compress(b'[]'),
                     gzip.compress(b'{"v":"5"}'), bytes.fromhex('1f8b0800') + b'broken'):
            with self.subTest(data=data):
                _check_non_tgs_is_not_identified(data)
        _check_oversized_json_is_rejected()

    def test_animation(self):
        _check_gif_preserves_motion_transparency_and_duration()

    def test_png_alpha(self):
        _check_png_unpremultiplies_alpha()

    def test_storage(self):
        for index, extension in enumerate((".TGS", ".tgs", ".bin")):
            with self.subTest(extension=extension):
                directory = self.tmp_path / str(index)
                _check_storage_converts_and_deduplicates(directory, extension)

    def test_static(self):
        _check_single_frame_tgs_normalizes_to_png(self.tmp_path)

    def test_downloader(self):
        for target in ("png", "gif", "original"):
            with self.subTest(target=target):
                _check_downloader_returns_requested_format(target)

    def test_missing_renderer(self):
        _check_missing_renderer_does_not_save_raw_tgs(self.tmp_path)

    def test_existing_gif_regressions(self):
        import test_storage_single_frame_gif
        for name, function in vars(test_storage_single_frame_gif).items():
            if name.startswith("test_"):
                with self.subTest(name=name):
                    directory = self.tmp_path / name
                    directory.mkdir()
                    function(directory)
