from PySide6.QtGui import QGuiApplication, QImage, QClipboard
from PySide6.QtCore import QObject, QMimeData, QUrl
import os
import re
import html
from services.auto_paste import AutoPasteController, user32
from services.send_media import static_gif

class ClipboardService(QObject):
    def __init__(self, config_service=None):
        super().__init__()
        # 获取系统的剪切板
        self.clipboard = QGuiApplication.clipboard()
        self.config_service = config_service
        self.auto_paste = AutoPasteController(self)
        self._copied_clipboard_sequence = 0
        self.clipboard.dataChanged.connect(self._on_clipboard_changed)
        QGuiApplication.instance().aboutToQuit.connect(self.auto_paste.cancel)

        # 确定缓存目录
        import sys
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        self.cache_dir = os.path.join(base_dir, "data", "cache", "gif_output")
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except Exception as e:
            print(f"[WARNING] 创建 GIF 转换缓存目录失败: {e}")

    def _on_clipboard_changed(self):
        # Windows delivers our own WM_CLIPBOARDUPDATE after setMimeData returns.
        # It can arrive after paste_to_window has started; only a replacement
        # should cancel the request, not this delayed acknowledgement.
        if (not self.clipboard.ownsClipboard()
                or user32.GetClipboardSequenceNumber() != self._copied_clipboard_sequence):
            self.auto_paste.cancel()

    def _get_config_service(self):
        if self.config_service:
            return self.config_service
        try:
            from services.config import ConfigService
            self.config_service = ConfigService()
            return self.config_service
        except Exception:
            return None

    def _convert_static_to_1frame_gif(self, image_path):
        converted = static_gif(image_path, self.cache_dir)
        return os.fspath(converted) if converted else None

    def get_data_from_clipboard(self):
        """
        尝试从系统剪切板读取数据
        优先级: 1.本地文件路径 -> 2.网络图片URL(从HTML提取) -> 3.纯位图
        :return: (type, data)
                 type 为 'file' 时，data 为本地文件绝对路径列表
                 type 为 'network_url' 时，data 为网络图片URL字符串
                 type 为 'image' 时，data 为 QImage 对象
                 什么都没有返回 (None, None)
        """
        mime_data = self.clipboard.mimeData()
        
        # 1. 优先判断是否包含文件路径 (比如右键复制文件)
        if mime_data.hasUrls():
            urls = mime_data.urls()
            filepaths = []
            for url in urls:
                if url.isLocalFile():
                    filepaths.append(url.toLocalFile())
            if filepaths:
                return 'file', filepaths
                
        # 2. 如果是从浏览器右键复制动图，虽然不给文件，但会写入带 <img src> 的 HTML 数据
        if mime_data.hasHtml():
            html_text = mime_data.html()
            # 简单的正则提取 src 属性
            match = re.search(r'<img[^>]+src=["\']([^">]+)["\']', html_text, re.IGNORECASE)
            if match:
                img_url = match.group(1)
                # 必须反编译 HTML 实体字符，否则带有 & 等 token 会导致 403 错误
                img_url = html.unescape(img_url)
                if img_url.startswith("http://") or img_url.startswith("https://"):
                    return 'network_url', img_url
                    
        # 3. 判断是否是纯文本的 URL (比如直接复制了一个图片链接)
        if mime_data.hasText():
            text = mime_data.text().strip()
            if text.startswith("http://") or text.startswith("https://"):
                return 'network_url', text

        # 4. 退而求其次，判断是否包含直接的图像数据 (比如截图/不支持富文本的复制)
        if mime_data.hasImage():
            image = self.clipboard.image()
            if not image.isNull():
                return 'image', image
                
        return None, None

    def copy_image_to_clipboard(self, image_path):
        """
        将本地图片复制到系统剪切板（以文件和图像双重形式，确保聊天软件能识别表情并自适应气泡大小）
        - 若已是 GIF 格式，直接按原文件复制
        - 若是 PNG/JPG/WEBP 等静态图且开启了设置，则转为 1 帧 GIF 路径放入剪贴板
        - 转换或复制异常时尝试回退至原图，失败返回 False，由调用方停止自动粘贴
        :param image_path: 图片绝对路径
        :return: bool 是否复制成功
        """
        self.auto_paste.cancel()
        self._copied_clipboard_sequence = 0
        if not os.path.isfile(image_path):
            return False
        try:
            mime_data = QMimeData()
            final_path = image_path

            # 检查用户是否开启了“静态图转 GIF”设置（默认开启）
            cfg = self._get_config_service()
            convert_enabled = cfg.get("convert_static_to_gif", True) if cfg else True

            _, ext = os.path.splitext(image_path)
            is_gif = ext.lower() == '.gif'

            # 仅在非 GIF 且开关开启时执行转 1 帧 GIF
            if convert_enabled and not is_gif:
                converted_gif = self._convert_static_to_1frame_gif(image_path)
                if converted_gif and os.path.exists(converted_gif):
                    final_path = converted_gif

            # 1. 放入文件路径 (这是支持表情发送的关键，微信QQ等会优先读取路径按表情或图片发送)
            mime_data.setUrls([QUrl.fromLocalFile(final_path)])
            
            # 2. 放入图像数据 (这是兜底，某些只支持接收位图的场景)
            image = QImage(final_path)
            if image.isNull():
                raise ValueError("无法读取图片")
            mime_data.setImageData(image)
                
            self.clipboard.setMimeData(mime_data)
            if QGuiApplication.platformName() == 'windows' and not self.clipboard.ownsClipboard():
                raise RuntimeError("未能取得剪贴板所有权")
            self._copied_clipboard_sequence = user32.GetClipboardSequenceNumber()
            return True
        except Exception as e:
            print(f"复制图片到剪切板失败: {e}")
            # 最终兜底尝试复制原图路径
            try:
                if not os.path.isfile(image_path):
                    return False
                fallback_mime = QMimeData()
                fallback_mime.setUrls([QUrl.fromLocalFile(image_path)])
                img = QImage(image_path)
                if img.isNull():
                    return False
                fallback_mime.setImageData(img)
                self.clipboard.setMimeData(fallback_mime)
                if QGuiApplication.platformName() == 'windows' and not self.clipboard.ownsClipboard():
                    return False
                self._copied_clipboard_sequence = user32.GetClipboardSequenceNumber()
                return True
            except Exception as fallback_err:
                print(f"剪贴板兜底回退复制失败: {fallback_err}")
        return False

    def paste_to_window(self, target):
        return self.auto_paste.start(target, self._copied_clipboard_sequence)
