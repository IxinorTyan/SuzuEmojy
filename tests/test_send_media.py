from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys

from PIL import Image
import pytest

from services.send_media import static_gif


def test_static_gif_is_atomic_cached_and_keeps_binary_transparency(tmp_path):
    image_path = tmp_path / "表情.png"
    image = Image.new("RGBA", (2, 1), (200, 10, 60, 127))
    image.putpixel((1, 0), (64, 100, 120, 128))
    image.save(image_path)
    original = image_path.read_bytes()
    cache = tmp_path / "cache"
    with ThreadPoolExecutor(max_workers=3) as pool:
        copies = list(pool.map(lambda _: static_gif(image_path, cache), range(6)))
    assert copies[0] is not None and copies == [copies[0]] * 6
    assert list(cache.iterdir()) == [copies[0]]
    with Image.open(copies[0]) as gif:
        assert gif.n_frames == 1
        rgba = gif.convert("RGBA")
        transparent, opaque = rgba.getpixel((0, 0)), rgba.getpixel((1, 0))
        assert transparent[3] == 0
        assert opaque == (64, 100, 120, 255)
    assert image_path.read_bytes() == original


@pytest.mark.parametrize("extension", ["png", "gif", "webp"])
def test_animated_source_is_never_flattened_for_sending(tmp_path, extension):
    path = tmp_path / f"animated.{extension}"
    red = Image.new("RGBA", (15, 9), (255, 0, 0, 255))
    blue = Image.new("RGBA", (15, 9), (0, 0, 255, 255))
    red.save(path, save_all=True, append_images=[blue], duration=[100, 180], loop=0)
    before = path.read_bytes()
    with Image.open(path) as animation:
        assert animation.n_frames == 2
    assert static_gif(path, tmp_path / "cache") is None
    assert path.read_bytes() == before


def test_cli_is_qt_free_and_reports_original_file_fallback(tmp_path):
    missing = tmp_path / "missing.png"
    output = subprocess.check_output([
        sys.executable, "-B", "-c",
        "import sys; from services.send_media import main; main(); "
        "assert not any(m.startswith(('PySide', 'qfluentwidgets')) for m in sys.modules)",
        str(missing), str(tmp_path / "cache"),
    ])
    assert json.loads(output) is None
