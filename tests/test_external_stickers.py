import base64
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from PIL import Image

from services.gallery_commands import run
from services.gallery_snapshot import snapshot
from services.marketface_handler import restore_marketface
from services.tg_downloader import TG_DIRECT_API, TGStickerDownloader, StickerItem, StickerPackInfo


def _image(path, color="tomato", format=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (30, 26), color).save(path, format)
    return path


def test_qq_cache_scan_preview_and_selected_import(tmp_path):
    base = tmp_path / "Tencent Files"
    cache = base / "1234" / "nt_qq" / "nt_data"
    personal = cache / "Emoji" / "personal_emoji"
    first = _image(personal / "Ori" / "frame0.dat", format="PNG")
    second = _image(personal / "Ori" / "other.dat", "navy", format="PNG")
    _image(personal / "Thumb" / "derivative.png")
    picture = _image(cache / "Pic" / "2026-10" / "Ori" / "picture.png")
    _image(cache / "Pic" / "2026-10" / "Thumb" / "derivative.png")
    data = tmp_path / "data"
    req = dict(value=str(base), account="1234", kind="personal_emoji")
    before = {file: file.read_bytes() for file in cache.rglob("*") if file.is_file()}
    detected = run(data, dict(action="qq_discover", **req))
    assert detected["sources"][0]["key"] == "1234"
    assert {item["key"] for item in detected["sources"][0]["options"]} == {"personal_emoji", "pic"}
    items = run(data, dict(action="qq_scan", **req))["items"]
    assert [Path(item["id"]) for item in items] == [first, second]
    assert (items[0]["width"], items[0]["height"], items[0]["animated"]) == (30, 26, False)
    photos = run(data, dict(action="qq_scan", **(req | {"kind": "pic"})))["items"]
    assert [Path(item["id"]) for item in photos] == [picture]
    previews = run(data, dict(action="qq_previews", items=items[:1], **req))["previews"]
    with Image.open(BytesIO(base64.b64decode(previews[0]["data"]))) as thumb:
        assert thumb.size == (30, 26)
    assert not data.exists()

    updates = []
    run(data, dict(action="create_category", value="QQ 收藏"))
    import_req = dict(action="qq_import", paths=[str(second)], category="QQ 收藏", **req)
    assert run(data, import_req, lambda *update: updates.append(update))["count"] == 1
    assert updates == [("import", 0, 1), ("import", 1, 1)]
    assert run(data, import_req)["skipped"] == 1
    library = snapshot(data)
    assert library["images"][0]["categories"] == ["QQ 收藏"]
    assert library["categories"][0]["iconPath"] == library["images"][0]["path"]
    run(data, dict(action="category_icon", category="QQ 收藏", value="🌟"))
    assert run(data, dict(import_req, paths=[str(first)]))["count"] == 1
    assert snapshot(data)["categories"][0]["icon"] == "🌟"
    assert run(data, dict(import_req, paths=[str(picture)]))["failed"] == 1
    assert {file: file.read_bytes() for file in cache.rglob("*") if file.is_file()} == before


def test_qq_scan_reports_walk_checks_and_metadata(tmp_path):
    base = tmp_path / "Tencent Files"
    cache = base / "1234" / "nt_qq" / "nt_data"
    _image(cache / "Emoji" / "personal_emoji" / "Ori" / "one.png")
    _image(cache / "Pic" / "2026-10" / "Ori" / "one.png")
    (cache / "Pic" / "2026-10" / "Ori" / "note.txt").write_text("not an image")
    market = cache / "Emoji" / "marketface"
    market.mkdir()
    (market / "bad.dat").write_bytes(b"not marketface")
    raw = BytesIO()
    Image.new("RGB", (8, 8)).save(raw, "GIF")
    (market / "ok.dat").write_bytes(restore_marketface(raw.getvalue()))
    for kind, total in [("personal_emoji", 1), ("pic", 2), ("marketface", 2)]:
        reports = []
        result = run(tmp_path / "data", dict(action="qq_scan", value=str(base), account="1234", kind=kind),
                     lambda *event: reports.append(event))
        assert len(result["items"]) == 1
        assert reports[0][:3] == ("qq_walk", 0, 0)
        assert any(phase == "qq_walk" and current > 0 and limit == 0 for phase, current, limit, _ in reports)
        assert [(n, limit) for phase, n, limit, _ in reports if phase == "qq_check"] == [
            (n, total) for n in range(total + 1)
        ]
        assert reports[-1][:3] == ("qq_metadata", 1, 1)


