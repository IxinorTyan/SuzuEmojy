import os
import shutil
import tempfile
import unittest
from pathlib import Path
from services.qq_extractor import QQExtractor
from fluent_ui.views.qq_scan_view import QQScanInterface


# Valid image magic byte headers
JPEG_HEADER = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
GIF_HEADER = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04"


class TestQQExtractor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_qq_env_")
        self.userdata = Path(self.temp_dir) / "Tencent Files"
        self.qq_number = "123456789"
        nt_data = self.userdata / self.qq_number / "nt_qq" / "nt_data"

        emoji_dir = nt_data / "Emoji" / "personal_emoji" / "Ori"
        emoji_dir.mkdir(parents=True, exist_ok=True)
        with open(emoji_dir / "emoji1.png", "wb") as f:
            f.write(PNG_HEADER + b"\x00" * 32)

        self.pic_dir = nt_data / "Pic"
        # Month 1
        ori_m1 = self.pic_dir / "2026-01" / "Ori"
        thumb_m1 = self.pic_dir / "2026-01" / "Thumb"
        ori_m1.mkdir(parents=True, exist_ok=True)
        thumb_m1.mkdir(parents=True, exist_ok=True)

        with open(ori_m1 / "photo1.jpg", "wb") as f:
            f.write(JPEG_HEADER + b"\x00" * 32)
        with open(thumb_m1 / "photo1_thumb.jpg", "wb") as f:
            f.write(JPEG_HEADER + b"\x00" * 32)
        with open(ori_m1 / "not_image.txt", "wb") as f:
            f.write(b"plain text content")

        # Month 2
        ori_m2 = self.pic_dir / "2026-02" / "Ori"
        ori_m2.mkdir(parents=True, exist_ok=True)
        with open(ori_m2 / "photo2.gif", "wb") as f:
            f.write(GIF_HEADER + b"\x00" * 32)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_paths(self):
        emoji_root = QQExtractor.get_emoji_root(self.userdata, self.qq_number)
        self.assertEqual(emoji_root, self.userdata / self.qq_number / "nt_qq" / "nt_data" / "Emoji")

        pic_root = QQExtractor.get_pic_root(self.userdata, self.qq_number)
        self.assertEqual(pic_root, self.userdata / self.qq_number / "nt_qq" / "nt_data" / "Pic")

        # Category path routing
        cat_emoji = QQExtractor.get_category_path(self.userdata, self.qq_number, "personal_emoji")
        self.assertEqual(cat_emoji, emoji_root / "personal_emoji")

        cat_pic = QQExtractor.get_category_path(self.userdata, self.qq_number, "pic")
        self.assertEqual(cat_pic, pic_root)

        cat_pic_upper = QQExtractor.get_category_path(self.userdata, self.qq_number, "Pic")
        self.assertEqual(cat_pic_upper, pic_root)

    def test_scan_pic_folder(self):
        found_files = QQExtractor.scan_pic_folder(self.pic_dir)
        # Should only find the 2 valid image files inside Ori subdirectories:
        # 2026-01/Ori/photo1.jpg and 2026-02/Ori/photo2.gif
        # Should exclude Thumb folder and plain text files
        self.assertEqual(len(found_files), 2)
        basenames = [os.path.basename(p) for p in found_files]
        self.assertIn("photo1.jpg", basenames)
        self.assertIn("photo2.gif", basenames)
        self.assertNotIn("photo1_thumb.jpg", basenames)
        self.assertNotIn("not_image.txt", basenames)

    def test_scan_emojis_routing(self):
        # Scan pic
        pic_results = QQExtractor.scan_emojis(self.pic_dir, "pic")
        self.assertEqual(len(pic_results), 2)

        # Scan personal_emoji
        emoji_dir = QQExtractor.get_category_path(self.userdata, self.qq_number, "personal_emoji")
        emoji_results = QQExtractor.scan_emojis(emoji_dir, "personal_emoji")
        self.assertEqual(len(emoji_results), 1)
        self.assertEqual(os.path.basename(emoji_results[0]), "emoji1.png")

    def test_unique_dest_path(self):
        temp_dir = tempfile.mkdtemp(prefix="test_unique_dest_")
        try:
            p1 = QQScanInterface._unique_dest_path(temp_dir, "sample", "jpg")
            self.assertEqual(os.path.basename(p1), "sample.jpg")
            with open(p1, "w") as f:
                f.write("1")

            p2 = QQScanInterface._unique_dest_path(temp_dir, "sample", "jpg")
            self.assertEqual(os.path.basename(p2), "sample_1.jpg")
            with open(p2, "w") as f:
                f.write("2")

            p3 = QQScanInterface._unique_dest_path(temp_dir, "sample", "jpg")
            self.assertEqual(os.path.basename(p3), "sample_2.jpg")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_interface_config_persistence(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        mock_config_dict = {
            "qq_save_path": self.temp_dir,
            "qq_data_path": str(self.userdata)
        }
        class MockConfig:
            def __init__(self, data):
                self.data = data
            def get(self, k, default=None):
                return self.data.get(k, default)
            def set(self, k, v):
                self.data[k] = v

        cfg = MockConfig(mock_config_dict)
        interface = QQScanInterface(config_service=cfg)
        self.assertEqual(interface.savePath, self.temp_dir)
        self.assertEqual(interface.userdata_save_path_cache, str(self.userdata))
        self.assertEqual(interface.savePathEdit.text(), self.temp_dir)
        self.assertEqual(interface.readPathEdit.text(), str(self.userdata))

        # 检查是否成功扫描出 pic 分类并加入下拉列表
        categories = [interface.emojiFolderComboBox.itemText(i) for i in range(interface.emojiFolderComboBox.count())]
        self.assertIn(interface._category_display_name("pic"), categories)
        interface.deleteLater()


if __name__ == "__main__":
    unittest.main()
