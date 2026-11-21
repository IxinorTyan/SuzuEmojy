"""Fetch a user-selected image URL into an import staging file."""

import os
import time
from http.client import HTTPException
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 64 * 1024 * 1024


class ImageDownloadError(ValueError):
    pass


def validate_url(url):
    try:
        parts = urlsplit(url)
        if (len(url) <= 8192 and parts.scheme in {"http", "https"} and parts.hostname
                and parts.username is None and parts.password is None
                and not any(char.isspace() or ord(char) < 32 for char in url)):
            return
    except (TypeError, ValueError):
        pass
    raise ImageDownloadError("链接不是有效的 HTTP 或 HTTPS 图片地址。")


class ImageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_image(url, target, formats, *, opener=None, cancelled=lambda: False):
    validate_url(url)
    opener = opener or build_opener(ImageRedirect())
    deadline = time.monotonic() + 45
    try:
        if cancelled():
            raise InterruptedError()
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with opener.open(request, timeout=10) as response:
            final = response.geturl()
            validate_url(final)
            mime = response.headers.get_content_type()
            suffix = os.path.splitext(urlsplit(final).path)[1].lower()
            if not mime.startswith("image/") and mime != "video/webm" and suffix not in formats:
                raise ImageDownloadError("该链接不是有效的图片资源。")
            length = response.headers.get("Content-Length", "").strip()
            if length.isascii() and length.isdecimal() and (len(length) > 16 or int(length) > MAX_BYTES):
                raise ImageDownloadError("图片超过可下载的大小限制。")
            size = 0
            with open(target, "xb") as file:
                while True:
                    if cancelled():
                        raise InterruptedError()
                    if time.monotonic() >= deadline:
                        raise ImageDownloadError("下载图片超时。")
                    block = response.read1(128 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_BYTES:
                        raise ImageDownloadError("图片超过可下载的大小限制。")
                    file.write(block)
            if not size or (length.isascii() and length.isdecimal() and size != int(length)):
                raise ImageDownloadError("图片没有下载完整。")
    except (ImageDownloadError, InterruptedError):
        raise
    except (OSError, HTTPException, URLError, ValueError):
        # Do not publish URLs (which may contain signed query strings) in errors.
        raise ImageDownloadError("无法获取网络图片。") from None