def test_qq_recycle_only_selected_unchanged_images_and_never_unlinks(tmp_path, monkeypatch):
    from services.external_stickers import _file_identity

    cache = tmp_path / "1234" / "nt_qq" / "nt_data" / "Emoji" / "personal_emoji"
    ori = cache / "Ori"
    chosen = _image(ori / "所选.png")
    in_use = _image(ori / "占用.png")
    replaced = _image(ori / "已替换.png")
    unselected = _image(ori / "未选.png")
    gallery = ori / "library"
    protected = _image(gallery / "images" / "馆藏.png")
    other_source = _image(cache / "Thumb" / "缩略图.png")
    fake_image = ori / "配置.xml"
    fake_image.write_bytes(b"<?xml version='1.0'?><config/>")
    req = dict(value=str(tmp_path), account="1234", kind="personal_emoji")
    scanned = run(gallery, dict(action="qq_scan", **req))["items"]
    paths = {Path(item["id"]): item for item in scanned}
    assert all(paths[file]["file_identity"] for file in (chosen, protected, replaced))
    replaced.write_bytes(b"new content from QQ")
    quarantine = tmp_path / "fake-bin"
    quarantine.mkdir()
    calls = []

    def fake_recycle(file):
        calls.append(file)
        if file == in_use:
            raise OSError("file in use")
        file.rename(quarantine / file.name)

    monkeypatch.setattr("services.windows_recycle.recycle_file", fake_recycle)
    items = [paths[file] for file in (chosen, in_use, replaced, protected, fake_image)]
    items += [{"id": str(other_source), "file_identity": _file_identity(other_source)}, items[0]]
    progress = []
    out = run(gallery, dict(action="qq_recycle", items=items, **req), lambda *p: progress.append(p))
    assert out["removed_ids"] == [str(chosen)]
    assert (out["count"], out["failed"]) == (1, 5)
    assert calls == [chosen, in_use]
    assert (quarantine / chosen.name).is_file() and not chosen.exists()
    assert all(p.is_file() for p in (unselected, replaced, protected, in_use, other_source, fake_image))
    assert progress == [("qq_recycle", n, 6) for n in range(7)]


