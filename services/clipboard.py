from PySide6.QtGui import QGuiApplication, QImage, QClipboard
from PySide6.QtCore import QObject, QMimeData, QUrl
import os
import re
import html

class ClipboardService(QObject):
    def __init__(self):
        super().__init__()
        # 获取系统的剪切板
        self.clipboard = QGuiApplication.clipboard()

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
                    
        # 3. 退而求其次，判断是否包含直接的图像数据 (比如截图/不支持富文本的复制)
        if mime_data.hasImage():
            image = self.clipboard.image()
            if not image.isNull():
                return 'image', image
                
        return None, None

    def copy_image_to_clipboard(self, image_path):
        """
        将本地图片复制到系统剪切板（以文件和图像双重形式，确保聊天软件能识别动图）
        :param image_path: 图片绝对路径
        :return: bool 是否复制成功
        """
        try:
            mime_data = QMimeData()
            
            # 1. 放入文件路径 (这是支持动图的关键，微信QQ等会优先读取路径按原文件发送)
            mime_data.setUrls([QUrl.fromLocalFile(image_path)])
            
            # 2. 放入图像数据 (这是兜底，某些只支持接收位图的场景)
            image = QImage(image_path)
            if not image.isNull():
                mime_data.setImageData(image)
                
            self.clipboard.setMimeData(mime_data)
            return True
        except Exception as e:
            print(f"复制图片到剪切板失败: {e}")
        return False
