"""Executed by the packaged Python, before publishing the local ZIP."""

from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import sys

import certifi
import requests
from PIL import Image
from rlottie_python.rlottie_wrapper import RLOTTIE_LIB

from services.gallery_commands import run
from services.gallery_snapshot import snapshot
from services.webm_converter import _get_ffmpeg_exe


def check(root):
    backend = Path.cwd()
    assert Path(sys.executable).parent == backend / "python" and sys.flags.isolated
    assert requests.__file__ and Path(requests.__file__).is_relative_to(backend / "python")
    assert Path(certifi.where()).is_file() and RLOTTIE_LIB is not None
    assert find_spec("PySide6") is None and find_spec("imageio_ffmpeg") is None
    converter = backend / "ffmpeg" / "ffmpeg.exe"
    assert Path(_get_ffmpeg_exe()) == converter
    subprocess.run([str(converter), "-version"], capture_output=True, check=True, timeout=15)

    root.mkdir()
    data, import_to = root / "图库", root / "重新导入"
    original = root / "多帧图片.png"
    first = Image.new("RGBA", (24, 32), "red")
    first.save(original, "PNG", save_all=True, append_images=[Image.new("RGBA", first.size, "blue")],
               duration=[120, 180], loop=0)
    saved = run(data, {"action": "import", "category": "封包测试", "paths": [str(original)]})
    assert (saved["count"], saved["failed"]) == (1, 0), saved
    gallery = snapshot(data)
    with Image.open(gallery["images"][0]["path"]) as animation:
        assert animation.n_frames == 2 and animation.info["duration"] == 120
        animation.seek(1)
        assert animation.info["duration"] == 180 and animation.convert("RGB").getpixel((5, 5)) == (0, 0, 255)
    assert gallery["categories"][0]["iconPath"]

    archive = root / "资源包.zip"
    run(data, {"action": "export_zip", "value": str(archive)})
    imported = run(import_to, {"action": "import_zip", "value": str(archive)})
    assert imported["count"] == 1 and snapshot(import_to)["categories"][0]["name"] == "封包测试"

    # Exercise the process/JSON contract the WPF front end expects.
    result = subprocess.run(
        [sys.executable, "-B", "-m", "services.gallery_snapshot", str(import_to)], cwd=backend,
        encoding="utf-8", capture_output=True, check=True, timeout=15,
    )
    assert len(json.loads(result.stdout)["images"]) == 1
    assert not (backend / "data").exists()
    print("Isolated runtime, SQLite, APNG, ZIP exchange, TGS library and minimal FFmpeg checks passed.")


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve())
