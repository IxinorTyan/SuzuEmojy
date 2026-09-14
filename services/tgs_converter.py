"""Telegram TGS（gzip/Lottie）转为可预览、可清洗入库的 PNG/GIF。"""

import gzip
import io
import json
import math
import zlib

from PIL import Image


MAX_JSON_BYTES = 8 * 1024 * 1024


def read_tgs_json(data: bytes) -> dict:
    """限量解压并校验动画头；普通 gzip 和损坏的 TGS 不算图片。"""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("TGS 解压后内容过大")
        animation = json.loads(raw)
        if not isinstance(animation, dict):
            raise ValueError("TGS 内容不是 Lottie 动画")
        if not isinstance(animation.get("v"), str) or not isinstance(animation.get("layers"), list):
            raise ValueError("TGS 缺少 Lottie 版本或图层")
        for key in ("w", "h", "fr", "ip", "op"):
            value = animation.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"TGS 动画参数无效: {key}")
        if not (0 < animation["w"] <= 4096 and 0 < animation["h"] <= 4096):
            raise ValueError("TGS 画布尺寸无效")
        if not (0 < animation["fr"] <= 120 and 0 < animation["op"] - animation["ip"] <= 1200):
            raise ValueError("TGS 帧率或帧数无效")
        if (animation["op"] - animation["ip"]) / animation["fr"] > 10:
            raise ValueError("TGS 动画时长超过 10 秒")
        return animation
    except (OSError, EOFError, UnicodeError, ValueError, OverflowError, zlib.error) as exc:
        raise ValueError(f"无法解析 TGS 动画: {exc}") from exc


def _gif_frame(rgba: Image.Image) -> Image.Image:
    # 为透明背景保留独立色号，避免量化后黑色内容被当成透明像素。
    frame = rgba.convert("RGB").quantize(colors=255)
    palette = frame.getpalette()
    frame.putpalette(palette[:765] + [0, 0, 0])
    frame.paste(255, mask=rgba.getchannel("A").point(lambda alpha: 255 if alpha < 128 else 0))
    frame.info["transparency"] = 255
    return frame


def convert_tgs(data: bytes, target_format: str = "gif") -> bytes:
    """渲染透明 PNG 首帧或循环 GIF；GIF 最高 50 fps，保留总时长。"""
    if target_format not in ("png", "gif"):
        raise ValueError(f"不支持的 TGS 输出格式: {target_format}")
    document = read_tgs_json(data)
    try:
        from rlottie_python import LottieAnimation
        from rlottie_python.rlottie_wrapper import RLOTTIE_LIB
        if RLOTTIE_LIB is None:
            raise OSError("rlottie 动态库不可用")
    except (ImportError, OSError) as exc:
        raise RuntimeError("TGS 转换需要 rlottie-python，请安装或更新 requirements.txt 中的依赖") from exc

    try:
        with LottieAnimation.from_data(json.dumps(document)) as animation:
            total_frames = animation.lottie_animation_get_totalframe()
            if total_frames < 1:
                raise ValueError("TGS 没有可渲染的帧")
            scale = min(1.0, 512 / max(document["w"], document["h"]))
            width = max(1, round(document["w"] * scale))
            height = max(1, round(document["h"] * scale))

            def render(frame_num):
                # rlottie 输出预乘 alpha 的 BGRA；转成普通 RGBA 后再编码。
                buffer = animation.lottie_animation_render(frame_num=frame_num, width=width, height=height)
                return Image.frombytes("RGBa", (width, height), buffer, "raw", "BGRa").convert("RGBA")

            output = io.BytesIO()
            if target_format == "png":
                render(0).save(output, format="PNG")
            else:
                duration = (document["op"] - document["ip"]) / document["fr"]
                count = max(1, math.ceil(duration * min(document["fr"], 50)))
                frames = [_gif_frame(render(min(total_frames - 1, int(i * total_frames / count)))) for i in range(count)]
                # GIF 以 10 ms 计时，累计取整避免逐帧截断导致动画加速。
                ticks = [round(i * duration * 100 / count) for i in range(count + 1)]
                durations = [max(10, (ticks[i + 1] - ticks[i]) * 10) for i in range(count)]
                frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                               duration=durations, loop=0, disposal=2, transparency=255,
                               background=255, optimize=False)
            return output.getvalue()
    except Exception as exc:
        raise RuntimeError(f"TGS 转 {target_format.upper()} 失败: {exc}") from exc
