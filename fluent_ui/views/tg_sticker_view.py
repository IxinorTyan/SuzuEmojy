import os
import sys
import time
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QSize, Signal, QCoreApplication, QRect, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter, QFrame,
    QFileDialog, QMessageBox, QLabel, QListWidget, QListWidgetItem, QSizePolicy,
    QDialog, QTabWidget, QTextBrowser, QApplication
)
from PySide6.QtGui import (
    QIcon, QFont, QPixmap, QPainter, QColor, QMovie, QImageReader
)
from qfluentwidgets import (
    LineEdit, PushButton, PrimaryPushButton, ComboBox, CheckBox, ProgressBar,
    TextEdit, FluentIcon as FIF, TransparentToolButton,
    TitleLabel, BodyLabel, SubtitleLabel, ScrollArea, CardWidget, StrongBodyLabel
)

from services.tg_downloader import (
    TGStickerDownloader,
    StickerPackInfo,
    StickerItem,
    DEF_PROXY,
    DEF_CF_PROXY,
    DEF_TOKEN,
    TG_DIRECT_API
)
from services.i18n import t

# Cloudflare Worker 示例脚本
CORS_WORKER_SAMPLE = """export default {
  async fetch(req) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "*"
    };
    if (req.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }
    const url = new URL(req.url);
    if (url.pathname === "/health") {
      return Response.json({ status: "ok" }, { headers: cors });
    }
    const tg = "https://api.telegram.org" + url.pathname + url.search;
    const resp = await fetch(tg, {
      method: req.method,
      headers: req.headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : req.body
    });
    const headers = new Headers(resp.headers);
    Object.entries(cors).forEach(([k, v]) => headers.set(k, v));
    return new Response(resp.body, { status: resp.status, headers });
  }
};"""


# ==================== 使用说明书与教程弹窗 ====================