def test_qq_apng_and_scrambled_marketface_stay_animated(tmp_path, monkeypatch):
    base = tmp_path / "Tencent Files"
    folder = base / "1234" / "nt_qq" / "nt_data" / "Emoji"
    market = folder / "marketface" / "f.dat"
    market.parent.mkdir(parents=True)
    first = Image.new("RGBA", (20, 20), "red")
    second = Image.new("RGBA", (20, 20), "blue")
    gif = BytesIO()
    first.save(gif, "GIF", save_all=True, append_images=[second], duration=[70, 100], loop=0)
    market.write_bytes(restore_marketface(gif.getvalue()))
    apng = folder / "personal_emoji" / "Ori" / "animated.dat"
    apng.parent.mkdir(parents=True)
    first.save(apng, "PNG", save_all=True, append_images=[second], duration=[90, 120], loop=0)
    data = tmp_path / "data"
    output = tmp_path / "export"
    output.mkdir()
    for kind, file in [("marketface", market), ("personal_emoji", apng)]:
        req = dict(value=str(base), account="1234", kind=kind)
        scanned = run(data, dict(action="qq_scan", **req))["items"]
        assert [Path(item["id"]) for item in scanned] == [file]
        assert (scanned[0]["width"], scanned[0]["height"], scanned[0]["animated"]) == (20, 20, True)
        assert len(run(data, dict(action="qq_previews", items=scanned, **req))["previews"]) == 1
        detail = run(data, dict(action="qq_detail", items=scanned, **req))["previews"][0]
        assert Path(detail["id"]) == file
        with Image.open(BytesIO(base64.b64decode(detail["animation"]))) as motion:
            assert motion.n_frames == 2
            assert motion.info["duration"] == (70 if kind == "marketface" else 90)
        original = file.read_bytes()
        result = run(data, dict(action="qq_export", items=scanned, destination=str(output), **req))
        assert (result["count"], result["failed"], result["cancelled"]) == (1, 0, 0)
        with Image.open(output / (file.stem + ".gif")) as exported:
            assert exported.n_frames == 2
            assert exported.info["duration"] == (70 if kind == "marketface" else 90)
        assert file.read_bytes() == original
        assert run(data, dict(action="qq_import", paths=[str(file)], **req))["count"] == 1
    saved = snapshot(data)["images"]
    assert len(saved) == 2 and all(item["animated"] for item in saved)
    for item in saved:
        with Image.open(item["path"]) as image:
            assert image.n_frames == 2
    monkeypatch.setattr("services.external_stickers.MAX_PREVIEW_FRAME_PIXELS", 1)
    fallback = run(data, dict(action="qq_detail", value=str(base), account="1234", kind="personal_emoji",
                              items=[dict(id=str(apng))]))["previews"][0]
    assert fallback["animation"] is None and fallback["data"]


def test_qq_export_only_writes_requested_copies_and_preserves_existing_files(tmp_path):
    base = tmp_path / "Tencent Files"
    cache = base / "1234" / "nt_qq" / "nt_data"
    ori = cache / "Emoji" / "personal_emoji" / "Ori"
    first = _image(ori / "one.dat", format="PNG")
    second = _image(ori / "two.webp", color="blue")
    outsider = _image(tmp_path / "outside.png")
    output = tmp_path / "exports"
    output.mkdir()
    existing = output / "one.dat.png"
    existing.write_bytes(b"already there")
    data = tmp_path / "never-opened-library"
    req = dict(action="qq_export", value=str(base), account="1234", kind="personal_emoji",
               destination=str(output))
    progress = []
    result = run(data, dict(req, items=[{"id": str(path)} for path in (first, outsider, first)]),
                 lambda *entry: progress.append(entry))
    assert (result["count"], result["failed"]) == (1, 1)
    assert progress == [("qq_export", number, 2) for number in range(3)]
    assert sorted(path.name for path in output.iterdir()) == ["one.dat.png", "one.dat_1.png"]
    assert existing.read_bytes() == b"already there" and (output / "one.dat_1.png").read_bytes() == first.read_bytes()
    assert second.exists() and outsider.exists() and not data.exists()
    assert "error" in run(data, dict(req, destination=str(ori), items=[{"id": str(first)}]))
    data.mkdir()
    assert "error" in run(data, dict(req, destination=str(data), items=[{"id": str(first)}]))
    assert list(data.iterdir()) == []


def test_qq_export_cancellation_keeps_completed_files(tmp_path):
    base = tmp_path / "Tencent Files"
    ori = base / "1234" / "nt_qq" / "nt_data" / "Emoji" / "personal_emoji" / "Ori"
    first, second = (_image(ori / name, color) for name, color in [("first.png", "red"), ("second.png", "blue")])
    output = tmp_path / "exports"
    output.mkdir()
    marker = tmp_path / "cancel"
    marker.touch()
    data = tmp_path / "data"

    def cancel_after_first(phase, completed, total):
        if phase == "qq_export" and completed == 1:
            assert total == 2
            marker.write_text("1")

    result = run(data, dict(action="qq_export", value=str(base), account="1234", kind="personal_emoji",
                            destination=str(output), cancel_file=str(marker),
                            items=[{"id": str(path)} for path in (first, second)]), cancel_after_first)
    assert (result["count"], result["failed"], result["cancelled"]) == (1, 0, 1)
    assert sorted(path.name for path in output.iterdir()) == ["first.png"]
    assert (output / "first.png").read_bytes() == first.read_bytes()
    assert second.exists() and not data.exists()


