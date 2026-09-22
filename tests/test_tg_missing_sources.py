import unittest
from unittest.mock import Mock, patch

import requests

from services.tg_downloader import (
    TGStickerDownloader, StickerPackInfo, StickerUnavailableError,
)


class MissingSourceTest(unittest.TestCase):
    def setUp(self):
        self.downloader = TGStickerDownloader(bot_token="test-token", retries=1)
        self.downloader.session = Mock()

    def response(self, code, description="", content=b"image"):
        response = Mock(status_code=code, content=content, headers={})
        response.json.return_value = {
            "ok": False, "error_code": code, "description": description,
        }
        if code >= 400:
            response.raise_for_status.side_effect = requests.HTTPError(str(code))
        return response

    def test_api_missing_file_has_distinct_error(self):
        self.downloader.session.get.return_value = self.response(400, "Bad Request: file not found")
        with self.assertRaises(StickerUnavailableError):
            self.downloader.get_file_path("source-id")

    def test_other_api_errors_are_not_missing_sources(self):
        for code, description in [
            (400, "Bad Request: wrong file_id or the file is temporarily unavailable"),
            (400, "Bad Request: file is too big"),
            (401, "Unauthorized"), (429, "Too Many Requests"), (500, "Internal Server Error"),
        ]:
            with self.subTest(description=description), patch("services.tg_downloader.time.sleep"):
                self.downloader.session.get.return_value = self.response(code, description)
                with self.assertRaises(RuntimeError) as caught:
                    self.downloader.get_file_path("source-id")
                self.assertNotIsInstance(caught.exception, StickerUnavailableError)

    def test_official_missing_file_is_skippable_after_routes_exhausted(self):
        self.downloader.session.get.side_effect = [
            self.response(404), requests.Timeout("proxy timeout"),
        ]
        with self.assertRaises(StickerUnavailableError):
            self.downloader.download_file_bytes("stickers/missing.webm")

    def test_proxy_404_is_not_evidence_of_source_loss(self):
        self.downloader.session.get.side_effect = [
            requests.Timeout("direct timeout"), self.response(404),
        ]
        with self.assertRaises(RuntimeError) as caught:
            self.downloader.download_file_bytes("stickers/missing.webm")
        self.assertNotIsInstance(caught.exception, StickerUnavailableError)

    def test_alternate_route_success_takes_priority_over_404(self):
        self.downloader.session.get.side_effect = [self.response(404), self.response(200)]
        self.assertEqual(self.downloader.download_file_bytes("stickers/source.webm"), b"image")

    def test_source_links_use_pack_identity_not_display_title(self):
        for sticker_type, route in [("regular", "addstickers"), ("custom_emoji", "addemoji")]:
            pack = StickerPackInfo("TestPack", "可编辑标题", sticker_type, False, False, [])
            self.assertEqual(pack.source_url, f"https://t.me/{route}/TestPack")
