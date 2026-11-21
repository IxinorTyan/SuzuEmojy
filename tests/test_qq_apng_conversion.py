import hashlib
import io
import struct
import zlib

import pytest
from PIL import Image, PngImagePlugin

from services.apng import PNG_SIGNATURE, _decode, quantize_gif_frame
from services.qq_extractor import QQExtractor
from services.storage import StorageService


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _apng(size, frames, *, cover=None, metadata=b""):
    """Write precise frame rectangles and alpha values without encoder optimization."""
    def scanlines(frame):
        return zlib.compress(b"".join(
            b"\x00" + frame.crop((0, y, frame.width, y + 1)).tobytes()
            for y in range(frame.height)
        ))

    chunks = [
        _chunk(b"IHDR", struct.pack(">IIBBBBB", *size, 8, 6, 0, 0, 0)),
        metadata,
        _chunk(b"acTL", struct.pack(">II", len(frames), 0)),
    ]
    if cover is not None:
        chunks.append(_chunk(b"IDAT", scanlines(cover)))
    sequence = 0
    for i, (frame, x, y, dispose, blend) in enumerate(frames):
        chunks.append(_chunk(b"fcTL", struct.pack(
            ">IIIIIHHBB", sequence, frame.width, frame.height, x, y, 100, 1000, dispose, blend
        )))
        sequence += 1
        compressed = scanlines(frame)
        if i == 0 and cover is None:
            chunks.append(_chunk(b"IDAT", compressed))
        else:
            # Test frame reassembly across two separate fdAT chunks.
            for part in (compressed[:4], compressed[4:]):
                chunks.append(_chunk(b"fdAT", struct.pack(">I", sequence) + part))
                sequence += 1
    return PNG_SIGNATURE + b"".join(chunks) + _chunk(b"IEND", b"")


def test_edges_and_disposal_round_trip(tmp_path):
    source = tmp_path / 'edge.png'
    target = tmp_path / 'edge.gif'
    first = Image.new('RGBA', (8, 8), (255, 0, 255, 0))
    first.putpixel((1, 1), (20, 100, 200, 255))
    first.putpixel((2, 1), (20, 100, 200, 127))
    first.putpixel((3, 1), (20, 100, 200, 128))
    second = Image.new('RGBA', first.size)
    second.putpixel((6, 6), (230, 80, 30, 255))
    first.save(source, save_all=True, append_images=[second],
               duration=[80, 120], loop=0, blend=0, disposal=0)

    assert QQExtractor.convert_apng_to_gif(source, target) == target
    with Image.open(target) as gif:
        assert gif.n_frames == 2
        frame = gif.convert('RGBA')
        assert frame.getpixel((0, 0))[3] == 0
        assert frame.getpixel((1, 1)) == (20, 100, 200, 255)
        assert frame.getpixel((2, 1))[3] == 0
        assert frame.getpixel((3, 1)) == (20, 100, 200, 255)
        assert gif.info['duration'] == 80
        gif.seek(1)
        assert gif.info['duration'] == 120
        frame = gif.convert('RGBA')
        assert frame.getpixel((1, 1))[3] == 0
        assert frame.getpixel((6, 6)) == (230, 80, 30, 255)


def test_default_cover_is_skipped_and_explicit_output_replaced(tmp_path):
    source = tmp_path / 'cover.png'
    target = tmp_path / 'cover.gif'
    cover = Image.new('RGBA', (4, 4), 'red')
    frames = [Image.new('RGBA', (4, 4), color) for color in ('green', 'blue')]
    cover.save(source, save_all=True, append_images=frames, default_image=True,
               duration=[70, 90], loop=1)
    target.write_bytes(b'old export')
    assert QQExtractor.convert_apng_to_gif(source, target) == target
    with Image.open(target) as gif:
        assert gif.n_frames == 2
        assert gif.convert('RGB').getpixel((0, 0)) == (0, 128, 0)
        assert gif.info['duration'] == 70
        assert 'loop' not in gif.info


def test_cache_version_and_finite_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(QQExtractor, 'get_temp_gif_cache_dir', lambda: str(tmp_path))
    source = tmp_path / 'loop.png'
    first = Image.new('RGBA', (4, 4), 'red')
    first.save(source, save_all=True, append_images=[Image.new('RGBA', (4, 4), 'blue')],
               duration=100, loop=3)
    old_key = hashlib.md5(source.read_bytes()).hexdigest()
    (tmp_path / f"{old_key}_v2.gif").write_bytes(b"old conversion")
    target = QQExtractor.convert_apng_to_gif(source)
    assert target.endswith('_v3.gif')
    with Image.open(target) as gif:
        assert gif.info['loop'] == 2
    assert QQExtractor.convert_apng_to_gif(source) == target


def test_hidden_rgb_does_not_change_visible_colors():
    first = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
    second = first.copy()
    for y in range(32):
        for x in range(32):
            second.putpixel((x, y), (x * 8, y * 8, (x + y) * 4, 0))
    for frame in (first, second):
        frame.putpixel((8, 8), (120, 50, 190, 255))
    a = quantize_gif_frame(first).convert('RGBA')
    b = quantize_gif_frame(second).convert('RGBA')
    assert a.tobytes() == b.tobytes()