TOKEN = "123456:synthetic_test_token"


def _fake_telegram(monkeypatch):
    raw = BytesIO()
    Image.new("RGBA", (26, 20), "olive").save(raw, "WEBP")
    calls = []

    class Response:
        status_code = 200
        headers = {}
        content = raw.getvalue()

        def __init__(self, result=None):
            self.result = result

        def json(self):
            return {"ok": True, "result": self.result}

        def raise_for_status(self):
            pass

    def get(_session, url, *, params=None, timeout, allow_redirects):
        assert not allow_redirects
        assert url.startswith(TG_DIRECT_API + "/")
        if url.endswith("/getStickerSet"):
            calls.append(("parse", params["name"]))
            return Response({"name": "test_pack", "title": f"Pack {TOKEN}", "stickers": [
                {"file_id": "actual_first", "emoji": "🙂"},
                {"file_id": "actual_second", "emoji": "👍", "width": 26, "height": 20, "thumbnail": {"file_id": "thumb"}},
                {"file_id": "gone", "emoji": "👻"},
            ]})
        if url.endswith("/getFile"):
            file_id = params["file_id"]
            calls.append(("file", file_id))
            if file_id == "gone":
                raise RuntimeError(f"failure at {url}")
            return Response({"file_path": f"stickers/{file_id}.webp"})
        return Response()

    monkeypatch.setattr("requests.Session.get", get)
    return calls


def test_tg_selected_download_zip_conversion_and_library(tmp_path, monkeypatch):
    calls = _fake_telegram(monkeypatch)
    data = tmp_path / "data"
    req = dict(bot_token=TOKEN)
    meta = run(data, dict(action="tg_parse", value="tg://addemoji?set=test_pack", **req))
    assert TOKEN not in json.dumps(meta)
    assert [item["id"] for item in meta["items"]] == ["actual_first", "actual_second", "gone"]
    assert not data.exists()
    previews = run(data, dict(action="tg_previews", items=meta["items"][1:2], **req))
    assert previews["previews"][0]["id"] == "actual_second"
    assert (meta["items"][1]["width"], meta["items"][1]["height"]) == (26, 20)
    assert ("file", "actual_second") not in calls
    detail = run(data, dict(action="tg_detail", items=meta["items"][1:2], **req))["previews"][0]
    assert ("file", "actual_second") in calls and detail["animation"] is None
    with Image.open(BytesIO(base64.b64decode(detail["data"]))) as image:
        assert image.size == (26, 20)
    dest = tmp_path / "downloads"
    dest.mkdir()
    existing = dest / "test_pack_002.png"
    existing.write_bytes(b"original")
    items = meta["items"][1:]
    updates = []
    result = run(data, dict(action="tg_download", value="test_pack", kind="files", items=items,
                           destination=str(dest), zip_output=True, **req), lambda *u: updates.append(u))
    assert result["count"] == 1 and result["failed"] == 1
    assert existing.read_bytes() == b"original"
    with ZipFile(result["destination"]) as archive:
        assert archive.namelist() == ["test_pack_002_1.png"]
        with Image.open(BytesIO(archive.read("test_pack_002_1.png"))) as image:
            assert image.format == "PNG"
    assert updates == [("download", 0, 2), ("download", 1, 2), ("download", 2, 2)]
    assert calls.count(("parse", "test_pack")) == 1
    assert ("file", "actual_first") not in calls
    assert ("file", "thumb") in calls and ("file", "actual_second") in calls
    assert TOKEN not in json.dumps([result, updates])
    original = run(data, dict(action="tg_download", value="test_pack", kind="files", items=items[:1],
                             destination=str(dest), output_format="original", **req))
    assert original["count"] == 1 and (dest / "test_pack_002.webp").exists()
    imported = run(data, dict(action="tg_download", value="test_pack", kind="library", items=items[:1], **req))
    library = snapshot(data)
    assert imported["count"] == 1 and library["images"][0]["categories"] == ["TG_test_pack"]
    assert library["categories"][0]["iconPath"] == library["images"][0]["path"]


