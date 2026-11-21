from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread

import pytest
from PIL import Image

from services import network_image
from services.gallery_commands import run
from services.gallery_snapshot import snapshot
from services.inbox_import import claim_ready


def apng():
    cover = Image.new("RGBA", (12, 10), "red")
    frame = Image.new("RGBA", cover.size, "blue")
    output = BytesIO()
    cover.save(output, "PNG", save_all=True, append_images=[frame], duration=[100, 170], loop=0)
    return output.getvalue()


def assert_animated(path):
    with Image.open(path) as image:
        assert image.format == "GIF" and image.n_frames == 2
        assert image.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
        image.seek(1)
        assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 255)


@pytest.fixture
def image_server(monkeypatch):
    content = apng()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/image")
                self.end_headers()
                return
            if self.path == "/bad-redirect":
                self.send_response(302)
                self.send_header("Location", "ftp://localhost/image")
                self.end_headers()
                return
            mime = "text/html" if self.path in ("/page", "/not-an-image.png") else "image/apng"
            data = b"<html>Not an image</html>" if mime == "text/html" else content
            self.send_response(200)
            self.send_header("Content-Type", mime)
            if self.path != "/unbounded":
                self.send_header("Content-Length", str(len(data) + (10 if self.path == "/truncated" else 0)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    monkeypatch.setenv("no_proxy", "127.0.0.1")
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()


def test_direct_link_redirect_uses_the_same_import_pipeline(tmp_path, image_server):
    data = tmp_path / "data"
    events = []
    request = dict(action="import_link", value=image_server + "/redirect", category="浏览器")
    result = run(data, request, lambda *event: events.append(event))
    image, = snapshot(data)["images"]
    assert result["count"] == 1 and image["categories"] == ["浏览器"] and image["animated"]
    assert events[0][:3] == ("download", 0, 0) and events[-1][:3] == ("import", 1, 1)
    assert_animated(image["path"])
    assert run(data, request)["skipped"] == 1


def test_network_import_rejects_wrong_type_incomplete_and_oversized_content(tmp_path, image_server, monkeypatch):
    data = tmp_path / "data"
    for path in ("/page", "/not-an-image.png", "/bad-redirect", "/truncated"):
        assert "error" in run(data, dict(action="import_link", value=image_server + path))
    monkeypatch.setattr(network_image, "MAX_BYTES", 20)
    for path in ("/image", "/unbounded"):
        result = run(data, dict(action="import_link", value=image_server + path))
        assert "大小限制" in result["error"]
    assert not snapshot(data)["images"]
    for link in ("file:///C:/image.png", "https://user:password@example.com/a.png", "http://x/a\nb.png"):
        assert "error" in run(data, dict(action="import_link", value=link))


def observed(path, staged=False):
    info = path.stat()
    return dict(name=path.name, length=info.st_size, modified=info.st_mtime_ns // 100, staged=staged)


def test_inbox_consumes_only_the_observed_version_and_quarantines_failures(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    animated, broken = inbox / "网页动图.apng", inbox / "broken.png"
    animated.write_bytes(apng())
    broken.write_bytes(b"broken")
    outside = tmp_path / "original.png"
    outside.write_bytes(apng())
    old = observed(animated)
    animated.write_bytes(old["length"] * b"x" + b"z")
    assert run(tmp_path, dict(action="import_inbox", inbox_files=[old, observed(outside) | {"name": "../original.png"}])) == {}
    assert animated.is_file() and outside.is_file()
    animated.write_bytes(apng())
    result = run(tmp_path, dict(action="import_inbox", inbox_files=[observed(animated), observed(broken)]))
    image, = snapshot(tmp_path)["images"]
    assert (result["count"], result["skipped"], result["failed"]) == (1, 0, 1)
    assert image["categories"] == [] and image["animated"]
    assert_animated(image["path"])
    assert not animated.exists() and not broken.exists()
    retry, = (inbox / "failed").glob("*.png")
    assert retry.read_bytes() == b"broken" and outside.read_bytes() == apng()
    assert not any((inbox / ".processing").iterdir())


def test_inbox_resumes_claimed_files_without_consuming_a_replacement(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "saved.png"
    source.write_bytes(apng())
    claimed, = claim_ready(tmp_path, [observed(source)])
    source.write_bytes(apng())
    assert run(tmp_path, dict(action="import_inbox", inbox_files=[observed(Path(claimed), staged=True)]))["count"] == 1
    assert source.exists()
    assert run(tmp_path, dict(action="import_inbox", inbox_files=[observed(source)]))["skipped"] == 1
    assert not source.exists() and not Path(claimed).exists()


def test_apng_extension_is_recognized_by_manual_folder_import(tmp_path):
    folder = tmp_path / "saved"
    folder.mkdir()
    file = folder / "file.apng"
    file.write_bytes(apng())
    data = tmp_path / "data"
    result = run(data, dict(action="import", paths=[str(folder)]))
    assert result["count"] == result["folder_stats"]["image_count"] == 1
    assert file.exists()
    assert_animated(snapshot(data)["images"][0]["path"])


def test_cancelling_import_preserves_committed_items_and_skips_remaining_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for index, color in enumerate(("red", "blue", "green")):
        Image.new("RGB", (12, 12), color).save(source / f"{index}.png")
    marker = tmp_path / "cancel"
    marker.touch()
    root = tmp_path / "data"

    def cancel_after_first(phase, current, total, *_):
        if phase == "import" and current == 1:
            assert total == 3
            marker.write_text("1")

    result = run(root, dict(action="import", paths=[str(source)], cancel_file=str(marker)), cancel_after_first)
    assert (result["count"], result["failed"], result["cancelled"]) == (1, 0, 1)
    assert result["folder_stats"]["image_count"] == 3
    image, = snapshot(root)["images"]
    assert image["categories"] == ["source"]
    assert len(list(source.iterdir())) == 3
    assert run(root, dict(action="import", paths=[str(source)], cancel_file=str(marker))) == {"cancelled": 1}
    assert len(snapshot(root)["images"]) == 1


def test_cancelling_link_never_commits_a_download(tmp_path, image_server):
    marker = tmp_path / "cancel"
    marker.touch()
    root = tmp_path / "data"

    def cancel_download(phase, *_):
        if phase == "download":
            marker.write_text("1")

    result = run(root, dict(action="import_link", value=image_server + "/image", cancel_file=str(marker)), cancel_download)
    assert result == {"cancelled": 1} and not root.exists()
