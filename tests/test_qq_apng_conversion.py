from PIL import Image

from services.qq_extractor import QQExtractor


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
    target = QQExtractor.convert_apng_to_gif(source)
    assert target.endswith('_v2.gif')
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
    a = QQExtractor._quantize_gif_frame(first).convert('RGBA')
    b = QQExtractor._quantize_gif_frame(second).convert('RGBA')
    assert a.tobytes() == b.tobytes()