def test_over_transparent_canvas_does_not_erase_second_gif_frame(tmp_path):
    size = (4, 2)
    first = Image.new("RGBA", size)
    first.putpixel((0, 0), (0, 120, 255, 255))
    second = Image.new("RGBA", (1, 1), (180, 100, 40, 180))
    source, target = tmp_path / "overlay.png", tmp_path / "overlay.gif"
    source.write_bytes(_apng(size, [(first, 0, 0, 0, 0), (second, 2, 1, 0, 1)]))

    assert QQExtractor.convert_apng_to_gif(source, target) == target
    converted, ext, _, _, animated = object.__new__(StorageService).prepare_file(source)
    assert (ext, animated) == (".gif", True)
    assert converted == target.read_bytes()
    with Image.open(target) as gif:
        assert gif.n_frames == 2
        assert gif.convert("RGBA").getpixel((2, 1))[3] == 0
        gif.seek(1)
        frame = gif.convert("RGBA")
        assert frame.getpixel((2, 1)) == (180, 100, 40, 255)
        assert frame.getpixel((0, 0)) == (0, 120, 255, 255)
        assert gif.info["duration"] == 100


def test_default_cover_blend_and_both_disposal_modes():
    size = (5, 1)
    cover = Image.new("RGBA", size, (255, 0, 0, 255))
    base = Image.new("RGBA", (2, 1), (0, 40, 200, 160))
    overlay = Image.new("RGBA", (1, 1), (200, 80, 0, 192))
    solid = Image.new("RGBA", (1, 1), (0, 255, 0, 255))
    clear = Image.new("RGBA", (1, 1))
    source = _apng(size, [(base, 1, 0, 0, 1), (overlay, 2, 0, 2, 1),
                          (solid, 4, 0, 1, 0), (clear, 1, 0, 0, 0)], cover=cover)
    decoded = [frame for frame, _ in _decode(source, size)]
    assert len(decoded) == 4
    assert decoded[0].getpixel((0, 0)) == (0, 0, 0, 0)  # cover is not the canvas
    assert decoded[0].getpixel((1, 0)) == base.getpixel((0, 0))
    expected = Image.alpha_composite(base.crop((0, 0, 1, 1)), overlay).getpixel((0, 0))
    assert decoded[1].getpixel((2, 0)) == expected
    assert decoded[2].getpixel((2, 0)) == base.getpixel((0, 0))  # PREVIOUS restored
    assert decoded[2].getpixel((4, 0)) == solid.getpixel((0, 0))
    assert decoded[3].getpixel((4, 0))[3] == 0  # BACKGROUND cleared
    assert decoded[3].getpixel((1, 0))[3] == 0  # transparent SOURCE replaces
    assert decoded[3].getpixel((2, 0)) == base.getpixel((0, 0))


def test_late_actl_is_converted_at_import(tmp_path):
    first = Image.new("RGBA", (4, 4), "red")
    second = Image.new("RGBA", first.size, "blue")
    data = _apng(first.size, [(first, 0, 0, 0, 0), (second, 0, 0, 0, 0)],
                 metadata=_chunk(b"tEXt", b"Comment\0" + b"x" * 2048))
    assert data.index(b"acTL") > 1024
    source = tmp_path / "with_metadata.png"
    source.write_bytes(data)
    assert QQExtractor.is_apng_file(source)
    assert StorageService.detect_format_magic(data) == "apng"

    storage = object.__new__(StorageService)
    converted, ext, _, _, animated = storage.prepare_file(source)
    assert (ext, animated) == (".gif", True)
    assert converted.startswith(b"GIF")
    with Image.open(io.BytesIO(converted)) as gif:
        assert gif.n_frames == 2
        assert gif.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
        gif.seek(1)
        assert gif.convert("RGB").getpixel((0, 0)) == (0, 0, 255)


def test_actl_letters_inside_static_png_metadata_do_not_trigger_conversion(tmp_path):
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "acTL is text, not a chunk")
    source = tmp_path / "static.png"
    Image.new("RGBA", (4, 4), "red").save(source, pnginfo=metadata)
    assert StorageService.detect_format_magic(source.read_bytes()) == "png"
    assert not QQExtractor.is_apng_file(source)
    converted, ext, _, _, animated = object.__new__(StorageService).prepare_file(source)
    assert converted.startswith(PNG_SIGNATURE)
    assert (ext, animated) == (".png", False)


def test_indexed_apng_keeps_palette_and_transparency(tmp_path):
    source, target = tmp_path / "indexed.png", tmp_path / "indexed.gif"
    first = Image.new("P", (3, 1), 0)
    first.putpalette([0, 0, 0, 20, 60, 200, 180, 30, 10] + [0] * (768 - 9))
    first.putpixel((1, 0), 1)
    second = first.copy()
    second.putpixel((2, 0), 2)
    first.save(source, save_all=True, append_images=[second], duration=[100, 100],
               transparency=b"\x00\xb4\xff", blend=0, disposal=0)
    assert QQExtractor.convert_apng_to_gif(source, target) == target
    with Image.open(target) as gif:
        assert gif.n_frames == 2
        gif.seek(1)
        frame = gif.convert("RGBA")
        assert frame.getpixel((0, 0))[3] == 0
        assert frame.getpixel((1, 0)) == (20, 60, 200, 255)
        assert frame.getpixel((2, 0)) == (180, 30, 10, 255)


def test_truncated_apng_cannot_replace_existing_export(tmp_path):
    source = tmp_path / "invalid.png"
    output = tmp_path / "existing.gif"
    frame = Image.new("RGBA", (4, 4), "red")
    source.write_bytes(_apng(frame.size, [(frame, 0, 0, 0, 0)])[:-12])
    output.write_bytes(b"existing export")
    assert QQExtractor.convert_apng_to_gif(source, output) is None
    assert output.read_bytes() == b"existing export"
    with pytest.raises(RuntimeError, match="missing IEND"):
        object.__new__(StorageService).prepare_file(source)
