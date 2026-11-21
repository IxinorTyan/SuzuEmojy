import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest
from PIL import Image, PngImagePlugin

from services.gallery_commands import run
from services.gallery_snapshot import snapshot


def issue(root, action, **fields):
    return run(root, {"action": action, **fields})


def make_png(path, color):
    Image.new("RGBA", (40, 30), color).save(path)


def test_qt_free_command_roundtrip_with_unicode_and_isolated_paths(tmp_path):
    root = tmp_path / "工作 目录"
    source = tmp_path / "导入"
    source.mkdir()
    path = source / "图.png"
    make_png(path, "lime")
    proc = subprocess.run([
        sys.executable, "-B", "-c", "from services.gallery_commands import main; main(); "
        "import sys; assert not any(n.startswith(('PySide', 'qfluentwidgets')) for n in sys.modules)",
        str(root)], input=json.dumps({"action": "import", "paths": [str(source)]}, ensure_ascii=False),
        encoding="utf-8", capture_output=True, check=True)
    assert json.loads(proc.stdout)["count"] == 1
    entry, = snapshot(root)["images"]
    assert entry["categories"] == ["导入"] and path.exists()
    assert str(root) in entry["path"]


def test_folder_import_reports_actual_progress_and_old_summary(tmp_path):
    source = tmp_path / "相册"
    child = source / "动图"
    child.mkdir(parents=True)
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "remove this before saving")
    Image.new("RGB", (40, 30), "lime").save(source / "1.png", pnginfo=info)
    make_png(source / "2.png", "lime")
    (child / "broken.png").write_text("not an image", encoding="utf-8")
    (child / "ignore.txt").write_text("not an image", encoding="utf-8")
    data = tmp_path / "data"
    process = subprocess.run([sys.executable, "-B", "-m", "services.gallery_commands", str(data)],
                             input=json.dumps({"action": "import", "paths": [str(source)], "report_progress": True}),
                             encoding="utf-8", capture_output=True, check=True)
    result = json.loads(process.stdout)
    assert (result["count"], result["skipped"], result["failed"]) == (1, 1, 1)
    stats = result["folder_stats"]
    assert {k: stats[k] for k in ("folder_name", "total_files", "image_count", "non_images", "subdirs", "issue_count")} == {
        "folder_name": "相册", "total_files": 4, "image_count": 3, "non_images": 1, "subdirs": 1, "issue_count": 1,
    }
    assert len(stats["issues"]) == 1 and "broken.png" in stats["issues"][0]
    updates = [json.loads(line.removeprefix("SUZU_PROGRESS ")) for line in process.stderr.splitlines()
               if line.startswith("SUZU_PROGRESS ")]
    assert updates[0]["phase"] == "folder_scan" and updates[0]["total"] == 0
    progress = [(u["current"], u["total"]) for u in updates if u["phase"] == "import"]
    assert progress[0] == (0, 3) and progress[-1] == (3, 3)
    entry, = snapshot(data)["images"]
    with Image.open(entry["path"]) as saved:
        assert saved.format == "PNG" and saved.mode == "RGBA" and "Comment" not in saved.info
    assert (source / "1.png").is_file() and (child / "broken.png").is_file()


def test_library_crud_roundtrip_and_non_destructive_category_deletion(tmp_path):
    root = tmp_path / "data"
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    make_png(a, "red")
    make_png(b, "blue")
    assert issue(root, "import", paths=[str(a), str(b)], category="旧")["count"] == 2
    one, two = (item["path"] for item in snapshot(root)["images"])
    issue(root, "create_category", value="收藏")
    issue(root, "rename_category", category="旧", value="新")
    issue(root, "category_order", categories=["收藏", "新"])
    assert [cat["name"] for cat in snapshot(root)["categories"]] == ["收藏", "新"]
    issue(root, "add", paths=[one], value="收藏")
    issue(root, "category_icon", paths=[one], category="收藏")
    assert snapshot(root)["categories"][0]["iconPath"] == one
    issue(root, "category_icon", category="收藏")
    assert snapshot(root)["categories"][0]["iconPath"] is None
    issue(root, "tags", paths=[one, two], value="快乐 你好")
    issue(root, "add_tags", paths=[one], value="你好 其他")
    issue(root, "remove_tags", paths=[one], value="你好")
    issue(root, "order", paths=[two, one])
    images = snapshot(root)["images"]
    assert [item["path"] for item in images] == [two, one]
    assert images[1]["keywords"] == "快乐 其他"
    issue(root, "recent", paths=[one], limit=2)
    issue(root, "recent", paths=[two], limit=2)
    issue(root, "recent", paths=[one], limit=2)
    assert snapshot(root)["recent"] == [Path(one).name, Path(two).name]
    issue(root, "remove_category", category="新", delete_images=True)
    assert Path(one).exists() and not Path(two).exists()
    assert snapshot(root)["images"][0]["categories"] == ["收藏"]
    with pytest.raises(ValueError, match="图库"):
        issue(root, "delete", paths=[str(a)])
    assert a.exists() and b.exists()
    assert issue(root, "delete", paths=[one])["count"] == 1
    assert not snapshot(root)["images"]