class TGHelpDialog(QDialog):
    """详细使用说明书与高级配置教程窗口"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Telegram 贴纸下载器 - 使用说明与高级配置教程")
        self.resize(780, 560)
        self.setMinimumSize(600, 450)
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tabs = QTabWidget(self)
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid rgba(0, 0, 0, 0.1);
                background: transparent;
                border-radius: 6px;
            }
            QTabBar::tab {
                background: rgba(0, 0, 0, 0.04);
                border: 1px solid rgba(0, 0, 0, 0.1);
                padding: 8px 16px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: rgba(255, 255, 255, 0.9);
                border-bottom-color: transparent;
                font-weight: bold;
                color: #0078d4;
            }
        """)

        # Tab 1: 贴纸链接获取教程
        tab1 = QWidget()
        tab1_layout = QVBoxLayout(tab1)
        tb1 = QTextBrowser()
        tb1.setOpenExternalLinks(True)
        tb1.setHtml("""
        <div style="font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 13px; line-height: 1.6;">
            <h3 style="color: #0078d4; margin-top:0;">📘 如何获取 Telegram 贴纸链接与包名？</h3>
            <ol>
                <li><b>在客户端中复制链接</b>：在 Telegram 中打开任意聊天，点击任一贴纸/表情，点击<b>「添加贴纸包」</b>进入详情页；</li>
                <li><b>分享并复制链接</b>：点击右上角菜单，选择<b>「分享」</b>或<b>「复制链接」</b>；</li>
                <li><b>支持的常见输入格式</b>：
                    <ul>
                        <li><b>标准贴纸链接</b>：<code>https://t.me/addstickers/FunnyDogs</code></li>
                        <li><b>短链接格式</b>：<code>t.me/addstickers/FunnyDogs</code></li>
                        <li><b>自定义表情包链接</b>：<code>https://t.me/addemoji/MyEmojiPack</code></li>
                        <li><b>TG 协议链接</b>：<code>tg://resolve?domain=addstickers&set=FunnyDogs</code></li>
                        <li><b>直接输入包名</b>：直接输入末尾的短英文名称，例如 <code>FunnyDogs</code> 即可直接解析！</li>
                    </ul>
                </li>
            </ol>
            <h3 style="color: #0078d4;">💡 格式转换说明</h3>
            <ul>
                <li><b>PNG</b>：通用静态图片格式。对于动图或视频贴纸，会智能提取其第一帧高清画面。</li>
                <li><b>GIF</b>：动态图片格式。支持将 <b>TGS (Lottie矢量动画)</b> 与 <b>WebM (VP9高清视频贴纸)</b> 自动转码为可循环播放的 GIF。</li>
                <li><b>原始格式</b>：不进行任何转换，原汁原味保存 Telegram 官方服务器原始文件（<code>.webp</code> / <code>.tgs</code> / <code>.webm</code>）。</li>
            </ul>
        </div>
        """)
        tab1_layout.addWidget(tb1)
        tabs.addTab(tab1, "📘 贴纸链接获取")

        # Tab 2: Cloudflare CORS 代理搭建教程
        tab2 = QWidget()
        tab2_layout = QVBoxLayout(tab2)
        tb2 = QTextBrowser()
        tb2.setOpenExternalLinks(True)
        tb2.setHtml("""
        <div style="font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 13px; line-height: 1.6;">
            <h3 style="color: #0078d4; margin-top:0;">☁️ 为什么需要 Cloudflare Workers 代理？</h3>
            <p>由于部分地区网络无法直接访问 <code>api.telegram.org</code>，本工具内置了官方直连优先 + 备用 Worker 代理的智能路由。如果您想完全使用自己独立稳定的线路，可在 Cloudflare 上免费搭建 1 个 Worker 转发代理。</p>
            
            <h3 style="color: #0078d4;">🚀 快速部署指引</h3>
            <ol>
                <li>登录 <a href="https://dash.cloudflare.com/">Cloudflare 控制台</a>，进入 <b>Workers & Pages</b>，点击 <b>Create Application</b> -> <b>Create Worker</b>；</li>
                <li>点击 <b>Deploy</b> 部署默认模板，然后点击 <b>Quick Edit (快速编辑)</b>；</li>
                <li>清空编辑器中的代码，粘贴下方提供的 <b>Worker 代理脚本</b> 并点击 <b>Save and Deploy</b>；</li>
                <li>部署完成后复制您的 Worker 域名（形如 <code>https://your-worker.workers.dev</code>），填入本软件的高级配置 <b>「CF 代理 URL」</b> 中，点击<b>「测试连通」</b>即可！</li>
            </ol>
        </div>
        """)
        tab2_layout.addWidget(tb2)

        self.codeEdit = TextEdit()
        self.codeEdit.setPlainText(CORS_WORKER_SAMPLE)
        self.codeEdit.setReadOnly(True)
        self.codeEdit.setFont(QFont("Consolas", 10))
        self.codeEdit.setMaximumHeight(140)
        tab2_layout.addWidget(self.codeEdit)

        btn_copy_code = PushButton("📋 复制上方 Worker 部署代码")
        btn_copy_code.clicked.connect(self._copyWorkerCode)
        tab2_layout.addWidget(btn_copy_code)

        tabs.addTab(tab2, "☁️ Cloudflare 代理教程")

        # Tab 3: Bot Token 申请指引
        tab3 = QWidget()
        tab3_layout = QVBoxLayout(tab3)
        tb3 = QTextBrowser()
        tb3.setOpenExternalLinks(True)
        tb3.setHtml("""
        <div style="font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 13px; line-height: 1.6;">
            <h3 style="color: #0078d4; margin-top:0;">🤖 如何免费获取 Telegram Bot Token？</h3>
            <p>本工具已<b>内置默认 Bot Token</b>，通常情况下您无需配置即可直接使用。<br/>
            如果您需要长期稳定下载、避免公用 Token 偶尔触发的 Telegram 速率限制 (Rate Limit)，建议自己向官方申请一个完全属于您自己的免费 Token。</p>
            
            <h3 style="color: #0078d4;">📝 获取步骤 (仅需 1 分钟)</h3>
            <ol>
                <li>在 Telegram 中搜索官方机器人 <b>@BotFather</b> 并点击开启对话（或访问 <a href="https://t.me/BotFather">https://t.me/BotFather</a>）；</li>
                <li>向 BotFather 发送命令 <code>/newbot</code>；</li>
                <li>按照提示输入机器人的<b>昵称</b>（如 <code>MyStickerTool</code>）和<b>用户名</b>（需以 <code>bot</code> 结尾，如 <code>my_sticker_dl_bot</code>）；</li>
                <li>创建成功后，BotFather 会发送一段 HTTP API Token（格式形如 <code>7203628923:AAF5D9vqy5o71egC9zIAb...</code>）；</li>
                <li>将该 Token 复制并粘贴到本软件的高级配置 <b>「Bot Token」</b> 栏中，点击<b>「测试连通」</b>即可完成绑定。</li>
            </ol>
        </div>
        """)
        tab3_layout.addWidget(tb3)
        tabs.addTab(tab3, "🤖 Bot Token 说明")

        layout.addWidget(tabs)

        btn_close = PrimaryPushButton("关闭")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignCenter)

    def _copyWorkerCode(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(CORS_WORKER_SAMPLE)
        QMessageBox.information(self, "复制成功", "Cloudflare Worker 脚本已成功复制到剪贴板！", QMessageBox.Ok)


# ==================== 后台工作线程 ====================

class ParsePackThread(QThread):
    """解析贴纸包元数据线程"""
    success = Signal(object)  # StickerPackInfo
    failed = Signal(str)

    def __init__(self, downloader: TGStickerDownloader, link_or_name: str, parent=None):
        super().__init__(parent)
        self.downloader = downloader
        self.link_or_name = link_or_name

    def run(self):
        try:
            pack = self.downloader.get_sticker_set(self.link_or_name)
            self.success.emit(pack)
        except Exception as e:
            self.failed.emit(str(e))


class BatchThumbnailThread(QThread):
    """
    分批异步下载缩略图线程
    在后台线程内部使用轻量并发池并行获取当前批次图片
    """
    item_loaded = Signal(int, bytes, str)  # sticker_index, png_bytes, badge_text
    batch_done = Signal()

    def __init__(self, downloader: TGStickerDownloader, stickers_batch: List[StickerItem], parent=None):
        super().__init__(parent)
        self.downloader = downloader
        self.stickers_batch = stickers_batch
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _fetch_single(self, sticker: StickerItem):
        if self._is_cancelled:
            return None
        try:
            target_id = sticker.thumbnail_file_id or sticker.file_id
            file_path = self.downloader.get_file_path(target_id)
            raw_bytes = self.downloader.download_file_bytes(file_path)
            
            badge_text = "WEBP"
            if sticker.is_animated:
                badge_text = "TGS"
            elif sticker.is_video:
                badge_text = "WEBM"
            else:
                ext = file_path.split(".")[-1].upper() if "." in file_path else "WEBP"
                badge_text = ext

            ext = file_path.split(".")[-1] if "." in file_path else "webp"
            png_bytes, _ = self.downloader.process_sticker_data(raw_bytes, ext, "png")
            return sticker.index, png_bytes, badge_text
        except Exception:
            return None

    def run(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(self._fetch_single, s) for s in self.stickers_batch]
            for future in as_completed(futures):
                if self._is_cancelled:
                    break
                res = future.result()
                if res and not self._is_cancelled:
                    idx, png_bytes, badge_text = res
                    self.item_loaded.emit(idx, png_bytes, badge_text)
        self.batch_done.emit()


class DetailPreviewThread(QThread):
    """
    单张贴纸大图 / 动图后台异步下载与转换线程
    """
    preview_ready = Signal(int, str, bool)  # sticker_index, file_path, is_gif
    preview_failed = Signal(int, str)

    def __init__(self, downloader: TGStickerDownloader, sticker: StickerItem, parent=None):
        super().__init__(parent)
        self.downloader = downloader
        self.sticker = sticker
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self._is_cancelled:
                return

            target_fmt = "gif" if (self.sticker.is_animated or self.sticker.is_video) else "png"
            raw_bytes, ext = self.downloader.download_sticker(self.sticker, target_format=target_fmt)

            if self._is_cancelled:
                return

            temp_file = os.path.join(tempfile.gettempdir(), f"tg_preview_{self.sticker.file_id[:16]}.{ext}")
            with open(temp_file, "wb") as f:
                f.write(raw_bytes)

            if not self._is_cancelled:
                self.preview_ready.emit(self.sticker.index, temp_file, target_fmt == "gif")
        except Exception as e:
            if not self._is_cancelled:
                self.preview_failed.emit(self.sticker.index, str(e))


class DownloadPackThread(QThread):
    """批量下载贴纸包线程"""
    progress = Signal(int, int, str)
    finished_all = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        downloader: TGStickerDownloader,
        link_or_name: str,
        output_dir: str,
        fmt: str,
        to_zip: bool,
        selected_indices: Optional[List[int]],
        max_workers: int,
        parent=None,
    ):
        super().__init__(parent)
        self.downloader = downloader
        self.link_or_name = link_or_name
        self.output_dir = output_dir
        self.fmt = fmt
        self.to_zip = to_zip
        self.selected_indices = selected_indices
        self.max_workers = max_workers

    def run(self):
        try:
            def _cb(done, total, msg, extra=None):
                self.progress.emit(done, total, msg)

            res = self.downloader.download_pack(
                name_or_url=self.link_or_name,
                output_dir=self.output_dir,
                format=self.fmt,
                to_zip=self.to_zip,
                selected_indices=self.selected_indices,
                max_workers=self.max_workers,
                progress_callback=_cb,
            )
            self.finished_all.emit(res)
        except Exception as e:
            self.failed.emit(str(e))