def _fake_batch(monkeypatch):
    png = BytesIO()
    Image.new("RGBA", (24, 16), "coral").save(png, "PNG")

    class Downloader:
        session = SimpleNamespace(close=lambda: None)

        def get_sticker_set(self, link):
            if link == "bad":
                raise RuntimeError(f"network URL with {TOKEN}")
            return StickerPackInfo(link, "同名包", "custom_emoji" if link == "emoji" else "regular",
                                   False, False, [StickerItem(0, "missing", ""), StickerItem(1, "ok", "")])

        def get_file_path(self, file_id):
            if file_id == "missing":
                raise RuntimeError(f"missing at {TOKEN}")
            return file_id

        def download_file_bytes(self, _):
            return png.getvalue()

    monkeypatch.setattr("services.external_stickers._telegram", lambda _: Downloader())


def test_tg_batch_resource_packages_reimport_and_unique_title(tmp_path, monkeypatch):
    _fake_batch(monkeypatch)
    data = tmp_path / "data"
    output = tmp_path / "downloads"
    output.mkdir()
    old = output / "同名包.zip"
    old.write_bytes(b"keep")
    run(data, dict(action="create_category", value="同名包"))
    updates = []
    result = run(data, dict(action="tg_batch_package", links=["first", "bad", "emoji"],
                            destination=str(output), bot_token=TOKEN),
                 lambda *args, **fields: updates.append((args, fields)))
    assert (result["count"], result["failed"], result["cancelled"]) == (2, 1, 0)
    assert [row["status"] for row in result["batch_rows"]] == ["done", "failed", "done"]
    assert TOKEN not in json.dumps([result, updates])
    assert old.read_bytes() == b"keep"
    assert len(list(output.iterdir())) == 3  # no staging remains
    library = snapshot(data)
    icons = {item["name"]: item["iconPath"] for item in library["categories"]}
    assert icons["同名包"] is None  # the existing category stays separate
    for index, category, route in [(0, "同名包 (2)", "addstickers/first"), (2, "同名包 (3)", "addemoji/emoji")]:
        row = result["batch_rows"][index]
        assert "贴纸 #1 获取失败" in row["message"]
        with ZipFile(row["path"]) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            catalog = json.loads(archive.read("catalog.json"))
            assert manifest["format_version"] == 1 and manifest["package_id"] == category
            assert manifest["counts"]["resources"] == 1
            assert catalog["categories"][0]["name"] == category and catalog["categories"][0]["icon"]["type"] == "image"
            assert archive.read("TG.txt").decode().strip() == f"https://t.me/{route}"
        imported = run(tmp_path / "reimport", dict(action="import_zip", value=row["path"]))
        assert imported["count"] + imported["skipped"] == 1
        assert icons[category] == library["images"][0]["path"]
        assert {"parse", "parsed", "download", "import", "export", "done"}.issubset(
            update[0] for update, fields in updates if fields["row"] == index)


