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
    使用两步调色板法将视频或动态图片转换为高质量 GIF
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
        # 第一步：生成调色板
        cmd1 = [
            ffmpeg_path,
            "-y",
            "-c:v", "libvpx-vp9",
            "-i", input_path,
            "-vf", "fps=15,scale=320:-1:flags=lanczos,palettegen=reserve_transparent=1",
            palette_path
        ]
        process1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
        _, stderr1 = process1.communicate()
        if process1.returncode != 0:
            raise RuntimeError(f"FFmpeg palettegen failed: {stderr1.decode('utf-8', errors='ignore')}")
            
        # 第二步：使用调色板生成 GIF
        cmd2 = [
            ffmpeg_path,
            "-y",
            "-c:v", "libvpx-vp9",
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
