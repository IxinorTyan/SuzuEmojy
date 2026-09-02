import os
import sys
import subprocess


def _get_ffmpeg_exe():
    """优先使用发布包内置 FFmpeg，开发环境回退到 imageio-ffmpeg。"""
    candidates = []

    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(base_dir, "ffmpeg", "ffmpeg.exe"))
    else:
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(project_dir, "ffmpeg", "ffmpeg.exe"))

    for path in candidates:
        if os.path.isfile(path):
            return path

    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        raise FileNotFoundError(f"Unable to locate FFmpeg: {e}") from e


def is_ffmpeg_available():
    try:
        return os.path.isfile(_get_ffmpeg_exe())
    except Exception:
        return False

def webm_to_png_frame_ffmpeg(input_path, output_path):
    """使用 FFmpeg 提取 WebM 首帧为带透明通道的 PNG。"""
    try:
        ffmpeg_path = _get_ffmpeg_exe()
    except Exception as e:
        raise FileNotFoundError(
            f"Failed to get ffmpeg executable from imageio_ffmpeg: {e}"
        ) from e

    if not os.path.exists(ffmpeg_path):
        raise FileNotFoundError(f"ffmpeg.exe not found at path: {ffmpeg_path}")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    cmd = [
        ffmpeg_path,
        "-y",
        "-c:v", "libvpx-vp9",
        "-i", input_path,
        "-frames:v", "1",
        "-pix_fmt", "rgba",
        "-c:v", "png",
        output_path,
    ]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"FFmpeg first-frame extraction failed: "
            f"{stderr.decode('utf-8', errors='ignore')}"
        )
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("FFmpeg produced an empty PNG output")
    return True


def convert_video_to_gif(input_path, output_path, fps=15, max_size=320, alpha_threshold=128):
    """
    将 WebM/VP8/VP9 视频转换为带透明背景的 GIF。

    GIF 只能表达 1-bit 透明度，不能保留半透明像素；这里会把 alpha
    小于 alpha_threshold 的像素映射为 GIF 的透明索引。调色板和输出帧
    使用同一条 RGBA 滤镜链生成，避免在两次独立解码时丢失 alpha。
    """
    try:
        ffmpeg_path = _get_ffmpeg_exe()
    except Exception as e:
        raise FileNotFoundError(f"Failed to get ffmpeg executable: {e}") from e

    if not os.path.exists(ffmpeg_path):
        raise FileNotFoundError(f"ffmpeg.exe not found at path: {ffmpeg_path}")

    if fps <= 0:
        raise ValueError("fps must be greater than 0")
    if max_size <= 0:
        raise ValueError("max_size must be greater than 0")
    if not 0 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be between 0 and 255")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    # 一次解码后 split：两路使用完全相同的 RGBA 帧。
    # format=rgba 必须放在 palettegen 之前，否则某些 VP9 alpha 输入会
    # 被自动转换为不带 alpha 的 YUV 帧。
    filter_graph = (
        f"format=rgba,fps={fps},"
        f"scale={max_size}:{max_size}:"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        "split[frames][palette_source];"
        "[palette_source]palettegen="
        "reserve_transparent=1:transparency_color=black[palette];"
        f"[frames][palette]paletteuse="
        f"alpha_threshold={alpha_threshold}:diff_mode=rectangle"
    )

    cmd = [
        ffmpeg_path,
        "-y",
        # FFmpeg 的 native VP9 解码器在部分带 alpha 的 WebM 上会
        # 将透明帧降为不透明 yuv420p；libvpx 解码器可以保留 alpha。
        "-c:v", "libvpx-vp9",
        "-i", input_path,
        "-an",
        "-filter_complex", filter_graph,
        "-loop", "0",
        "-gifflags", "+transdiff",
        "-f", "gif",
        output_path,
    ]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"FFmpeg GIF conversion failed: "
            f"{stderr.decode('utf-8', errors='ignore')}"
        )
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("FFmpeg produced an empty GIF output")

    return True


def convert_animated_webp_to_gif(input_path_or_bytes, output_path=None) -> bytes:
    """
    使用 Pillow ImageSequence 将动态 WebP 转换为 GIF，严格保留各帧时长、循环和透明背景
    :param input_path_or_bytes: 文件路径 (str) 或 二进制数据 (bytes)
    :param output_path: 若提供则保存到该路径，否则返回 bytes
    :return: gif bytes
    """
    import io
    from PIL import Image, ImageSequence

    if isinstance(input_path_or_bytes, bytes):
        fp = io.BytesIO(input_path_or_bytes)
    else:
        fp = input_path_or_bytes

    with Image.open(fp) as img:
        is_animated = getattr(img, "is_animated", False)
        if not is_animated or getattr(img, "n_frames", 1) <= 1:
            raise ValueError("The provided WebP is not animated")

        frames = []
        durations = []
        for frame in ImageSequence.Iterator(img):
            frame_rgba = frame.convert("RGBA")
            frames.append(frame_rgba)
            durations.append(frame.info.get("duration", 100))

        loop = img.info.get("loop", 0)

        out_io = io.BytesIO()
        frames[0].save(
            out_io,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=loop,
            disposal=2
        )
        gif_bytes = out_io.getvalue()

    if output_path:
        with open(output_path, "wb") as f:
            f.write(gif_bytes)

    return gif_bytes
