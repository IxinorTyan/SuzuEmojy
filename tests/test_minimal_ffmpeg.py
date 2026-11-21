"""Exercise the release binary with WebM fixtures produced by full FFmpeg."""
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image

import build_nuitka
from services import webm_converter


OUTPUT = build_nuitka.ROOT / "dist" / "ffmpeg-minimal"


@unittest.skipUnless(sys.platform == "win32" and (OUTPUT / "ffmpeg.exe").is_file(),
                     "Build the Windows FFmpeg with scripts/build_ffmpeg_minimal.sh")
class MinimalFFmpegTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = build_nuitka.minimal_ffmpeg_path()
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name) / "表情 文件"
        cls.root.mkdir()
        cls.app_exe = cls.root / "bin" / "SuzuEmojy.exe"
        bundled = cls.app_exe.parent / "ffmpeg" / "ffmpeg.exe"
        bundled.parent.mkdir(parents=True)
        shutil.copy2(binary, bundled)
        fixture = cls.root / "输入图片"
        fixture.mkdir()
        for index in range(6):
            image = Image.new("RGBA", (48, 32))
            image.paste((230, 110, 20, 64), (4, 5, 14, 16))
            image.paste((25, 200, 75, 192), (16, 5, 28, 16))
            image.paste((20, 55, 220, 255), (index * 4, 20, index * 4 + 6, 27))
            image.save(fixture / f"{index:02d}.png")
        cls.still = cls.root / "单帧 表情.webm"
        cls.animation = cls.root / "动画 表情.webm"
        cls.ten_bit = cls.root / "10 位 表情.webm"
        cls.full_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        for count, output, pix_fmt in (
            (1, cls.still, "yuva420p"),
            (6, cls.animation, "yuva420p"),
            (1, cls.ten_bit, "yuv420p10le"),
        ):
            subprocess.run([
                cls.full_ffmpeg, "-hide_banner", "-loglevel", "error",
                "-framerate", "25", "-i", str(fixture / "%02d.png"),
                "-frames:v", str(count), "-c:v", "libvpx-vp9", "-lossless", "1",
                "-auto-alt-ref", "0", "-pix_fmt", pix_fmt, str(output),
            ], check=True, capture_output=True)

    def setUp(self):
        self.enterContext(patch.object(sys, "frozen", True, create=True))
        self.enterContext(patch.object(sys, "executable", str(self.app_exe)))

    def assert_rgba(self, image):
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (48, 32))
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertAlmostEqual(image.getpixel((8, 10))[3], 64, delta=2)
        self.assertAlmostEqual(image.getpixel((22, 10))[3], 192, delta=2)

    def test_detects_one_frame_and_keeps_rgba(self):
        with Image.open(io.BytesIO(webm_converter.webm_single_frame_png(self.still))) as image:
            self.assert_rgba(image)
        self.assertIsNone(webm_converter.webm_single_frame_png(self.animation))

    def test_extracts_first_frame_for_telegram_preview(self):
        output = self.root / "预览 图片.png"
        self.assertTrue(webm_converter.webm_to_png_frame_ffmpeg(str(self.animation), str(output)))
        with Image.open(output) as image:
            self.assert_rgba(image)

    def test_animated_gif_keeps_frame_changes_transparency_and_loop(self):
        output = self.root / "转换 动画.gif"
        reference = self.root / "完整版 动画.gif"
        # FFmpeg versions differ on whether the fps filter keeps the terminal
        # frame. The minimal build must keep at least as many as our baseline.
        with patch.object(webm_converter, "_get_ffmpeg_exe", return_value=self.full_ffmpeg):
            webm_converter.convert_video_to_gif(str(self.animation), str(reference), fps=25, max_size=48)
        with Image.open(reference) as full:
            baseline_frames = full.n_frames
        self.assertTrue(webm_converter.convert_video_to_gif(
            str(self.animation), str(output), fps=25, max_size=48))
        with Image.open(output) as image:
            self.assertEqual(image.size, (48, 32))
            self.assertGreaterEqual(image.n_frames, max(2, baseline_frames))
            self.assertLessEqual(image.n_frames, 6)
            self.assertEqual(image.info.get("loop"), 0)
            for index in range(image.n_frames):
                image.seek(index)
                frame = image.convert("RGBA")
                self.assertEqual(image.info.get("duration"), 40)
                self.assertEqual(frame.getpixel((0, 0))[3], 0)
                self.assertEqual(frame.getpixel((8, 10))[3], 0)
                self.assertEqual(frame.getpixel((22, 10))[3], 255)
                self.assertEqual(frame.getpixel((index * 4 + 2, 22))[3], 255)

    def test_decodes_high_bit_depth_vp9(self):
        with Image.open(io.BytesIO(webm_converter.webm_single_frame_png(self.ten_bit))) as image:
            self.assertEqual((image.size, image.mode), ((48, 32), "RGBA"))
            self.assertEqual(image.getpixel((22, 10))[3], 255)

    def test_corrupt_webm_fails_cleanly(self):
        broken = self.root / "无效.webm"
        broken.write_bytes(b"not a WebM")
        with self.assertRaisesRegex(RuntimeError, "decoding failed"):
            webm_converter.webm_single_frame_png(broken)


if __name__ == "__main__":
    unittest.main()
