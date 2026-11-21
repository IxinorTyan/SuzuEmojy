"""Identify APNG chunks and composite frames before converting to GIF."""

import io
import struct
import zlib

from PIL import Image


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HEADER = struct.Struct(">I4s")
_CONTROL = struct.Struct(">IIIIIHHBB")
_CHECKED_CHUNKS = {b"IHDR", b"PLTE", b"tRNS", b"acTL", b"fcTL", b"IDAT", b"fdAT", b"IEND"}


def is_apng(stream):
    """Look for a real acTL chunk before IDAT without loading PNG metadata."""
    if stream.read(8) != PNG_SIGNATURE:
        return False
    while len(header := stream.read(_HEADER.size)) == _HEADER.size:
        length, kind = _HEADER.unpack(header)
        if length > 0x7fffffff or kind in (b"IDAT", b"IEND"):
            return False
        if kind == b"acTL":
            if length != 8 or len(payload := stream.read(8)) != 8:
                return False
            crc = stream.read(4)
            return (int.from_bytes(payload[:4], "big") > 0
                    and len(crc) == 4
                    and int.from_bytes(crc, "big") == zlib.crc32(kind + payload))
        stream.seek(length + 4, io.SEEK_CUR)
    return False


def _chunks(data):
    view = memoryview(data)
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(view):
        length, kind = _HEADER.unpack_from(view, offset)
        end = offset + 8 + length
        if length > 0x7fffffff or end + 4 > len(view):
            raise ValueError("Truncated or oversized PNG chunk")
        payload = view[offset + 8:end]
        if kind in _CHECKED_CHUNKS:
            if zlib.crc32(payload, zlib.crc32(kind)) != int.from_bytes(view[end:end + 4], "big"):
                raise ValueError(f"Invalid PNG chunk checksum: {kind!r}")
        yield kind, payload
        if kind == b"IEND":
            return
        offset = end + 4
    raise ValueError("PNG is missing IEND")


def _chunk(kind, payload):
    return b"".join((_HEADER.pack(len(payload), kind), payload,
                     struct.pack(">I", zlib.crc32(payload, zlib.crc32(kind)))))


def _decode(data, canvas_size):
    canvas = Image.new("RGBA", canvas_size)
    palette = []
    parts = []
    control = None
    seen_idat = False
    declared_frames = decoded_frames = sequence = 0

    def composite_frame():
        if not parts:
            raise ValueError("APNG frame has no image data")
        _, width, height, x, y, delay, denominator, dispose, blend = control
        rect = (x, y, x + width, y + height)
        header = _chunk(b"IHDR", struct.pack(">II", width, height) + image_encoding)
        # Reuse Pillow's regular PNG decoder (palette, tRNS, bit depth and
        # interlace included). APNG's composition is separate: paste with an
        # alpha mask applies the coverage twice to the alpha channel.
        png = b"".join((PNG_SIGNATURE, header, *palette, *parts, _chunk(b"IEND", b"")))
        with Image.open(io.BytesIO(png)) as decoded:
            patch = decoded.convert("RGBA")
        previous = canvas.crop(rect) if dispose == 2 else None
        if blend == 0:  # SOURCE replaces the entire frame rectangle.
            canvas.paste(patch, (x, y))
        else:  # OVER is Porter-Duff source-over, including destination alpha.
            canvas.alpha_composite(patch, (x, y))
        yield canvas.copy(), delay * 1000 / (denominator or 100)
        if dispose == 1:
            canvas.paste((0, 0, 0, 0), rect)
        elif dispose == 2:
            canvas.paste(previous, (x, y))

    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            image_encoding = bytes(payload[8:13])
        elif kind in (b"PLTE", b"tRNS") and not seen_idat:
            palette.append(_chunk(kind, payload))
        elif kind == b"acTL":
            if declared_frames or seen_idat or len(payload) != 8:
                raise ValueError("Invalid APNG animation control")
            declared_frames, _ = struct.unpack(">II", payload)
        elif kind == b"fcTL":
            if control is not None:
                yield from composite_frame()
                decoded_frames += 1
            control = _CONTROL.unpack(payload)
            number, width, height, x, y, _, _, dispose, blend = control
            if (not declared_frames or number != sequence or not width or not height
                    or x + width > canvas_size[0] or y + height > canvas_size[1]
                    or dispose > 2 or blend > 1
                    or (not seen_idat and ((width, height) != canvas_size or x or y))):
                raise ValueError("Invalid APNG frame control")
            sequence += 1
            parts = []
            uses_idat = not seen_idat
        elif kind == b"IDAT":
            seen_idat = True
            if control is not None:
                if not uses_idat:
                    raise ValueError("Unexpected IDAT in APNG frame")
                parts.append(_chunk(b"IDAT", payload))
            # If there is no frame control before IDAT, this is a cover only.
        elif kind == b"fdAT":
            if (control is None or uses_idat or len(payload) < 4
                    or int.from_bytes(payload[:4], "big") != sequence):
                raise ValueError("Invalid APNG frame sequence")
            sequence += 1
            parts.append(_chunk(b"IDAT", payload[4:]))
        elif kind == b"IEND":
            if control is not None:
                yield from composite_frame()
                decoded_frames += 1
            if not seen_idat or not decoded_frames or decoded_frames != declared_frames:
                raise ValueError("Invalid APNG frame count")


def quantize_gif_frame(frame):
    rgba = frame.convert("RGBA")
    transparent = rgba.getchannel("A").point(lambda alpha: 255 if alpha < 128 else 0)
    rgb = rgba.convert("RGB")
    # Hidden RGB must not influence the palette used by visible pixels.
    rgb.paste((0, 0, 0), mask=transparent)
    indexed = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT,
                           dither=Image.Dither.NONE)
    palette = indexed.getpalette()[:765]
    indexed.putpalette(palette + [0] * (768 - len(palette)))
    indexed.paste(255, mask=transparent)
    indexed.info.clear()
    indexed.info["transparency"] = 255
    return indexed


def to_gif(data):
    """Convert a genuine APNG to the GIF variant used for QQ compatibility."""
    if not is_apng(io.BytesIO(data)):
        raise ValueError("Not an APNG")
    # Validate the original header and honor Pillow's image size limit before
    # allocating an RGBA canvas, even if each animated patch is small.
    with Image.open(io.BytesIO(data)) as source:
        size, plays = source.size, source.info.get("loop", 0)
    frames = []
    durations = []
    for frame, duration in _decode(data, size):
        frames.append(quantize_gif_frame(frame))
        durations.append(duration if duration > 0 else 40)
    # APNG counts total plays; GIF counts repeats after its first play.
    loop_options = {"loop": 0 if plays == 0 else plays - 1} if plays != 1 else {}
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                   duration=durations, disposal=2, transparency=255, background=255,
                   optimize=False, **loop_options)
    return output.getvalue()
