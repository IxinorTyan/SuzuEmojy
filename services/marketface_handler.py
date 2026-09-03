# coding=utf-8
"""QQNT marketface 动图的恢复、校验与临时文件工具。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image


GIF_HEADERS = (b"GIF87a", b"GIF89a")
# marketface 中通常是可直接查看的缩略图或元数据，不是待恢复原图。
AUXILIARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".apng", ".json", ".ini"
}


def is_gif_header(data: bytes) -> bool:
    return len(data) >= 6 and data[:6] in GIF_HEADERS


def restore_marketface(data: bytes) -> bytes:
    """执行 QQNT marketface 的 20 字节 XOR + 30 字节明文循环。"""
    restored = bytearray(data)
    for offset in range(0, len(restored), 50):
        end = min(offset + 20, len(restored))
        for index in range(offset, end):
            restored[index] ^= 0xFF
    return bytes(restored)


def validate_gif(data: bytes) -> int:
    """在内存中完整验证 GIF，返回帧数。"""
    with Image.open(BytesIO(data)) as image:
        image.load()
        frames = getattr(image, "n_frames", 1)
        for frame in range(frames):
            image.seek(frame)
            image.copy().load()
        return frames


def recover_marketface_data(file_path: str) -> Optional[Tuple[bytes, int]]:
    """读取并恢复单个 marketface 文件，成功时返回 (GIF 数据, 帧数)。"""
    try:
        data = Path(file_path).read_bytes()
        restored = data if is_gif_header(data) else restore_marketface(data)
        if not is_gif_header(restored):
            return None
        return restored, validate_gif(restored)
    except Exception:
        return None


def is_marketface_candidate(file_path: str) -> bool:
    """过滤缩略图及元数据，只保留可能是原始 marketface 的文件。"""
    return Path(file_path).suffix.lower() not in AUXILIARY_SUFFIXES


def get_cache_dir() -> str:
    cache_dir = os.path.join(tempfile.gettempdir(), "SuzuEmojy_QQ_GIF_Cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def get_recovered_gif_path(file_path: str, data: Optional[bytes] = None) -> Optional[str]:
    """将 marketface 恢复为临时 GIF，按数据哈希缓存。"""
    try:
        if data is None:
            recovered = recover_marketface_data(file_path)
            if recovered is None:
                return None
            data = recovered[0]

        if not is_gif_header(data):
            return None

        digest = hashlib.md5(data).hexdigest()
        output_path = os.path.join(get_cache_dir(), f"{digest}.gif")
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            temp_path = f"{output_path}.{os.getpid()}.tmp"
            with open(temp_path, "wb") as output:
                output.write(data)
            os.replace(temp_path, output_path)
        return output_path
    except Exception:
        return None