def test_resource_export_split_reimport_and_settings_preserve_existing_data(tmp_path):
    root = tmp_path / "source-data"
    restored = tmp_path / "restored-data"
    source = tmp_path / "image.png"
    make_png(source, "orange")
    assert issue(root, "import", paths=[str(source)], category="表情")["count"] == 1
    entry, = snapshot(root)["images"]
    issue(root, "tags", paths=[entry["path"]], value="测试")
    config = root / "config.json"
    config.write_text('{"some_legacy_key": 123}', encoding="utf-8")
    issue(root, "settings", settings={"appearance_mode": "dark", "quick_panel_width": 450})
    assert snapshot(root)["settings"] == {"some_legacy_key": 123, "appearance_mode": "dark", "quick_panel_width": 450}
    with pytest.raises(ValueError):
        issue(root, "settings", settings={"quick_panel_width": -1})

    exported = tmp_path / "exported"
    exported.mkdir()
    issue(root, "export_files", paths=[entry["path"]], value=str(exported))
    issue(root, "export_files", paths=[entry["path"]], value=str(exported))
    assert len(list(exported.iterdir())) == 2
    assert all(path.read_bytes() == Path(entry["path"]).read_bytes() for path in exported.iterdir())
    archive = tmp_path / "资源包.zip"
    updates = []
    run(root, {"action": "export_zip", "value": str(archive)}, lambda *update: updates.append(update))
    assert updates[-1][:3] == ("exchange", 2, 2)
    with ZipFile(archive) as package:
        manifest = json.loads(package.read("manifest.json"))
        catalog = json.loads(package.read("catalog.json"))
    assert manifest["format_version"] == 1 and manifest["export_scope"] == "all"
    assert len(catalog["resources"]) == 1 and catalog["resources"][0]["keywords"] == ["测试"]
    split = issue(root, "split_zip", value=str(archive), destination=str(exported))
    zip_file, = Path(split["destination"]).glob("*.zip")
    assert zip_file.name == "资源包-part1.zip"
    assert issue(restored, "import_zip", value=str(zip_file))["count"] == 1
    assert issue(restored, "import_zip", value=str(zip_file))["skipped"] == 1
    copy, = snapshot(restored)["images"]
    assert copy["categories"] == ["表情"] and copy["keywords"] == "测试"
    assert Path(copy["path"]).read_bytes() == Path(entry["path"]).read_bytes()


def test_similarity_command_reports_candidates_without_deleting_anything(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    make_png(images / "a.png", "red")
    make_png(images / "b.png", "red")
    updates = []
    result = run(tmp_path, {"action": "similarity"}, lambda *update: updates.append(update))
    assert [set(Path(path).name for path in group) for group in result["groups"]] == [{"a.png", "b.png"}]
    assert {Path(item["path"]).name for item in result["similarity_groups"][0]} == {"a.png", "b.png"}
    assert all(item["width"] > 0 and item["height"] > 0 and item["size"] > 0 and not item["animated"]
               for item in result["similarity_groups"][0])
    assert ("scan", 2, 2) in updates and ("match", 2, 2) in updates
    assert (tmp_path / "cache" / "similarity-v1.db").exists()
    assert sorted(p.name for p in images.iterdir()) == ["a.png", "b.png"]


def test_recording_recent_on_json_only_library_preserves_older_items(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        make_png(images / name, "lime")
    (tmp_path / "recent.json").write_text('["c.png", "b.png"]', encoding="utf-8")
    issue(tmp_path, "recent", paths=[str(images / "a.png")], limit=3)
    assert snapshot(tmp_path)["recent"] == ["a.png", "c.png", "b.png"]
