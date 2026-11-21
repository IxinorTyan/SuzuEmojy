"""Qt-free preparation of the file path sent to a chat client."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from PIL import Image

from services.apng import is_apng, quantize_gif_frame


def static_gif(path, cache_dir):
    """Return a cached one-frame GIF or None; never flatten a multi-frame source."""
    path = Path(path)
    temporary = None
    try:
        info = path.stat()
        with Image.open(path) as image:
            if image.format == "GIF" or getattr(image, "is_animated", False):
                return None
            if image.format == "PNG":
                with path.open("rb") as stream:
                    if is_apng(stream):
                        return None
            key = f"v2:{path.resolve()}:{info.st_mtime_ns}:{info.st_size}"
            target = Path(cache_dir) / (hashlib.sha256(key.encode()).hexdigest() + ".gif")
            if target.is_file() and target.stat().st_size > 0:
                return target

            frame = quantize_gif_frame(image)
            if (latest := path.stat()).st_size != info.st_size or latest.st_mtime_ns != info.st_mtime_ns:
                return None
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as output:
                temporary = output.name
                frame.save(output, format="GIF", transparency=255, disposal=2)
            try:
                os.replace(temporary, target)
            except OSError:
                # On Windows an already-published cache file may be open for reading.
                if not target.is_file() or target.stat().st_size == 0:
                    raise
            return target
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("cache", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    result = static_gif(args.path, args.cache)
    json.dump(os.fspath(result) if result else None, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
