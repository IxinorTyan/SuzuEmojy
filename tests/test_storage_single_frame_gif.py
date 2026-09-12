import os
import threading
from pathlib import Path

from PIL import Image

from services.clipboard import ClipboardService
from services.storage import StorageService


def _make_storage(tmp_path: Path) -> StorageService:
    """创建完全隔离的 StorageService，避免读写项目真实 data 目录。"""
    storage = StorageService.__new__(StorageService)
    storage.lock = threading.RLock()
    storage.base_dir = str(tmp_path)
    storage.data_dir = str(tmp_path / "data")
    storage.images_dir = str(tmp_path / "data" / "images")
    storage.inbox_dir = str(tmp_path / "data" / "inbox")
    storage.inbox_failed_dir = str(tmp_path / "data" / "inbox" / "failed")

    storage.order_file = str(tmp_path / "data" / "order.json")
    storage.categories_file = str(tmp_path / "data" / "categories.json")
    storage.metadata_file = str(tmp_path / "data" / "metadata.json")
    storage.icons_file = str(tmp_path / "data" / "category_icons.json")
    storage.hashes_file = str(tmp_path / "data" / "hashes.json")
    storage.recent_file = str(tmp_path / "data" / "recent.json")

    storage.features_db_path = str(tmp_path / "data" / "features.db")
    storage.metadata_db_path = str(tmp_path / "data" / "metadata.db")
    storage.categories_db_path = str(tmp_path / "data" / "categories.db")
    storage.order_db_path = str(tmp_path / "data" / "order.db")
    storage.recent_db_path = str(tmp_path / "data" / "recent.db")

    for directory in (
        storage.data_dir,
        storage.images_dir,
        storage.inbox_dir,
        storage.inbox_failed_dir,
    ):
        os.makedirs(directory, exist_ok=True)

    storage._ensure_db_tables()
    storage._hashes_cache = {}
    storage._images_cache = []
    storage._images_dirty = True
    storage._categories_cache = {}
    storage._image_to_categories_cache = {}
    storage._categories_dirty = True
    storage._metadata_cache = {}
    storage._metadata_dirty = True
    storage._recent_cache = None
    storage._sync_key_index = None
    return storage


def _make_clipboard_converter(tmp_path: Path) -> ClipboardService:
    """
    绕过 QGuiApplication 初始化，仅复用项目正式的 PNG -> 单帧 GIF 方法。
    该方法只依赖 cache_dir，不依赖系统剪贴板。
    """
    converter = ClipboardService.__new__(ClipboardService)
    converter.cache_dir = str(tmp_path / "gif_cache")
    os.makedirs(converter.cache_dir, exist_ok=True)
    return converter


def _convert_with_project_flow(converter: ClipboardService, png_path: Path) -> Path:
    gif_path = converter._convert_static_to_1frame_gif(str(png_path))
    assert gif_path is not None
    gif_path = Path(gif_path)
    assert gif_path.exists()

    with Image.open(gif_path) as gif:
        assert gif.format == "GIF"
        assert getattr(gif, "n_frames", 1) == 1

    return gif_path


def _save_png(path: Path, image: Image.Image) -> None:
    image.save(path, format="PNG")


def _pixel_bytes(path: str | Path) -> bytes:
    with Image.open(path) as image:
        return image.convert("RGBA").tobytes()


def test_single_frame_gif_is_deduplicated_against_original_png(tmp_path):
    storage = _make_storage(tmp_path)
    converter = _make_clipboard_converter(tmp_path)

    source_png = tmp_path / "source.png"
    image = Image.new("RGBA", (32, 32), (20, 40, 60, 255))
    for x in range(8, 24):
        for y in range(8, 24):
            image.putpixel((x, y), (220, 80, 40, 255))
    _save_png(source_png, image)

    original_path, original_duplicate = storage.save_file(str(source_png))
    gif_path = _convert_with_project_flow(converter, source_png)
    imported_path, gif_duplicate = storage.save_file(str(gif_path))

    assert original_path is not None
    assert original_duplicate is False
    assert gif_duplicate is True
    assert imported_path == original_path
    assert len(os.listdir(storage.images_dir)) == 1


