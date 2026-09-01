import os
import sys
import subprocess
import imageio_ffmpeg

def is_ffmpeg_available():
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        return os.path.exists(ffmpeg_path)
    except Exception:
        return False

def convert_video_to_gif(input_path, output_path):
    """
    使用两步调色板法将视频（VP8/VP9 等自动解码）转换为高质量透明 GIF
    """
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        raise FileNotFoundError(f"Failed to get ffmpeg executable from imageio_ffmpeg: {e}")

    if not os.path.exists(ffmpeg_path):
        raise FileNotFoundError(f"ffmpeg.exe not found at {ffmpeg_path}")

    import tempfile
    fd, palette_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        # 第一步：生成调色板（自动识别 VP8/VP9 编码器，保留透明通道）
        cmd1 = [
            ffmpeg_path,
            "-y",
            "-i", input_path,
            "-vf", "fps=15,scale=320:-1:flags=lanczos,palettegen=reserve_transparent=1",
            palette_path
        ]
        process1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
        _, stderr1 = process1.communicate()
        if process1.returncode != 0:
            raise RuntimeError(f"FFmpeg palettegen failed: {stderr1.decode('utf-8', errors='ignore')}")

        # 第二步：使用调色板生成 GIF（循环播放，透明度阈值 alpha_threshold=128）
        cmd2 = [
            ffmpeg_path,
            "-y",
            "-i", input_path,
            "-i", palette_path,
            "-lavfi", "fps=15,scale=320:-1:flags=lanczos[x];[x][1:v]paletteuse=alpha_threshold=128",
            "-loop", "0",
            output_path
        ]
        process2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
        _, stderr2 = process2.communicate()
        if process2.returncode != 0:
            raise RuntimeError(f"FFmpeg paletteuse failed: {stderr2.decode('utf-8', errors='ignore')}")

        return True
    finally:
        # 确保清理临时调色板文件
        if os.path.exists(palette_path):
            try:
                os.remove(palette_path)
            except Exception:
                pass


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
