from pathlib import Path

from PIL import Image
import pytest

from services.gallery_commands import run
from services.gallery_snapshot import snapshot


def test_managed_wallpaper_is_independent_of_source_and_gallery(tmp_path):
    data = tmp_path / "图库"
    source = tmp_path / "背景.png"
    Image.new("RGBA", (1200, 500), (30, 100, 220, 128)).save(source)
    assert snapshot(data)["wallpaper"] is None
    assert run(data, {"action": "set_wallpaper", "value": str(source)}) == {}
    source.rename(tmp_path / "已移动.png")
    saved = snapshot(data)
    asset = Path(saved["wallpaper"]["path"])
    assert asset.parent == data / "appearance"
    with Image.open(asset) as picture:
        assert picture.size == (1200, 500) and picture.getpixel((0, 0)) == (30, 100, 220, 128)
    assert not saved["images"]
    assert run(data, {"action": "settings", "settings": {
        "wallpaper_blur": 17, "wallpaper_brightness": -43, "wallpaper_overlay_opacity": 35,
    }}) == {}
    assert snapshot(data)["settings"]["wallpaper_brightness"] == -43
    assert run(data, {"action": "clear_wallpaper"}) == {}
    assert not asset.exists() and snapshot(data)["wallpaper"] is None
    assert (tmp_path / "已移动.png").is_file()
    assert snapshot(data)["settings"]["wallpaper_blur"] == 17
    assert snapshot(data)["settings"]["wallpaper_overlay_opacity"] == 35


def test_wallpaper_preserves_old_image_on_invalid_replacement_and_limits_large_images(tmp_path):
    data = tmp_path / "data"
    source = tmp_path / "landscape.jpg"
    Image.new("RGB", (4000, 3000), "orange").save(source)
    assert not run(data, {"action": "set_wallpaper", "value": str(source)})
    asset = snapshot(data)["wallpaper"]
    with Image.open(asset["path"]) as picture:
        assert picture.width * picture.height <= 4_000_000
        assert abs(picture.width / picture.height - 4 / 3) < .002
    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image")
    assert run(data, {"action": "set_wallpaper", "value": str(invalid)})["error"]
    assert snapshot(data)["wallpaper"] == asset
    for key, value in [("wallpaper_blur", 41), ("wallpaper_brightness", -101), ("wallpaper_brightness", True),
                       ("wallpaper_overlay_opacity", -1), ("wallpaper_overlay_opacity", 101)]:
        with pytest.raises(ValueError):
            run(data, {"action": "settings", "settings": {key: value}})


def test_wallpaper_honors_exif_orientation(tmp_path):
    source = tmp_path / "rotated.jpg"
    data = tmp_path / "data"
    image = Image.new("RGB", (100, 60), "teal")
    exif = image.getexif()
    exif[274] = 6
    image.save(source, exif=exif)
    assert not run(data, {"action": "set_wallpaper", "value": str(source)})
    with Image.open(snapshot(data)["wallpaper"]["path"]) as saved:
        assert saved.size == (60, 100)


def test_replacing_background_keeps_one_managed_copy_and_leaves_originals(tmp_path):
    data = tmp_path / "data"
    originals = []
    previous = None
    for index, color in enumerate(["red", "blue", "green"]):
        source = tmp_path / f"source-{index}.png"
        Image.new("RGBA", (64, 48), color).save(source)
        originals.append((source, source.read_bytes()))
        assert run(data, {"action": "set_wallpaper", "value": str(source)}) == {}
        wallpaper = snapshot(data)["wallpaper"]
        path = Path(wallpaper["path"])
        assert path != source and path.parent == data / "appearance"
        assert list(path.parent.iterdir()) == [path]
        if previous:
            assert wallpaper["path"] == previous["path"] and wallpaper["revision"] != previous["revision"]
        with Image.open(path) as saved:
            assert saved.tobytes() == Image.new("RGBA", (64, 48), color).tobytes()
        previous = wallpaper
    assert all(source.read_bytes() == content for source, content in originals)