class ImportPackThread(QThread):
    """
    异步下载并入库到表情包存储服务的后台线程
    彻底释放 GUI 主线程，避免界面卡死
    全部由 storage.save_file 统一魔数识别与清洗转码入库
    """
    progress = Signal(int, int, str)  # done, total, msg
    finished_all = Signal(int, int, int, str)  # imported, duplicated, failed, category_name
    failed = Signal(str)

    def __init__(
        self,
        downloader: TGStickerDownloader,
        storage_service: Any,
        target_stickers: List[StickerItem],
        category_name: str,
        max_workers: int = 4,
        parent=None,
    ):
        super().__init__(parent)
        self.downloader = downloader
        self.storage = storage_service
        self.target_stickers = target_stickers
        self.category_name = category_name
        self.max_workers = max_workers
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        temp_dir = tempfile.mkdtemp(prefix="tg_import_")
        total_count = len(self.target_stickers)
        imported_count = 0
        dup_count = 0
        fail_count = 0

        try:
            # 创建/确保分类存在
            self.storage.add_category(self.category_name)

            def _process_single(sticker: StickerItem):
                if self._is_cancelled:
                    return None
                src_file = None
                try:
                    file_path = self.downloader.get_file_path(sticker.file_id)
                    raw_ext = file_path.split(".")[-1] if "." in file_path else "webp"
                    raw_bytes = self.downloader.download_file_bytes(file_path)

                    src_file = os.path.join(temp_dir, f"{sticker.index + 1:03d}.{raw_ext}")
                    with open(src_file, "wb") as f:
                        f.write(raw_bytes)

                    dest_path, is_dup = self.storage.save_file(src_file)
                    if dest_path:
                        self.storage.add_image_to_category(dest_path, self.category_name)
                        return True, is_dup, sticker.index, None
                    return False, False, sticker.index, "保存文件失败"
                except Exception as exc:
                    return False, False, sticker.index, str(exc)
                finally:
                    if src_file and os.path.exists(src_file):
                        try:
                            os.remove(src_file)
                        except Exception:
                            pass

            # 使用可控并发线程池并发下载与存储
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(_process_single, s): s for s in self.target_stickers}
                done_count = 0
                for future in as_completed(futures):
                    if self._is_cancelled:
                        break
                    res = future.result()
                    done_count += 1
                    if res:
                        ok, is_dup, idx, err = res
                        if ok:
                            imported_count += 1
                            if is_dup:
                                dup_count += 1
                            self.progress.emit(done_count, total_count, f"已入库 ({done_count}/{total_count}): 贴纸 #{idx + 1}")
                        else:
                            fail_count += 1
                            self.progress.emit(done_count, total_count, f"入库失败: 贴纸 #{idx + 1} ({err})")
                    else:
                        fail_count += 1

            if not self._is_cancelled:
                self.finished_all.emit(imported_count, dup_count, fail_count, self.category_name)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ==================== TG 贴纸下载界面 ====================

