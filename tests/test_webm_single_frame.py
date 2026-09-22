import gc
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from services.webm_converter import _get_ffmpeg_exe, is_ffmpeg_available, webm_single_frame_png
from test_storage_single_frame_gif import _make_storage


@unittest.skipUnless(is_ffmpeg_available(), "FFmpeg is required")
class WebMSingleFrameTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.temp.name)
        self.storage = _make_storage(self.root)

    def make_webm(self, frames):
        for index in range(frames):
            image = Image.new("RGBA", (48, 32), (0, 0, 0, 0))
            image.paste((240, 20, 50, 128), (4 + index, 6, 20 + index, 22))
            image.save(self.root / f"{index:02d}.png")
        output = self.root / "source.webm"
        subprocess.run([
            _get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", "25", "-i", str(self.root / "%02d.png"),
            "-frames:v", str(frames), "-c:v", "libvpx-vp9", "-lossless", "1",
            "-auto-alt-ref", "0", "-pix_fmt", "yuva420p", str(output),
        ], check=True, capture_output=True)
        return output

    def test_40ms_single_frame_is_saved_as_full_size_rgba_png(self):
        source = self.make_webm(1)
        path, duplicate = self.storage.save_file(str(source), strict=True)
        self.assertFalse(duplicate)
        self.assertEqual(Path(path).suffix, ".png")
        with Image.open(path) as image:
            self.assertEqual(image.size, (48, 32))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertAlmostEqual(image.getpixel((10, 10))[3], 128, delta=2)

    def test_multiple_frames_remain_animated(self):
        source = self.make_webm(12)
        self.assertIsNone(webm_single_frame_png(source))
        path, _ = self.storage.save_file(str(source), strict=True)
        self.assertEqual(Path(path).suffix, ".gif")
        with Image.open(path) as image:
            self.assertGreater(image.n_frames, 1)
            self.assertEqual(image.convert("RGBA").getpixel((0, 0))[3], 0)

    def test_two_short_frames_are_not_mistaken_for_static(self):
        self.assertIsNone(webm_single_frame_png(self.make_webm(2)))

    def test_invalid_video_is_not_silently_converted(self):
        source = self.root / "broken.webm"
        source.write_bytes(b"\x1a\x45\xdf\xa3broken")
        with self.assertRaisesRegex(RuntimeError, "decoding failed"):
            webm_single_frame_png(source)
