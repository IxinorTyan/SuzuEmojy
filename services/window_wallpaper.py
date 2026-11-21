"""Managed wallpaper for the Windows frontend; independent of library images."""
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile


def _location(root):
    folder = root / "appearance"
    path = folder / "window-background"
    # Do not follow a junction or symlink out of the application data directory.
    if folder.resolve() != folder:
        raise ValueError("背景图片目录不可用")
    return path


def snapshot_wallpaper(root):
    try:
        path = _location(root)
        info = path.lstat()
        if path.resolve() == path and stat.S_ISREG(info.st_mode):
            return {"path": str(path), "revision": f"{info.st_mtime_ns}:{info.st_size}:{info.st_ino}"}
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def set_wallpaper(root, source):
    from PIL import Image, ImageOps

    target = _location(root)
    if source is None:
        # The path is fixed, outside the gallery, and unlink never follows the final symlink.
        target.unlink(missing_ok=True)
        return {}

    source = Path(source)
    if not source.is_absolute() or not source.is_file():
        return {"error": "请选择本地图片"}
    try:
        with Image.open(source) as image:
            if image.width * image.height > 64_000_000:
                return {"error": "图片尺寸过大"}
            image.draft("RGB", (2560, 2560))
            picture = ImageOps.exif_transpose(image)
            # Limit both the longest edge and decoded memory, keeping the whole
            # image so that later changes of window ratio can recrop it.
            ratio = min(1., 2560 / max(picture.size), (4_000_000 / (picture.width * picture.height)) ** .5)
            has_alpha = "A" in picture.getbands() or "transparency" in picture.info
            picture = picture.convert("RGBA" if has_alpha else "RGB")
            if ratio < 1:
                size = tuple(max(1, int(length * ratio)) for length in picture.size)
                picture = picture.resize(size, Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
        return {"error": "无法读取背景图片"}

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            if has_alpha:
                picture.save(stream, "PNG")
            else:
                picture.save(stream, "JPEG", quality=92)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {}
