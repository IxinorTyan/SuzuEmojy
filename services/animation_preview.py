"""Bounded, lossless frame transport to the Windows preview control."""
import base64
from io import BytesIO
import math
from pathlib import Path

from PIL import Image

from services.apng import _decode, is_apng


_MAX_INPUT = 48 * 1024 * 1024
_MAX_FRAMES = 600
_PIXEL_BUDGET = 4_000_000
_SOURCE_PIXELS = 16_000_000


def preview(path, max_side):
    path = Path(path)
    if path.stat().st_size > _MAX_INPUT:
        return {}
    with path.open('rb') as stream:
        apng = is_apng(stream)
        stream.seek(0)
        with Image.open(stream) as image:
            if not apng and image.format != 'WEBP':
                return {}
            count = image.n_frames - (1 if apng and image.info.get('default_image') else 0)
            if not 1 <= count <= _MAX_FRAMES or math.prod(image.size) > _SOURCE_PIXELS:
                return {}
            edge = min(800, max(1, int(max_side)), math.isqrt(_PIXEL_BUDGET // count))
            loop = min(image.info.get('loop', 1), 2_147_483_647)
            if apng:
                stream.seek(0)
                data = stream.read(_MAX_INPUT + 1)
                if len(data) > _MAX_INPUT:
                    return {}
                frames = _decode(data, image.size)
            else:
                def webp_frames():
                    for index in range(count):
                        image.seek(index)
                        frame = image.convert('RGBA')  # Duration is populated during load.
                        yield frame, image.info.get('duration', 100)
                frames = webp_frames()

            encoded = []
            for frame, duration in frames:
                frame.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                png = BytesIO()
                frame.save(png, format='PNG', compress_level=1)
                encoded.append({'png': base64.b64encode(png.getbuffer()).decode('ascii'),
                                'ms': max(20, round(duration or 40))})
            return {'frames': encoded, 'plays': loop}