@pytest.fixture
def single_tg(tmp_path, monkeypatch):
    animation = BytesIO()
    first = Image.new("RGBA", (18, 24), "red")
    first.save(animation, "PNG", save_all=True, append_images=[Image.new("RGBA", first.size, "blue")],
               duration=[120, 180], loop=0)
    calls = []
    payloads = {"animation": animation.getvalue()}

    class Downloader:
        bot_token = TOKEN
        session = SimpleNamespace(close=lambda: None)

        def parse_sticker_input(self, value):
            return value

        def get_sticker_set(self, value):
            calls.append(("parse", value))
            return StickerPackInfo(value, "包标题", "custom_emoji", True, False,
                                   [StickerItem(0, "animation", ""), StickerItem(1, "missing", "")])

        def get_file_path(self, file_id):
            calls.append(("file", file_id))
            if file_id not in payloads:
                raise RuntimeError(f"missing from {TOKEN}")
            return file_id

        def download_file_bytes(self, path):
            return payloads[path]

    monkeypatch.setattr("services.external_stickers._telegram", lambda _: Downloader())
    data, output = tmp_path / "data", tmp_path / "downloads"
    output.mkdir()
    parsed = run(data, dict(action="tg_parse", value="emoji_test", bot_token=TOKEN))
    req = dict(action="tg_single_package", value=parsed["source_name"], source_url=parsed["source_url"],
               items=parsed["items"], package_id="自定义 ID", destination=str(output), bot_token=TOKEN)
    return data, output / "自定义 ID.zip", req, calls, payloads


def test_tg_single_package_uses_parsed_items_and_reuses_category(single_tg, tmp_path):
    data, output, req, calls, _ = single_tg
    existing = _image(tmp_path / "existing.png")
    assert run(data, dict(action="import", paths=[str(existing)], category=req["package_id"]))["count"] == 1
    updates = []
    result = run(data, req, lambda *update: updates.append(update))
    assert (result["count"], result["skipped"], result["failed"]) == (1, 0, 1)
    assert result["warnings"] == ["贴纸 #2 获取失败"] and Path(result["destination"]) == output
    assert calls.count(("parse", "emoji_test")) == 1
    assert set(calls[1:]) == {("file", "animation"), ("file", "missing")}
    assert TOKEN not in json.dumps([result, updates])
    assert {update[0] for update in updates} == {"download", "import", "export"}
    assert len(list(output.parent.iterdir())) == 1
    library = snapshot(data)
    assert [item["name"] for item in library["categories"]] == [req["package_id"]]
    assert library["categories"][0]["iconPath"]
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        catalog = json.loads(archive.read("catalog.json"))
        assert manifest["format_version"] == 1 and manifest["package_id"] == req["package_id"]
        assert catalog["categories"][0]["name"] == req["package_id"]
        assert manifest["counts"]["resources"] == 2  # Existing pictures in the same category come with it.
        assert archive.read("TG.txt") == b"https://t.me/addemoji/emoji_test\n"
        gif_path = next(resource["asset_path"] for resource in catalog["resources"] if resource["asset_path"].endswith(".gif"))
        with Image.open(BytesIO(archive.read(gif_path))) as gif:
            assert gif.n_frames == 2 and gif.info["duration"] == 120
            assert gif.convert("RGB").getpixel((5, 5)) == (255, 0, 0)
            gif.seek(1)
            assert gif.info["duration"] == 180 and gif.convert("RGB").getpixel((5, 5)) == (0, 0, 255)
    reimported = run(tmp_path / "reimport", dict(action="import_zip", value=str(output)))
    assert reimported["count"] == 2


def test_tg_single_package_overwrite_requires_explicit_consent_and_success(single_tg):
    data, output, req, calls, payloads = single_tg
    output.write_bytes(b"keep")
    result = run(data, req)
    assert "未覆盖" in result["error"]
    assert calls == [("parse", "emoji_test")] and not data.exists()

    # A valid sticker commits before the second one fails cleaning; the existing ZIP stays intact.
    payloads["missing"] = b"corrupt non-image"
    result = run(data, dict(req, overwrite_package=True))
    assert "入库失败" in result["error"] and result["library_changed"]
    assert output.read_bytes() == b"keep" and len(list(output.parent.iterdir())) == 1
    assert len(snapshot(data)["images"]) == 1

    del payloads["missing"]
    result = run(data, dict(req, overwrite_package=True))
    assert (result["count"], result["skipped"], result["failed"]) == (0, 1, 1)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
    assert len(list(output.parent.iterdir())) == 1