def test_single_frame_gif_is_saved_as_png_when_no_duplicate_exists(tmp_path):
    storage = _make_storage(tmp_path)
    converter = _make_clipboard_converter(tmp_path)

    source_png = tmp_path / "single.png"
    _save_png(source_png, Image.new("RGBA", (20, 20), (45, 120, 210, 255)))
    gif_path = _convert_with_project_flow(converter, source_png)

    saved_path, is_duplicate = storage.save_file(str(gif_path))

    assert saved_path is not None
    assert is_duplicate is False
    assert Path(saved_path).suffix.lower() == ".png"
    with open(saved_path, "rb") as file:
        assert file.read(8) == b"\x89PNG\r\n\x1a\n"


def test_multiframe_gif_remains_gif(tmp_path):
    storage = _make_storage(tmp_path)
    source_gif = tmp_path / "animated.gif"

    frame_one = Image.new("RGBA", (18, 18), (255, 0, 0, 255))
    frame_two = Image.new("RGBA", (18, 18), (0, 0, 255, 255))
    frame_one.save(
        source_gif,
        format="GIF",
        save_all=True,
        append_images=[frame_two],
        duration=[80, 80],
        loop=0,
    )

    saved_path, is_duplicate = storage.save_file(str(source_gif))

    assert saved_path is not None
    assert is_duplicate is False
    assert Path(saved_path).suffix.lower() == ".gif"
    with Image.open(saved_path) as image:
        assert image.format == "GIF"
        assert getattr(image, "n_frames", 1) == 2


def test_different_single_frame_gif_is_not_misidentified(tmp_path):
    storage = _make_storage(tmp_path)
    converter = _make_clipboard_converter(tmp_path)

    first_png = tmp_path / "first.png"
    second_png = tmp_path / "second.png"
    _save_png(first_png, Image.new("RGBA", (24, 24), (255, 30, 30, 255)))
    _save_png(second_png, Image.new("RGBA", (24, 24), (30, 30, 255, 255)))

    first_path, _ = storage.save_file(str(first_png))
    second_gif = _convert_with_project_flow(converter, second_png)
    second_path, is_duplicate = storage.save_file(str(second_gif))

    assert first_path is not None
    assert second_path is not None
    assert is_duplicate is False
    assert second_path != first_path
    assert Path(second_path).suffix.lower() == ".png"
    assert len(os.listdir(storage.images_dir)) == 2


def test_transparent_png_roundtrip_hits_pixel_deduplication(tmp_path):
    storage = _make_storage(tmp_path)
    converter = _make_clipboard_converter(tmp_path)

    source_png = tmp_path / "transparent.png"
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for x in range(6, 26):
        for y in range(6, 26):
            image.putpixel((x, y), (80, 190, 120, 255))
    _save_png(source_png, image)

    original_path, _ = storage.save_file(str(source_png))
    gif_path = _convert_with_project_flow(converter, source_png)

    # 先证明正式转换流程在该二值透明、低色样本上保留 RGBA 像素，
    # 再验证 StorageService 通过现有像素 MD5 命中。
    assert _pixel_bytes(original_path) == _pixel_bytes(gif_path)

    imported_path, is_duplicate = storage.save_file(str(gif_path))
    assert is_duplicate is True
    assert imported_path == original_path
    assert len(os.listdir(storage.images_dir)) == 1


def test_many_color_png_quantization_does_not_cause_false_duplicate(tmp_path):
    """
    项目的正式 GIF 转换会量化到最多 255 色。

    对超过 255 色的 PNG，GIF 往返后的 RGBA 像素通常已变化；在本次只使用
    精确 RGBA MD5 的约束下，不应强行判为重复。该测试记录低风险方案边界，
    并确保没有引入 pHash/dHash 等近似误判。
    """
    storage = _make_storage(tmp_path)
    converter = _make_clipboard_converter(tmp_path)

    source_png = tmp_path / "many_colors.png"
    image = Image.new("RGBA", (32, 32))
    for y in range(32):
        for x in range(32):
            image.putpixel(
                (x, y),
                (
                    (x * 7 + y * 3) % 256,
                    (x * 11 + y * 13) % 256,
                    (x * 17 + y * 19) % 256,
                    255,
                ),
            )
    _save_png(source_png, image)

    original_path, _ = storage.save_file(str(source_png))
    gif_path = _convert_with_project_flow(converter, source_png)

    assert _pixel_bytes(original_path) != _pixel_bytes(gif_path)

    imported_path, is_duplicate = storage.save_file(str(gif_path))
    assert is_duplicate is False
    assert imported_path != original_path
    assert Path(imported_path).suffix.lower() == ".png"
    assert len(os.listdir(storage.images_dir)) == 2