class TGStickerInterface(QWidget):
    """Telegram 贴纸包批量下载与入库工具界面 (View)"""
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("TGStickerInterface")

        self.downloader = TGStickerDownloader()
        self.current_pack: Optional[StickerPackInfo] = None
        self.save_path = os.path.abspath(os.path.join(".", "downloads"))
        
        # 懒加载相关的状态变量
        self.all_stickers: List[StickerItem] = []      # 当前贴纸包所有贴纸元数据
        self.loaded_sticker_count = 0                  # 已渲染并加载缩略图的数量
        self.batch_size = 30                           # 每次滚动懒加载的贴纸数量
        self.is_batch_loading = False                  # 防止重复触发懒加载
        
        # 缩略图与大图缓存字典
        self._thumb_pixmaps: Dict[int, QPixmap] = {}   # {sticker_index: QPixmap}
        self._detail_cache: Dict[int, Tuple[str, bool]] = {}  # {sticker_index: (file_path, is_gif)}
        
        # 异步线程引用
        self._batch_thread: Optional[BatchThumbnailThread] = None
        self._detail_thread: Optional[DetailPreviewThread] = None
        self._download_thread: Optional[DownloadPackThread] = None
        self._import_thread: Optional[ImportPackThread] = None
        self._parse_thread: Optional[ParsePackThread] = None
        self.detail_movie: Optional[QMovie] = None
        self.last_download_result: Optional[Dict[str, Any]] = None

        self._init_ui()

    def _init_ui(self):
        # 主布局：垂直布局，顶栏 + 内容区 (与 QQ 扫描页面视觉风格完全一致)
        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(36, 10, 36, 12)
        self.mainLayout.setSpacing(12)

        # 顶部返回工具栏
        self.topBar = QWidget(self)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(0, 0, 0, 0)
        self.topBarLayout.setSpacing(12)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip("返回主面板")
        self.btnBack.clicked.connect(self.back_requested.emit)

        self.titleLabel = TitleLabel("下载TG贴纸", self.topBar)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()

        self.mainLayout.addWidget(self.topBar)

        # 内容分割器 (左控制面板 + 右预览面板)
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)

        # ====== 左侧控制面板 ======
        self.leftScrollArea = ScrollArea(self.splitter)
        self.leftScrollArea.setWidgetResizable(True)
        self.leftScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.leftScrollArea.enableTransparentBackground()
        self.leftScrollArea.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.leftScrollArea.setMinimumWidth(330)

        self.leftWidget = QWidget()
        self.leftWidget.setStyleSheet("QWidget { background-color: transparent; }")
        self.leftWidget.setMinimumWidth(320)
        self.leftLayout = QVBoxLayout(self.leftWidget)
        self.leftLayout.setContentsMargins(0, 0, 8, 0)
        self.leftLayout.setSpacing(12)

        # 1. 路径与配置卡片
        self.configCard = CardWidget(self.leftWidget)
        config_layout = QVBoxLayout(self.configCard)
        config_layout.setContentsMargins(14, 12, 14, 12)
        config_layout.setSpacing(10)

        card_title = StrongBodyLabel("Telegram 贴纸配置", self.configCard)
        config_layout.addWidget(card_title)

        self.formLayout = QFormLayout()
        self.formLayout.setSpacing(8)
        self.formLayout.setLabelAlignment(Qt.AlignLeft)

        # 贴纸链接/包名输入
        url_layout = QHBoxLayout()
        url_layout.setSpacing(6)
        self.urlInputEdit = LineEdit(self.configCard)
        self.urlInputEdit.setPlaceholderText("贴纸链接或包名，如: animals 或 https://t.me/addstickers/xxx")
        self.urlInputEdit.returnPressed.connect(self.startParsePack)

        self.helpButton = TransparentToolButton(FIF.HELP, self.configCard)
        self.helpButton.setFixedSize(28, 28)
        self.helpButton.setToolTip("使用帮助与教程")
        self.helpButton.clicked.connect(self.showHelpDialog)

        url_layout.addWidget(self.urlInputEdit, 1)
        url_layout.addWidget(self.helpButton)

        url_label = BodyLabel("贴纸链接:", self.configCard)
        self.formLayout.addRow(url_label, url_layout)

        # 保存路径选择
        save_path_layout = QHBoxLayout()
        save_path_layout.setSpacing(6)
        self.savePathEdit = LineEdit(self.configCard)
        self.savePathEdit.setText(self.save_path)
        self.savePathEdit.setPlaceholderText("请选择贴纸保存路径...")
        self.selectDirButton = PushButton("浏览...", self.configCard)
        self.selectDirButton.setFixedWidth(80)
        self.selectDirButton.clicked.connect(self.selectSavePath)
        save_path_layout.addWidget(self.savePathEdit)
        save_path_layout.addWidget(self.selectDirButton)

        save_path_label = BodyLabel("保存路径:", self.configCard)
        self.formLayout.addRow(save_path_label, save_path_layout)

        # 导出格式选择
        self.formatComboBox = ComboBox(self.configCard)
        self.formatComboBox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.formatComboBox.addItem("PNG (静态图片 / 动图首帧)", userData="png")
        self.formatComboBox.addItem("GIF (动图 / 视频贴纸)", userData="gif")
        self.formatComboBox.addItem("原始格式 (WebP / TGS / WebM)", userData="original")
        self.formatComboBox.addItem("智能适配 (自动匹配最佳格式)", userData="auto")

        format_label = BodyLabel("导出格式:", self.configCard)
        self.formLayout.addRow(format_label, self.formatComboBox)

        # 导出选项 (ZIP 勾选)
        self.zipCheckBox = CheckBox("导出完成后打包为 ZIP 压缩文件", self.configCard)
        self.zipCheckBox.setChecked(True)
        zip_label = BodyLabel("ZIP 打包:", self.configCard)
        self.formLayout.addRow(zip_label, self.zipCheckBox)

        config_layout.addLayout(self.formLayout)

        # 高级配置折叠按钮与内嵌配置项
        self.toggleAdvBtn = PushButton("▶ 展开高级设置 (Token / 网络代理)", self.configCard)
        self.toggleAdvBtn.clicked.connect(self.toggleAdvancedSettings)
        config_layout.addWidget(self.toggleAdvBtn)

        self.advWidget = QWidget(self.configCard)
        adv_widget_layout = QVBoxLayout(self.advWidget)
        adv_widget_layout.setContentsMargins(0, 4, 0, 0)
        adv_widget_layout.setSpacing(6)

        adv_form = QFormLayout()
        adv_form.setSpacing(6)
        adv_form.setLabelAlignment(Qt.AlignLeft)

        # Bot Token
        token_layout = QHBoxLayout()
        token_layout.setSpacing(6)
        self.tokenEdit = LineEdit(self.advWidget)
        self.tokenEdit.setEchoMode(LineEdit.Password)
        self.tokenEdit.setPlaceholderText("内置默认 Token，可填自定义 Token")

        self.toggleTokenBtn = PushButton("👁️ 显示", self.advWidget)
        self.toggleTokenBtn.setFixedWidth(65)
        self.toggleTokenBtn.clicked.connect(self.toggleTokenVisibility)

        token_layout.addWidget(self.tokenEdit)
        token_layout.addWidget(self.toggleTokenBtn)

        lbl_token = BodyLabel("Bot Token:", self.advWidget)
        adv_form.addRow(lbl_token, token_layout)

        # CF 代理 URL
        self.cfProxyEdit = LineEdit(self.advWidget)
        self.cfProxyEdit.setPlaceholderText(f"如: {DEF_CF_PROXY}")
        lbl_cf = BodyLabel("CF 代理 URL:", self.advWidget)
        adv_form.addRow(lbl_cf, self.cfProxyEdit)

        # 本地代理
        self.proxyEdit = LineEdit(self.advWidget)
        self.proxyEdit.setPlaceholderText("如 http://127.0.0.1:7890 (留空为官方直连/CF路由)")
        lbl_proxy = BodyLabel("本地代理:", self.advWidget)
        adv_form.addRow(lbl_proxy, self.proxyEdit)

        adv_widget_layout.addLayout(adv_form)

        cfg_btns_layout = QHBoxLayout()
        cfg_btns_layout.setSpacing(8)
        self.testNetButton = PushButton(FIF.IOT, "测试连通性", self.advWidget)
        self.testNetButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.testNetButton.clicked.connect(self.testNetworkConnection)

        self.resetCfgButton = PushButton(FIF.SYNC, "恢复默认", self.advWidget)
        self.resetCfgButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.resetCfgButton.clicked.connect(self.resetConfig)

        cfg_btns_layout.addWidget(self.testNetButton, 1)
        cfg_btns_layout.addWidget(self.resetCfgButton, 1)
        adv_widget_layout.addLayout(cfg_btns_layout)

        self.advWidget.setVisible(False)
        config_layout.addWidget(self.advWidget)

        self.leftLayout.addWidget(self.configCard)

        # 2. 操作与提取卡片
        self.actionsCard = CardWidget(self.leftWidget)
        actions_layout = QVBoxLayout(self.actionsCard)
        actions_layout.setContentsMargins(14, 12, 14, 12)
        actions_layout.setSpacing(10)

        actions_title = StrongBodyLabel("操作与提取", self.actionsCard)
        actions_layout.addWidget(actions_title)

        # 核心解析扫描按钮
        self.parseButton = PrimaryPushButton(FIF.SEARCH, "解析贴纸包预览", self.actionsCard)
        self.parseButton.setFixedHeight(34)
        self.parseButton.clicked.connect(self.startParsePack)
        actions_layout.addWidget(self.parseButton)

        # 导出操作 (双列并排)
        export_btn_layout = QHBoxLayout()
        export_btn_layout.setSpacing(8)
        self.exportSelectedButton = PushButton(FIF.DOWNLOAD, "导出选中", self.actionsCard)
        self.exportSelectedButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.exportSelectedButton.setEnabled(False)
        self.exportSelectedButton.clicked.connect(self.exportSelected)
        self.exportAllButton = PushButton(FIF.FOLDER, "导出全部", self.actionsCard)
        self.exportAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.exportAllButton.setEnabled(False)
        self.exportAllButton.clicked.connect(self.exportAll)
        export_btn_layout.addWidget(self.exportSelectedButton, 1)
        export_btn_layout.addWidget(self.exportAllButton, 1)
        actions_layout.addLayout(export_btn_layout)

        # 导入操作 (双列并排)
        import_btn_layout = QHBoxLayout()
        import_btn_layout.setSpacing(8)
        self.importSelectedButton = PushButton(FIF.SAVE, "入库选中", self.actionsCard)
        self.importSelectedButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.importSelectedButton.setEnabled(False)
        self.importSelectedButton.clicked.connect(self.importSelected)
        self.importAllButton = PushButton(FIF.APPLICATION, "入库全部", self.actionsCard)
        self.importAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.importAllButton.setEnabled(False)
        self.importAllButton.clicked.connect(self.importAll)
        import_btn_layout.addWidget(self.importSelectedButton, 1)
        import_btn_layout.addWidget(self.importAllButton, 1)
        actions_layout.addLayout(import_btn_layout)

        self.leftLayout.addWidget(self.actionsCard)

        # 3. 进度条与状态
        self.progressBar = ProgressBar(self.leftWidget)
        self.leftLayout.addWidget(self.progressBar)

        # 4. 日志输出框
        self.logTextEdit = TextEdit(self.leftWidget)
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setMinimumHeight(110)
        self.logTextEdit.setStyleSheet("""
            TextEdit {
                font-family: 'Segoe UI', 'Microsoft YaHei', Consolas;
                font-size: 12px;
                border-radius: 6px;
            }
        """)
        self.leftLayout.addWidget(self.logTextEdit)

        # 状态及致谢声明
        self.statusLabel = BodyLabel("", self.leftWidget)
        self.statusLabel.setStyleSheet("color: #666666; font-size: 11px;")
        self.leftLayout.addWidget(self.statusLabel)

        self.thanksLabel = BodyLabel(self.leftWidget)
        self.thanksLabel.setText('致谢：基于 <a href="https://github.com/Kiowx" style="color: #0078d4; text-decoration: underline;">Kiowx</a> 的项目二次开发')
        self.thanksLabel.setOpenExternalLinks(True)
        self.thanksLabel.setStyleSheet("color: #888888; font-size: 11px;")
        self.leftLayout.addWidget(self.thanksLabel)

        self.leftScrollArea.setWidget(self.leftWidget)

        # ====== 右侧贴纸包预览区域 ======
        self.rightWidget = QWidget(self.splitter)
        self.rightWidget.setMinimumWidth(320)
        self.rightLayout = QVBoxLayout(self.rightWidget)
        self.rightLayout.setContentsMargins(0, 0, 0, 0)
        self.rightLayout.setSpacing(10)

        preview_header_layout = QHBoxLayout()
        self.previewTitleLabel = SubtitleLabel("贴纸预览区 (未加载)", self.rightWidget)
        preview_header_layout.addWidget(self.previewTitleLabel)
        preview_header_layout.addStretch()

        self.selectAllButton = PushButton("全选已加载", self.rightWidget)
        self.selectAllButton.clicked.connect(self.selectAllLoaded)
        preview_header_layout.addWidget(self.selectAllButton)

        self.clearSelectionButton = PushButton("清空选择", self.rightWidget)
        self.clearSelectionButton.clicked.connect(self.clearSelection)
        preview_header_layout.addWidget(self.clearSelectionButton)

        self.invertSelectionButton = PushButton("反选", self.rightWidget)
        self.invertSelectionButton.clicked.connect(self.invertSelection)
        preview_header_layout.addWidget(self.invertSelectionButton)

        self.rightLayout.addLayout(preview_header_layout)

        self.previewListWidget = QListWidget(self.rightWidget)
        self.previewListWidget.setViewMode(QListWidget.IconMode)
        self.previewListWidget.setResizeMode(QListWidget.Adjust)
        self.previewListWidget.setIconSize(QSize(100, 100))
        self.previewListWidget.setGridSize(QSize(110, 110))
        self.previewListWidget.setSelectionMode(QListWidget.ExtendedSelection)
        self.previewListWidget.setDragEnabled(False)
        self.previewListWidget.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: 1px solid rgba(0, 0, 0, 15);
                border-radius: 8px;
            }
            QListWidget::item {
                width: 100px;
                height: 100px;
                border: 2px solid transparent;
                border-radius: 6px;
                margin: 4px;
                padding: 0px;
            }
            QListWidget::item:hover {
                background-color: rgba(0, 0, 0, 10);
            }
            QListWidget::item:selected {
                background-color: rgba(0, 120, 212, 30);
                border: 2px solid #0078d4;
            }
        """)
        self.previewListWidget.verticalScrollBar().valueChanged.connect(self.onScrollBarMoved)
        self.previewListWidget.itemSelectionChanged.connect(self.onItemSelectionChanged)
        self.rightLayout.addWidget(self.previewListWidget)

        # ====== 最右侧单个贴纸详细预览区域 ======
        self.detailWidget = QWidget(self)
        self.detailLayout = QVBoxLayout(self.detailWidget)
        self.detailLayout.setContentsMargins(10, 0, 0, 0)
        self.detailLayout.setSpacing(10)
        self.detailWidget.setFixedWidth(280)

        detail_title = SubtitleLabel("贴纸详细预览", self.detailWidget)
        self.detailLayout.addWidget(detail_title)

        self.detailPreviewLabel = QLabel(self.detailWidget)
        self.detailPreviewLabel.setAlignment(Qt.AlignCenter)
        self.detailPreviewLabel.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.detailPreviewLabel.setFixedSize(250, 250)
        self.detailPreviewLabel.setStyleSheet("background-color: rgba(0, 0, 0, 5); border: 1px solid rgba(0, 0, 0, 15); border-radius: 5px;")
        self.detailLayout.addWidget(self.detailPreviewLabel, alignment=Qt.AlignCenter)

        self.detailInfoLabel = BodyLabel("未选中贴纸", self.detailWidget)
        self.detailInfoLabel.setWordWrap(True)
        self.detailInfoLabel.setFixedWidth(250)
        self.detailInfoLabel.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.detailLayout.addWidget(self.detailInfoLabel)

        self.detailLayout.addStretch()

        # 分割器大小配置
        self.splitter.addWidget(self.leftScrollArea)
        self.splitter.addWidget(self.rightWidget)
        self.splitter.setSizes([360, 740])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        content_layout = QHBoxLayout()
        content_layout.addWidget(self.splitter, stretch=1)
        content_layout.addWidget(self.detailWidget)

        self.mainLayout.addLayout(content_layout)

        self.log("💬 Telegram 贴纸包批量下载工具已就绪")
        self.log("💡 支持官方直连与智能路由回退，在上方输入贴纸包链接即可开始解析。")

    # ==========================================
    # 日志输出与辅助函数
    # ==========================================

    def log(self, message: str):
        self.logTextEdit.append(message)
        self.statusLabel.setText(message)
        scrollbar = self.logTextEdit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.logTextEdit.ensureCursorVisible()

    def showHelpDialog(self):
        """弹出说明书与高级配置教程弹窗"""
        dlg = TGHelpDialog(self)
        dlg.exec()

    def toggleAdvancedSettings(self):
        """切换高级配置面板的展开与折叠"""
        is_visible = self.advWidget.isVisible()
        self.advWidget.setVisible(not is_visible)
        if not is_visible:
            self.toggleAdvBtn.setText("▼ 收起高级设置 (Token / 网络代理)")
        else:
            self.toggleAdvBtn.setText("▶ 展开高级设置 (Token / 网络代理)")

    def toggleTokenVisibility(self):
        """切换 Token 输入框密码显隐"""
        if self.tokenEdit.echoMode() == LineEdit.Password:
            self.tokenEdit.setEchoMode(LineEdit.Normal)
            self.toggleTokenBtn.setText("🔒 隐藏")
        else:
            self.tokenEdit.setEchoMode(LineEdit.Password)
            self.toggleTokenBtn.setText("👁️ 显示")

    def resetConfig(self):
        """重置高级配置为默认"""
        self.tokenEdit.clear()
        self.cfProxyEdit.clear()
        self.proxyEdit.clear()
        self.log("✅ 已恢复默认网络与 Token 配置")
        QMessageBox.information(self, "恢复默认", "高级网络与 Token 配置已恢复为内置默认值！", QMessageBox.Ok)

    def selectSavePath(self):
        directory = QFileDialog.getExistingDirectory(self, "💬 请选择贴纸保存路径", self.savePathEdit.text())
        if directory:
            self.savePathEdit.setText(directory)
            self.save_path = directory
            self.log(f"✅ 已将保存路径设置为: {directory}")

    def _get_configured_downloader(self) -> TGStickerDownloader:
        """根据当前 UI 的高级配置项动态创建 TGStickerDownloader 实例"""
        token = self.tokenEdit.text().strip() or None
        cf_proxy = self.cfProxyEdit.text().strip() or None
        proxy_str = self.proxyEdit.text().strip() or None
        
        net_proxy = None
        if proxy_str:
            if proxy_str.startswith("http://127.0.0.1") or proxy_str.startswith("socks") or "127.0.0.1" in proxy_str:
                net_proxy = proxy_str
            elif not cf_proxy:
                cf_proxy = proxy_str

        return TGStickerDownloader(
            bot_token=token,
            cf_proxy=cf_proxy,
            network_proxy=net_proxy,
        )

    def testNetworkConnection(self):
        self.log("💬 正在测试 Telegram API 连通性与 Token 有效性...")
        downloader = self._get_configured_downloader()
        try:
            res = downloader.test_connection()
            if res["bot_ok"]:
                msg = f"✅ 连接成功!\n\n通道: {res.get('channel')}\nBot 名称: @{res.get('bot_username')}"
                self.log(f"✅ 连接成功! 通道: {res.get('channel')}, Bot: @{res.get('bot_username')}")
                QMessageBox.information(self, "连通性测试", msg, QMessageBox.Ok)
            else:
                msg = f"❌ 连接失败: {res.get('error', '未知错误')}"
                self.log(msg)
                QMessageBox.warning(self, "连通性测试", msg, QMessageBox.Ok)
        except Exception as e:
            self.log(f"❌ 测试出错: {e}")
            QMessageBox.critical(self, "测试出错", f"发生异常: {e}", QMessageBox.Ok)

    # ==========================================
    # 解析贴纸包逻辑与懒加载体系
    # ==========================================

    def startParsePack(self):
        link = self.urlInputEdit.text().strip()
        if not link:
            self.log("❌ 请先输入 Telegram 贴纸链接或包名！")
            QMessageBox.warning(self, "提示", "请先输入 Telegram 贴纸链接或包名！", QMessageBox.Ok)
            return

        self._cancelActiveThreads()

        self.parseButton.setEnabled(False)
        self.exportSelectedButton.setEnabled(False)
        self.exportAllButton.setEnabled(False)
        self.importSelectedButton.setEnabled(False)
        self.importAllButton.setEnabled(False)
        self.previewListWidget.clear()
        self._thumb_pixmaps.clear()
        self._detail_cache.clear()
        self.all_stickers.clear()
        self.loaded_sticker_count = 0
        self.is_batch_loading = False

        self.detailPreviewLabel.clear()
        self.detailInfoLabel.setText("未选中贴纸")

        self.log(f"💬 正在解析贴纸包 [{link}] ...")
        self.progressBar.setMaximum(0)

        self.downloader = self._get_configured_downloader()
        self._parse_thread = ParsePackThread(self.downloader, link, self)
        self._parse_thread.success.connect(self._onParseSuccess)
        self._parse_thread.failed.connect(self._onParseFailed)
        self._parse_thread.start()

    def _cancelActiveThreads(self):
        """安全取消后台进行中的缩略图与大图加载线程"""
        if self._batch_thread and self._batch_thread.isRunning():
            self._batch_thread.cancel()
            self._batch_thread.wait(200)
            self._batch_thread = None

        if self._detail_thread and self._detail_thread.isRunning():
            self._detail_thread.cancel()
            self._detail_thread.wait(200)
            self._detail_thread = None

        if self._import_thread and self._import_thread.isRunning():
            self._import_thread.cancel()
            self._import_thread.wait(200)
            self._import_thread = None

        if self.detail_movie:
            try:
                self.detail_movie.stop()
            except Exception:
                pass
            self.detail_movie = None

    def _onParseSuccess(self, pack: StickerPackInfo):
        self.parseButton.setEnabled(True)
        self.progressBar.setMaximum(100)
        self.progressBar.setValue(100)
        self.current_pack = pack
        self.all_stickers = pack.stickers

        self.previewTitleLabel.setText(f"贴纸预览区 ({pack.title} - 共 {pack.total_count} 张)")
        self.log(f"✅ 成功解析贴纸包: 《{pack.title}》({pack.name})，共 {pack.total_count} 张贴纸。")
        self.log(f"ℹ️ 贴纸类型: {pack.type_desc}")

        if pack.is_animated or pack.is_video:
            self.formatComboBox.setCurrentIndex(1)
        else:
            self.formatComboBox.setCurrentIndex(0)

        self.exportSelectedButton.setEnabled(True)
        self.exportAllButton.setEnabled(True)
        self.importSelectedButton.setEnabled(True)
        self.importAllButton.setEnabled(True)

        self.loadMoreThumbnails()

    def _onParseFailed(self, error_msg: str):
        self.parseButton.setEnabled(True)
        self.progressBar.setMaximum(100)
        self.progressBar.setValue(0)
        self.previewTitleLabel.setText("贴纸预览区 (解析失败)")
        self.log(f"❌ 解析贴纸包失败: {error_msg}")
        QMessageBox.critical(self, "解析失败", f"无法获取贴纸包信息:\n{error_msg}\n\n建议检查网络代理、Token 或贴纸链接是否正确。", QMessageBox.Ok)

    def onScrollBarMoved(self, value):
        """监听滚动条位置，滑动到底部 85% 时触发懒加载下一批"""
        if not self.all_stickers or self.is_batch_loading:
            return
        scroll_bar = self.previewListWidget.verticalScrollBar()
        max_val = scroll_bar.maximum()
        if max_val > 0 and value > max_val * 0.85:
            if self.loaded_sticker_count < len(self.all_stickers):
                self.loadMoreThumbnails()

    def loadMoreThumbnails(self):
        """分批异步加载下一批缩略图"""
        if self.is_batch_loading or not self.all_stickers:
            return

        start_idx = self.loaded_sticker_count
        end_idx = min(start_idx + self.batch_size, len(self.all_stickers))

        if start_idx >= end_idx:
            return

        self.is_batch_loading = True
        batch_stickers = self.all_stickers[start_idx:end_idx]

        for s in batch_stickers:
            placeholder_pixmap = QPixmap(100, 100)
            placeholder_pixmap.fill(QColor(0, 0, 0, 15))
            
            painter = QPainter(placeholder_pixmap)
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("Arial", 8))
            painter.drawText(placeholder_pixmap.rect(), Qt.AlignCenter, f"#{s.index + 1}\n加载中...")
            painter.end()

            item = QListWidgetItem()
            item.setIcon(QIcon(placeholder_pixmap))
            item.setData(Qt.UserRole, s.index)
            item.setToolTip(f"序号: #{s.index + 1}\n表情: {s.emoji or '无'}\n尺寸: {s.width}x{s.height}")
            self.previewListWidget.addItem(item)

        self.loaded_sticker_count = end_idx
        self.log(f"💬 正在加载缩略图 [{start_idx + 1} - {end_idx}] / 共 {len(self.all_stickers)} 张...")

        self._batch_thread = BatchThumbnailThread(self.downloader, batch_stickers, self)
        self._batch_thread.item_loaded.connect(self._onThumbnailLoaded)
        self._batch_thread.batch_done.connect(self._onBatchDone)
        self._batch_thread.start()

    def _onThumbnailLoaded(self, index: int, png_bytes: bytes, badge_text: str):
        """
        统一 1:1 正方形居中渲染，确保右下角格式角标稳定清晰呈现
        """
        img_pixmap = QPixmap()
        if img_pixmap.loadFromData(png_bytes):
            scaled_img = img_pixmap.scaled(
                92, 92,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            canvas = QPixmap(100, 100)
            canvas.fill(Qt.transparent)

            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            x = (100 - scaled_img.width()) // 2
            y = (100 - scaled_img.height()) // 2
            painter.drawPixmap(x, y, scaled_img)

            badge_rect = QRect(46, 80, 52, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 160))
            painter.drawRoundedRect(badge_rect, 3, 3)

            painter.setPen(QColor(255, 255, 255))
            painter.setFont(QFont("Arial", 8, QFont.Bold))
            painter.drawText(badge_rect, Qt.AlignCenter, badge_text)
            painter.end()

            self._thumb_pixmaps[index] = canvas

            if index < self.previewListWidget.count():
                item = self.previewListWidget.item(index)
                if item:
                    item.setIcon(QIcon(canvas))

    def _onBatchDone(self):
        self.is_batch_loading = False
        self.log(f"✅ 已完成渲染预览：{self.loaded_sticker_count}/{len(self.all_stickers)}")

    # ==========================================
    # 中间预览区选择控制
    # ==========================================

    def selectAllLoaded(self):
        for i in range(self.previewListWidget.count()):
            self.previewListWidget.item(i).setSelected(True)
        self.log(f"✅ 已全选当前已加载的 {self.previewListWidget.count()} 个贴纸")

    def clearSelection(self):
        self.previewListWidget.clearSelection()
        self.log("✅ 已清空当前选择")

    def invertSelection(self):
        for i in range(self.previewListWidget.count()):
            item = self.previewListWidget.item(i)
            item.setSelected(not item.isSelected())
        self.log("✅ 已反转当前选择")

    # ==========================================
    # 右侧详细预览面板逻辑
    # ==========================================

    def onItemSelectionChanged(self):
        current_item = self.previewListWidget.currentItem()

        if self.detail_movie:
            try:
                self.detail_movie.stop()
            except Exception:
                pass
            self.detail_movie = None

        if self._detail_thread and self._detail_thread.isRunning():
            self._detail_thread.cancel()
            self._detail_thread = None

        if not current_item or not current_item.isSelected() or not self.current_pack:
            self.detailPreviewLabel.clear()
            self.detailInfoLabel.setText("未选中贴纸")
            return

        sticker_idx = current_item.data(Qt.UserRole)
        if sticker_idx is None or sticker_idx >= len(self.all_stickers):
            return

        sticker = self.all_stickers[sticker_idx]

        format_display = "静态贴纸 (WebP)"
        if sticker.is_animated:
            format_display = "矢量动画 (TGS / Lottie)"
        elif sticker.is_video:
            format_display = "视频动图 (WebM / VP9)"

        info_text = (
            f"<b>贴纸序号:</b> #{sticker.index + 1}<br/>"
            f"<b>代表表情:</b> {sticker.emoji or '无'}<br/>"
            f"<b>所属贴纸包:</b> {self.current_pack.title}<br/>"
            f"<b>贴纸类型:</b> {format_display}<br/>"
            f"<b>原始尺寸:</b> {sticker.width} x {sticker.height}<br/>"
            f"<b>文件大小:</b> {((sticker.file_size or 0) / 1024):.2f} KB<br/><br/>"
            f"<b>Telegram File ID:</b><br/>"
            f"<span style='font-size:10px; color:#666;'>{sticker.file_id[:26]}...</span>"
        )
        self.detailInfoLabel.setText(info_text)

        if sticker_idx in self._detail_cache:
            file_path, is_gif = self._detail_cache[sticker_idx]
            if os.path.exists(file_path):
                self._displayDetailPreview(file_path, is_gif)
                return

        if sticker_idx in self._thumb_pixmaps:
            thumb = self._thumb_pixmaps[sticker_idx]
            scaled = thumb.scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.detailPreviewLabel.setPixmap(scaled)
        else:
            self.detailPreviewLabel.setText("正在加载预览...")

        self._detail_thread = DetailPreviewThread(self.downloader, sticker, self)
        self._detail_thread.preview_ready.connect(self._onDetailPreviewReady)
        self._detail_thread.preview_failed.connect(self._onDetailPreviewFailed)
        self._detail_thread.start()

    def _onDetailPreviewReady(self, sticker_index: int, file_path: str, is_gif: bool):
        self._detail_cache[sticker_index] = (file_path, is_gif)
        current_item = self.previewListWidget.currentItem()
        if current_item and current_item.data(Qt.UserRole) == sticker_index:
            self._displayDetailPreview(file_path, is_gif)

    def _onDetailPreviewFailed(self, sticker_index: int, error_msg: str):
        current_item = self.previewListWidget.currentItem()
        if current_item and current_item.data(Qt.UserRole) == sticker_index:
            self.detailPreviewLabel.setText(f"预览加载失败:\n{error_msg}")

    def _displayDetailPreview(self, file_path: str, is_gif: bool):
        try:
            if is_gif:
                self.detail_movie = QMovie(file_path)
                reader = QImageReader(file_path)
                orig_size = reader.size()
                if orig_size.isValid():
                    scaled_size = orig_size.scaled(240, 240, Qt.KeepAspectRatio)
                    self.detail_movie.setScaledSize(scaled_size)
                else:
                    self.detail_movie.setScaledSize(QSize(240, 240))
                self.detailPreviewLabel.setMovie(self.detail_movie)
                self.detail_movie.start()
            else:
                pixmap = QPixmap()
                if pixmap.load(file_path):
                    scaled = pixmap.scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.detailPreviewLabel.setPixmap(scaled)
                else:
                    self.detailPreviewLabel.setText("图片解析失败")
        except Exception as e:
            self.detailPreviewLabel.setText(f"展示失败: {e}")

    # ==========================================
    # 导出到文件夹逻辑
    # ==========================================

    def exportSelected(self):
        """导出用户选中的贴纸到本地文件夹"""
        if not self.current_pack:
            return

        selected_items = self.previewListWidget.selectedItems()
        if not selected_items:
            self.log("❌ 您尚未选择任何贴纸！请先在预览区选中贴纸后再导出。")
            QMessageBox.warning(self, "提示", "请先在预览区选中贴纸后再导出！", QMessageBox.Ok)
            return

        selected_indices = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole) is not None]

        reply = QMessageBox.question(
            self,
            "确认导出选中",
            f"确定导出当前选中的 {len(selected_indices)} 个贴纸到文件夹？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导出操作")
            return

        self._executeDownload(selected_indices)

    def exportAll(self):
        """导出贴纸包全部贴纸到本地文件夹"""
        if not self.current_pack:
            return

        reply = QMessageBox.question(
            self,
            "确认导出全部",
            f"当前不管预览是否完全加载，将直接从服务器批量导出《{self.current_pack.title}》的全部 {self.current_pack.total_count} 个贴纸？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导出操作")
            return

        self._executeDownload(None)

    def _executeDownload(self, selected_indices: Optional[List[int]]):
        out_root = self.savePathEdit.text().strip()
        if not out_root:
            out_root = os.path.abspath(os.path.join(".", "downloads"))
            self.savePathEdit.setText(out_root)

        pack_folder_name = f"{self.current_pack.name}_{self.current_pack.title}"
        for ch in '<>:"/\\|?*':
            pack_folder_name = pack_folder_name.replace(ch, '_')
        output_dir = os.path.join(out_root, pack_folder_name)

        fmt = self.formatComboBox.currentData()
        to_zip = self.zipCheckBox.isChecked()
        workers = 8

        count_desc = len(selected_indices) if selected_indices is not None else self.current_pack.total_count
        self.log(f"🚀 开始批量导出: 目标目录 -> {output_dir} (共 {count_desc} 个贴纸, 格式: {str(fmt).upper()})")

        self.parseButton.setEnabled(False)
        self.exportSelectedButton.setEnabled(False)
        self.exportAllButton.setEnabled(False)
        self.importSelectedButton.setEnabled(False)
        self.importAllButton.setEnabled(False)
        self.progressBar.setValue(0)

        downloader = self._get_configured_downloader()
        self._download_thread = DownloadPackThread(
            downloader=downloader,
            link_or_name=self.current_pack.name,
            output_dir=output_dir,
            fmt=str(fmt),
            to_zip=to_zip,
            selected_indices=selected_indices,
            max_workers=workers,
            parent=self,
        )
        self._download_thread.progress.connect(self._onDownloadProgress)
        self._download_thread.finished_all.connect(self._onDownloadFinished)
        self._download_thread.failed.connect(self._onDownloadFailed)
        self._download_thread.start()

    def _onDownloadProgress(self, done: int, total: int, msg: str):
        if total > 0:
            val = int(done / total * 100)
            self.progressBar.setValue(val)
        self.log(f"导出进度 [{done}/{total}]: {msg}")

    def _onDownloadFinished(self, result: Dict[str, Any]):
        self.parseButton.setEnabled(True)
        self.exportSelectedButton.setEnabled(True)
        self.exportAllButton.setEnabled(True)
        self.importSelectedButton.setEnabled(True)
        self.importAllButton.setEnabled(True)
        self.progressBar.setValue(100)
        self.last_download_result = result

        msg = f"🎉 全部提取成功! 成功导出 {result['success_count']} 张, 失败 {result['fail_count']} 张。"
        self.log(f"✅ {msg}")
        self.log(f"📁 导出文件夹: {result['output_dir']}")
        if result.get("zip_path"):
            self.log(f"📦 ZIP压缩包: {result['zip_path']}")

        try:
            out_dir = result['output_dir']
            if sys.platform == "win32":
                subprocess.Popen(['explorer', os.path.abspath(out_dir)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", out_dir])
            else:
                subprocess.Popen(["xdg-open", out_dir])
        except Exception:
            pass

        QMessageBox.information(
            self, "导出完成",
            f"{msg}\n\n保存目录:\n{result['output_dir']}" +
            (f"\n\nZIP 压缩包:\n{result['zip_path']}" if result.get("zip_path") else ""),
            QMessageBox.Ok
        )

    def _onDownloadFailed(self, error_msg: str):
        self.parseButton.setEnabled(True)
        self.exportSelectedButton.setEnabled(True)
        self.exportAllButton.setEnabled(True)
        self.importSelectedButton.setEnabled(True)
        self.importAllButton.setEnabled(True)
        self.log(f"❌ 导出过程中出现错误: {error_msg}")
        QMessageBox.critical(self, "导出出错", f"导出失败:\n{error_msg}", QMessageBox.Ok)

    # ==========================================
    # 导入到 SuzuEmojy 资源库逻辑
    # ==========================================

    def importSelected(self):
        """将选中的贴纸下载并导入到资源库"""
        if not self.current_pack:
            return

        selected_items = self.previewListWidget.selectedItems()
        if not selected_items:
            self.log("❌ 您尚未选择任何贴纸！请先在预览区选中贴纸后再导入。")
            QMessageBox.warning(self, "提示", "请先在预览区选中贴纸后再导入！", QMessageBox.Ok)
            return

        selected_indices = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole) is not None]

        reply = QMessageBox.question(
            self,
            "确认导入选中",
            f"确定将当前选中的 {len(selected_indices)} 个贴纸导入到资源库？（自动转码为通用图片并去重）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导入操作")
            return

        self._executeImport(selected_indices)

    def importAll(self):
        """将全部贴纸下载并导入到资源库"""
        if not self.current_pack:
            return

        reply = QMessageBox.question(
            self,
            "确认导入全部",
            f"确定将《{self.current_pack.title}》的全部 {self.current_pack.total_count} 个贴纸导入到资源库？（自动转码并去重）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导入操作")
            return

        self._executeImport(None)

    def _executeImport(self, selected_indices: Optional[List[int]]):
        main_win = self.window()
        if not hasattr(main_win, 'storage') or not main_win.storage:
            self.log("❌ 导入失败，无法获取表情包资源库存储服务！")
            QMessageBox.warning(self, "错误", "无法获取表情包资源库存储服务！", QMessageBox.Ok)
            return

        storage = main_win.storage

        # 确定分类名称：TG_{pack_name}
        safe_pack_name = self.current_pack.name
        for ch in '<>:"/\\|?*':
            safe_pack_name = safe_pack_name.replace(ch, '_')
        category_name = f"TG_{safe_pack_name}"

        target_stickers = self.all_stickers
        if selected_indices is not None:
            target_set = set(selected_indices)
            target_stickers = [s for s in self.all_stickers if s.index in target_set]

        total_count = len(target_stickers)
        if total_count == 0:
            return

        self.parseButton.setEnabled(False)
        self.exportSelectedButton.setEnabled(False)
        self.exportAllButton.setEnabled(False)
        self.importSelectedButton.setEnabled(False)
        self.importAllButton.setEnabled(False)

        self.progressBar.setMaximum(total_count)
        self.progressBar.setValue(0)
        self.log(f"💬 开始异步并发导入贴纸到资源库，分类: [{category_name}]...")

        downloader = self._get_configured_downloader()

        self._import_thread = ImportPackThread(
            downloader=downloader,
            storage_service=storage,
            target_stickers=target_stickers,
            category_name=category_name,
            max_workers=4,
            parent=self
        )
        self._import_thread.progress.connect(self._onImportProgress)
        self._import_thread.finished_all.connect(self._onImportFinished)
        self._import_thread.failed.connect(self._onImportFailed)
        self._import_thread.start()

    def _onImportProgress(self, done: int, total: int, msg: str):
        if total > 0:
            val = int(done / total * 100)
            self.progressBar.setValue(done)
        self.log(msg)

    def _onImportFinished(self, imported: int, dup: int, failed: int, cat_name: str):
        self.parseButton.setEnabled(True)
        self.exportSelectedButton.setEnabled(True)
        self.exportAllButton.setEnabled(True)
        self.importSelectedButton.setEnabled(True)
        self.importAllButton.setEnabled(True)
        self.progressBar.setValue(self.progressBar.maximum())

        self.log(f"✅ 导入完成！成功入库 {imported} 张贴纸到 [{cat_name}]，重复合并 {dup} 张，失败 {failed} 张。")
        QMessageBox.information(
            self,
            "导入完成",
            f"Telegram 贴纸导入成功！\n分类: {cat_name}\n共成功入库: {imported} 个 (重复合并: {dup})\n失败: {failed} 个",
            QMessageBox.Ok
        )

    def _onImportFailed(self, error_msg: str):
        self.parseButton.setEnabled(True)
        self.exportSelectedButton.setEnabled(True)
        self.exportAllButton.setEnabled(True)
        self.importSelectedButton.setEnabled(True)
        self.importAllButton.setEnabled(True)
        self.log(f"❌ 导入过程中出现错误: {error_msg}")
        QMessageBox.critical(self, "导入出错", f"导入失败:\n{error_msg}", QMessageBox.Ok)