@pytest.mark.parametrize("overwrite", [False, True])
def test_tg_single_package_keeps_file_created_or_changed_during_download(single_tg, overwrite):
    data, output, req, _, _ = single_tg
    if overwrite:
        output.write_bytes(b"approved")

    def progress(phase, done, _total):
        if phase == "download" and done == 0:
            output.write_bytes(b"created after dialog")

    result = run(data, dict(req, overwrite_package=overwrite), progress)
    assert "未覆盖" in result["error"] and result["library_changed"]
    assert output.read_bytes() == b"created after dialog"


def test_tg_single_package_rejects_unsafe_targets_before_download(single_tg):
    data, output, req, calls, _ = single_tg
    for invalid in ("", "..", "path/elsewhere", "file:stream", "CON.txt", "LPT²", "CONIN$", "全部表情", "尾点."):
        assert run(data, dict(req, package_id=invalid))["error"]
    assert run(data, dict(req, destination=str(data)))["error"]
    assert run(data, dict(req, source_url="https://other.invalid/source"))["error"]
    output.mkdir()
    assert run(data, dict(req, overwrite_package=True))["error"]
    assert output.is_dir() and not data.exists()
    assert calls == [("parse", "emoji_test")]


def test_tg_batch_cancel_keeps_finished_packages(tmp_path, monkeypatch):
    _fake_batch(monkeypatch)
    output = tmp_path / "downloads"
    output.mkdir()
    marker = tmp_path / "cancel"
    marker.touch()
    data = tmp_path / "data"

    def stop(phase, *_args, row, **_fields):
        if phase == "done" and row == 0:
            marker.write_text("1")

    result = run(data, dict(action="tg_batch_package", links=["first", "second"],
                            destination=str(output), bot_token=TOKEN, cancel_file=str(marker)), stop)
    assert [row["status"] for row in result["batch_rows"]] == ["done", "cancelled"]
    assert len(list(output.iterdir())) == 1
    assert {item["name"] for item in snapshot(data)["categories"]} == {"同名包"}


def test_tg_batch_bridge_does_not_start_network_when_already_cancelled(tmp_path):
    marker = tmp_path / "stop"
    marker.write_text("1")
    data = tmp_path / "data"
    output = tmp_path / "new_directory"
    request = dict(action="tg_batch_package", links=["no_network"], destination=str(output), bot_token=TOKEN,
                   cancel_file=str(marker), report_progress=True)
    ran = subprocess.run([sys.executable, "-B", "-m", "services.gallery_commands", str(data)],
                         input=json.dumps(request), capture_output=True, text=True, encoding="utf-8",
                         timeout=10, check=True)
    assert json.loads(ran.stdout)["batch_rows"][0]["status"] == "cancelled"
    reports = [json.loads(line.removeprefix("SUZU_PROGRESS "))
               for line in ran.stderr.splitlines() if line.startswith("SUZU_PROGRESS ")]
    assert len(reports) == 1 and reports[0]["row"] == 0 and reports[0]["phase"] == "cancelled"
    assert not data.exists() and not output.exists()
    assert TOKEN not in ran.stdout + ran.stderr


def test_tg_token_only_goes_to_expected_https_origin(tmp_path, monkeypatch):
    calls = []

    class Redirect:
        status_code = 302
        headers = {"Location": "http://unexpected.invalid/"}

    def redirect(_session, url, **options):
        calls.append((url.split("/bot")[0], options["allow_redirects"]))
        raise RuntimeError(f"transport failure at {url}") if len(calls) == 1 else AssertionError()

    monkeypatch.setattr("requests.Session.get", redirect)
    req = dict(action="tg_parse", value="test_pack", bot_token=TOKEN)
    assert run(tmp_path, dict(req, api_proxy="http://untrusted.invalid"))["error"]
    assert not calls
    result = run(tmp_path, req)
    assert calls == [(TG_DIRECT_API, False)]
    assert result["error"] and TOKEN not in json.dumps(result)

    def redirect_response(_session, url, **options):
        calls.append((url.split("/bot")[0], options["allow_redirects"]))
        return Redirect()

    monkeypatch.setattr("requests.Session.get", redirect_response)
    result = run(tmp_path, dict(req, api_proxy="https://explicit.invalid/bot-api"))
    assert calls[1:] == [("https://explicit.invalid", False), (TG_DIRECT_API, False)]
    assert result["error"] and TOKEN not in json.dumps(result)
    assert TGStickerDownloader.parse_sticker_input("https://t.me/addstickers/test_pack")


def test_tg_connectivity_uses_explicit_routes_without_exposing_secrets(tmp_path, monkeypatch):
    calls = []

    class User:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"is_bot": True, "username": TOKEN}}

    def get(session, url, **options):
        calls.append((url, session.proxies, session.trust_env, options["allow_redirects"]))
        return User()

    monkeypatch.setattr("requests.Session.get", get)
    proxy = "socks5h://user:password@127.0.0.1:1234"
    request = dict(action="tg_test_connection", bot_token=TOKEN, api_proxy="https://explicit.invalid/api")
    for invalid in ("file://127.0.0.1:1234", "socks5://127.0.0.1", "https://127.0.0.1:65536", "http://x:80?a=b"):
        assert run(tmp_path, dict(request, network_proxy=invalid))["error"]
    assert calls == []
    assert run(tmp_path, dict(request, network_proxy=proxy)) == {}
    assert calls == [(f"https://explicit.invalid/api/bot{TOKEN}/getMe",
                      {"http": proxy, "https": proxy}, False, False)]
    assert not any(tmp_path.iterdir())


def test_sticker_picker_paths_use_existing_config_and_only_allow_absolute_directories(tmp_path):
    import pytest
    from services.gallery_snapshot import snapshot

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"qq_save_path": str(tmp_path), "appearance_mode": "dark"}), encoding="utf-8-sig")
    assert snapshot(tmp_path)["settings"]["qq_save_path"] == str(tmp_path)
    with pytest.raises(ValueError):
        run(tmp_path, {"action": "settings", "settings": {"tg_save_path": "relative"}})
    folders = {key: str(tmp_path / key) for key in ("qq_data_path", "qq_save_path", "tg_save_path", "tg_batch_save_path")}
    assert run(tmp_path, {"action": "settings", "settings": folders}) == {}
    assert snapshot(tmp_path)["settings"] == dict(folders, appearance_mode="dark")


def test_tg_animated_thumbnail_without_thumbnail_id(tmp_path, monkeypatch):
    gif = BytesIO()
    first = Image.new("RGBA", (24, 40), "red")
    first.save(gif, "GIF", save_all=True, append_images=[Image.new("RGBA", first.size, "blue")],
               duration=[120, 180], loop=0)
    still = BytesIO()
    first.save(still, "PNG")
    monkeypatch.setattr(TGStickerDownloader, "get_file_path", lambda self, file_id: f"stickers/{file_id}.tgs")
    monkeypatch.setattr(TGStickerDownloader, "download_file_bytes", lambda self, _: b"synthetic animation")

    def convert(_self, data, ext, target):
        assert data == b"synthetic animation" and ext == "tgs"
        return (gif.getvalue(), "gif") if target == "gif" else (still.getvalue(), "png")

    monkeypatch.setattr(TGStickerDownloader, "process_sticker_data", convert)
    request = dict(bot_token=TOKEN, items=[dict(id="animated", index=0, animated=True)])
    assert run(tmp_path, dict(action="tg_previews", **request))["previews"][0]["data"]
    preview = run(tmp_path, dict(action="tg_detail", **request))["previews"][0]
    with Image.open(BytesIO(base64.b64decode(preview["animation"]))) as detail:
        assert detail.n_frames == 2 and detail.size == (24, 40)
