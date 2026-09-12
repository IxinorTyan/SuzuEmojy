from PySide6.QtCore import (
    Qt, QSize, Signal, QCoreApplication, QRect, QStandardPaths,
    QEasingCurve, QPropertyAnimation, QTimer, QEventLoop, QThread
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QFileDialog, QMessageBox, QLabel, QListWidget, QSizePolicy
)
from PySide6.QtGui import (
    QIcon, QFont, QPixmap, QPainter, QColor, QMovie, QImageReader, QTextCursor
)
from qfluentwidgets import (
    LineEdit, PushButton, PrimaryPushButton, ComboBox,
    TextEdit, FluentIcon as FIF, TransparentToolButton,
    TitleLabel, BodyLabel, SubtitleLabel, CardWidget, StrongBodyLabel,
    MessageBoxBase, InfoBar
)

import os
import shutil
import subprocess
from pathlib import Path

from services.qq_extractor import QQExtractor
from services.i18n import t, i18n_engine
from fluent_ui.views.setting_view import disable_wheel_scroll_adjustment
from fluent_ui.components.rotating_chevron_button import RotatingChevronButton
from fluent_ui.components.state_tool_tip_manager import StateToolTipManager


def _tf(text, **kwargs):
    """翻译并格式化动态文本。"""
    return t(text).format(**kwargs)


class LogDialog(MessageBoxBase):
    """操作日志查看弹窗"""
    def __init__(self, log_history, parent=None):
        if parent is None:
            from PySide6.QtWidgets import QApplication
            parent = QApplication.activeWindow() or QWidget()
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(t("操作日志"), self)

        self.logTextEdit = TextEdit(self)
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setMinimumSize(520, 340)
        self.logTextEdit.setStyleSheet("""
            TextEdit {
                font-family: 'Segoe UI', 'Microsoft YaHei', Consolas;
                font-size: 12px;
                border-radius: 6px;
            }
        """)
        self.logTextEdit.setPlainText("\n".join(log_history))
        self.logTextEdit.moveCursor(QTextCursor.End)

        btn_layout = QHBoxLayout()
        self.clearBtn = PushButton(t("清空日志"), self)
        self.copyBtn = PushButton(t("复制日志"), self)
        btn_layout.addWidget(self.clearBtn)
        btn_layout.addWidget(self.copyBtn)
        btn_layout.addStretch()

        self.clearBtn.clicked.connect(self._on_clear)
        self.copyBtn.clicked.connect(self._on_copy)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addLayout(btn_layout)
        self.viewLayout.addWidget(self.logTextEdit)

        self.yesButton.setText(t("关闭"))
        self.hideCancelButton()
        self.widget.setMinimumWidth(560)
        self.parent_view = parent

    def _on_clear(self):
        self.logTextEdit.clear()
        if self.parent_view and hasattr(self.parent_view, 'log_history'):
            self.parent_view.log_history.clear()

    def _on_copy(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.logTextEdit.toPlainText())
class QQImportWorker(QThread):
    """后台异步入库线程：执行 APNG 转码、Marketface 提取、StorageService 保存与分类"""
    progress = Signal(int, int, str, bool)  # done, total, filename, is_duplicated
    log_msg = Signal(str)
    finished_all = Signal(int, int, int, str, bool)  # imported, duplicated, failed, category_name, was_cancelled
    failed = Signal(str)

    def __init__(self, storage, file_paths, category_name, is_marketface=False, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.file_paths = file_paths
        self.category_name = category_name
        self.is_marketface = is_marketface
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total_files = len(self.file_paths)
        imported_count = 0
        dup_count = 0
        fail_count = 0

        try:
            self.storage.add_category(self.category_name)
        except Exception as e:
            self.failed.emit(str(e))
            return

        for idx, src_file in enumerate(self.file_paths):
            if self._is_cancelled:
                break

            if not src_file or not os.path.exists(src_file):
                fail_count += 1
                continue

            target_file_to_save = src_file
            try:
                if self.is_marketface:
                    gif_path = QQExtractor.get_marketface_gif_path(src_file)
                    if gif_path:
                        target_file_to_save = gif_path
                elif QQExtractor.is_apng_file(src_file):
                    temp_gif = QQExtractor.convert_apng_to_gif(src_file)
                    if temp_gif:
                        target_file_to_save = temp_gif

                dest_path, is_duplicated = self.storage.save_file(target_file_to_save)
                if dest_path:
                    self.storage.add_image_to_category(dest_path, self.category_name)
                    if is_duplicated:
                        dup_count += 1
                    imported_count += 1
                else:
                    fail_count += 1

                filename = os.path.basename(src_file)
                self.progress.emit(idx + 1, total_files, filename, is_duplicated)
            except Exception as exc:
                fail_count += 1
                self.log_msg.emit(f"⚠️ 处理 {os.path.basename(src_file)} 异常: {exc}")

        self.finished_all.emit(imported_count, dup_count, fail_count, self.category_name, self._is_cancelled)


class QQExportWorker(QThread):
    """后台异步导出线程：复制/转码表情到外部指定目录"""
    progress = Signal(int, int, str, str)  # done, total, src_name, dest_name
    log_msg = Signal(str)
    finished_all = Signal(int, str, bool)  # copied_count, dst_dir, was_cancelled
    failed = Signal(str)

    def __init__(self, file_paths, dst_dir, is_marketface=False, parent=None):
        super().__init__(parent)
        self.file_paths = file_paths
        self.dst_dir = dst_dir
        self.is_marketface = is_marketface
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    @staticmethod
    def _unique_dest_path(dst_dir, stem, ext):
        ext = ext.lstrip('.') if ext else ''
        dest_file = os.path.join(dst_dir, f"{stem}.{ext}" if ext else stem)
        suffix_number = 1
        while os.path.exists(dest_file):
            dest_file = os.path.join(dst_dir, f"{stem}_{suffix_number}.{ext}" if ext else f"{stem}_{suffix_number}")
            suffix_number += 1
        return dest_file

    def run(self):
        try:
            if not os.path.exists(self.dst_dir):
                os.makedirs(self.dst_dir, exist_ok=True)
        except Exception as e:
            self.failed.emit(str(e))
            return

        total_files = len(self.file_paths)
        copied_count = 0

        for idx, src_file in enumerate(self.file_paths):
            if self._is_cancelled:
                break

            if not src_file or not os.path.exists(src_file):
                continue

            try:
                actual_ext = QQExtractor.get_actual_extension(src_file)
                marketface_gif_path = None
                if self.is_marketface:
                    marketface_gif_path = QQExtractor.get_marketface_gif_path(src_file)
                filename_no_ext = os.path.splitext(os.path.basename(src_file))[0]

                if marketface_gif_path:
                    dest_file = self._unique_dest_path(self.dst_dir, filename_no_ext, "gif")
                    shutil.copy2(marketface_gif_path, dest_file)
                    copied_count += 1
                elif actual_ext and actual_ext.lower() == 'png' and QQExtractor.is_apng_file(src_file):
                    dest_file = self._unique_dest_path(self.dst_dir, filename_no_ext, "gif")
                    converted_path = QQExtractor.convert_apng_to_gif(src_file, dest_file)
                    if converted_path:
                        copied_count += 1
                    else:
                        dest_file = self._unique_dest_path(self.dst_dir, filename_no_ext, "png")
                        shutil.copy2(src_file, dest_file)
                        copied_count += 1
                else:
                    filename = os.path.basename(src_file)
                    stem, orig_ext = os.path.splitext(filename)
                    if actual_ext:
                        filename_lower = filename.lower()
                        has_original_extension = (
                            filename_lower.endswith(f".{actual_ext}") or
                            filename_lower.endswith(f".{actual_ext}.gif")
                        )
                        if not has_original_extension:
                            dest_file = self._unique_dest_path(self.dst_dir, filename, actual_ext)
                        else:
                            dest_file = self._unique_dest_path(self.dst_dir, stem, orig_ext.lstrip('.'))
                    else:
                        dest_file = self._unique_dest_path(self.dst_dir, stem, orig_ext.lstrip('.'))

                    shutil.copy2(src_file, dest_file)
                    copied_count += 1

                self.progress.emit(
                    copied_count, total_files,
                    os.path.basename(src_file), os.path.basename(dest_file)
                )
            except Exception as exc:
                self.log_msg.emit(f"⚠️ 导出 {os.path.basename(src_file)} 异常: {exc}")

        self.finished_all.emit(copied_count, self.dst_dir, self._is_cancelled)


class QQScanInterface(QWidget):
    """QQNT表情包批量提取工具界面 (View - 现代化上下解构)"""
    back_requested = Signal()
    TOP_BAR_HEIGHT = 40

    def __init__(self, parent=None, config_service=None):
        super().__init__(parent=parent)
        self.setObjectName("QQScanInterface")

        self.config = config_service or getattr(parent, 'config', None)
        self.default_ini_path = r'C:\Users\Public\Documents\Tencent\QQ\UserDataInfo.ini'

        # 保存路径初始化：优先使用上次保存的值，否则设为当前用户的图片文件夹
        saved_save_path = self.config.get("qq_save_path", "") if self.config else ""
        if saved_save_path and os.path.exists(saved_save_path):
            self.savePath = saved_save_path
        else:
            pic_dir = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation) or os.path.join(os.path.expanduser('~'), 'Pictures')
            self.savePath = pic_dir
            if self.config:
                self.config.set("qq_save_path", pic_dir)

        # QQ 数据路径缓存：优先读取上次记录的路径
        saved_data_path = self.config.get("qq_data_path", "") if self.config else ""
        if saved_data_path and os.path.exists(saved_data_path):
            self.userdata_save_path_cache = saved_data_path
        else:
            self.userdata_save_path_cache = None

        # 懒加载相关的状态变量
        self.emoji_file_paths = []     # 存放当前分类下所有待加载表情包文件的完整路径
        self.loaded_emoji_count = 0    # 已渲染到列表中的表情包数量
        self.batch_size = 100          # 每次懒加载的表情包数量
        self.is_loading = False        # 是否正在加载，防止重复触发
        self.active_movies = {}        # 存放当前正在播放动图的项目 {item: (label, movie)}
        self.detail_movie = None
        self.log_history = []          # 内存中保存的操作日志
        self._config_anim = None
        self._populating_categories = False
        self._current_worker = None

        self._init_ui()
        i18n_engine.language_changed.connect(self.update_texts)

    def _init_ui(self):
        # 主布局：垂直布局，顶栏 + 配置/摘要 + 常驻操作条 + 主内容区
        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(32, 10, 32, 12)
        self.mainLayout.setSpacing(10)

        # 1. 顶部返回与日志工具栏
        self.topBar = QWidget(self)
        self.topBar.setFixedHeight(self.TOP_BAR_HEIGHT)
        self.topBar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(0, 0, 0, 0)
        self.topBarLayout.setSpacing(10)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip(t("返回主面板"))
        self.btnBack.clicked.connect(self._on_back_clicked)

        self.titleLabel = TitleLabel(t("扫描QQ文件"), self.topBar)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()

        self.btnLog = TransparentToolButton(FIF.DOCUMENT, self.topBar)
        self.btnLog.setFixedSize(32, 32)
        self.btnLog.setToolTip(t("操作日志"))
        self.btnLog.clicked.connect(self.show_log_dialog)
        self.topBarLayout.addWidget(self.btnLog)

        self.mainLayout.addWidget(self.topBar)

        # 2. 配置卡片折叠后的摘要行 (折叠时显示)
        self.summaryRow = QWidget(self)
        summary_layout = QHBoxLayout(self.summaryRow)
        summary_layout.setContentsMargins(12, 4, 12, 4)
        summary_layout.setSpacing(8)

        self.summaryLabel = BodyLabel("", self.summaryRow)
        self.summaryLabel.setStyleSheet("font-size: 13px; font-weight: bold;")
        summary_layout.addWidget(self.summaryLabel)
        summary_layout.addStretch()

        self.expandConfigButton = PushButton(t("展开配置"), self.summaryRow)
        self.expandConfigButton.setIcon(FIF.CHEVRON_DOWN_MED)
        self.expandConfigButton.clicked.connect(self.expand_config)
        summary_layout.addWidget(self.expandConfigButton)

        self.summaryRow.setVisible(False)
        self.mainLayout.addWidget(self.summaryRow)

        # 3. QQ 数据配置卡片 (可折叠)
        self.configCard = CardWidget(self)
        config_layout = QVBoxLayout(self.configCard)
        config_layout.setContentsMargins(20, 14, 20, 14)
        config_layout.setSpacing(10)

        # 头部：标题与折叠按钮
        config_header = QHBoxLayout()
        self.configTitle = StrongBodyLabel(t("QQ 数据配置"), self.configCard)
        config_header.addWidget(self.configTitle)
        config_header.addStretch()

        self.collapseConfigButton = RotatingChevronButton(self.configCard)
        self.collapseConfigButton.setToolTip(t("收起配置面板"))
        self.collapseConfigButton.set_direction(180, animated=False)
        self.collapseConfigButton.clicked.connect(self.toggle_config)
        config_header.addWidget(self.collapseConfigButton)
        config_layout.addLayout(config_header)

        # 表单布局：一行一项，舒展大方
        form_layout = QVBoxLayout()
        form_layout.setSpacing(10)

        # 行 1: 数据路径
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.readPathLabel = BodyLabel(t("数据路径:"), self.configCard)
        self.readPathLabel.setFixedWidth(70)
        self.readPathEdit = LineEdit(self.configCard)
        self.readPathEdit.setReadOnly(True)
        self.readPathEdit.setPlaceholderText(t("自动定位中，或手动选择..."))
        self.selectReadDirButton = PushButton(t("定位目录"), self.configCard)
        self.selectReadDirButton.setFixedWidth(90)
        self.selectReadDirButton.clicked.connect(self.selectReadPath)
        row1.addWidget(self.readPathLabel)
        row1.addWidget(self.readPathEdit, 1)
        row1.addWidget(self.selectReadDirButton)
        form_layout.addLayout(row1)

        # 行 2: 保存路径
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.savePathLabel = BodyLabel(t("保存路径:"), self.configCard)
        self.savePathLabel.setFixedWidth(70)
        self.savePathEdit = LineEdit(self.configCard)
        self.savePathEdit.setPlaceholderText(t("请选择表情包保存路径..."))
        if self.savePath:
            self.savePathEdit.setText(self.savePath)
        self.selectDirButton = PushButton(t("浏览..."), self.configCard)
        self.selectDirButton.setFixedWidth(90)
        self.selectDirButton.clicked.connect(self.selectSavePath)
        row2.addWidget(self.savePathLabel)
        row2.addWidget(self.savePathEdit, 1)
        row2.addWidget(self.selectDirButton)
        form_layout.addLayout(row2)

        # 行 3: 选择账号
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self.userLabel = BodyLabel(t("选择账号:"), self.configCard)
        self.userLabel.setFixedWidth(70)
        self.userComboBox = ComboBox(self.configCard)
        self.userComboBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.helpButton = TransparentToolButton(FIF.HELP, self.configCard)
        self.helpButton.setFixedSize(32, 32)
        self.helpButton.setToolTip(t("使用帮助"))
        self.helpButton.clicked.connect(self.showHelp)
        row3.addWidget(self.userLabel)
        row3.addWidget(self.userComboBox, 1)
        row3.addWidget(self.helpButton)
        form_layout.addLayout(row3)

        # 行 4: 选择分类
        row4 = QHBoxLayout()
        row4.setSpacing(8)
        self.emojiFolderLabel = BodyLabel(t("选择分类:"), self.configCard)
        self.emojiFolderLabel.setFixedWidth(70)
        self.emojiFolderComboBox = ComboBox(self.configCard)
        self.emojiFolderComboBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row4.addWidget(self.emojiFolderLabel)
        row4.addWidget(self.emojiFolderComboBox, 1)
        form_layout.addLayout(row4)

        # 行 5: 扫描表情包预览 (主按钮)
        self.scanButton = PrimaryPushButton(FIF.SEARCH, t("扫描表情包预览"), self.configCard)
        self.scanButton.setFixedHeight(36)
        self.scanButton.clicked.connect(self.scanEmojis)
        form_layout.addWidget(self.scanButton)

        # 行 6: [导出选中] [导出全部] (两按钮合并并排在一行)
        export_btn_layout = QHBoxLayout()
        export_btn_layout.setSpacing(10)
        self.exportSelectedButton = PushButton(FIF.DOWNLOAD, t("导出选中"), self.configCard)
        self.exportSelectedButton.setFixedHeight(34)
        self.exportSelectedButton.clicked.connect(self.exportSelected)
        self.exportAllButton = PushButton(FIF.FOLDER, t("导出全部"), self.configCard)
        self.exportAllButton.setFixedHeight(34)
        self.exportAllButton.clicked.connect(self.exportAll)
        export_btn_layout.addWidget(self.exportSelectedButton, 1)
        export_btn_layout.addWidget(self.exportAllButton, 1)
        form_layout.addLayout(export_btn_layout)

        config_layout.addLayout(form_layout)
        self.mainLayout.addWidget(self.configCard)

        # 4. 常驻操作工具条：[入库选中] [入库全部]   ...   [全选已加载] [清空选择]
        self.actionBar = QWidget(self)
        action_layout = QHBoxLayout(self.actionBar)
        action_layout.setContentsMargins(4, 2, 4, 2)
        action_layout.setSpacing(10)

        self.importSelectedButton = PrimaryPushButton(FIF.SAVE, t("入库选中"), self.actionBar)
        self.importSelectedButton.setFixedHeight(32)
        self.importSelectedButton.clicked.connect(self.importSelected)

        self.importAllButton = PushButton(FIF.APPLICATION, t("入库全部"), self.actionBar)
        self.importAllButton.setFixedHeight(32)
        self.importAllButton.clicked.connect(self.importAll)

        action_layout.addWidget(self.importSelectedButton)
        action_layout.addWidget(self.importAllButton)
        action_layout.addStretch()

        self.selectAllButton = PushButton(t("全选已加载"), self.actionBar)
        self.selectAllButton.setFixedHeight(32)
        self.selectAllButton.clicked.connect(self.selectAllLoaded)

        self.clearSelectionButton = PushButton(t("清空选择"), self.actionBar)
        self.clearSelectionButton.setFixedHeight(32)
        self.clearSelectionButton.clicked.connect(self.clearSelection)

        action_layout.addWidget(self.selectAllButton)
        action_layout.addWidget(self.clearSelectionButton)

        self.mainLayout.addWidget(self.actionBar)

        # 5. 下方主内容区 (左：预览网格，右：详细预览)
        self.contentLayout = QHBoxLayout()
        self.contentLayout.setSpacing(14)

        # 左侧预览网格（直接使用 QListWidget，避免多层包装导致鼠标事件冲突）
        self.previewListWidget = QListWidget(self)
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
        self.contentLayout.addWidget(self.previewListWidget, stretch=1)

        # 右侧详细预览卡片
        self.detailWidget = QWidget(self)
        self.detailWidget.setFixedWidth(280)
        self.detailWidget.setObjectName("detailWidget")
        self.detailWidget.setStyleSheet("""
            QWidget#detailWidget {
                background-color: rgba(255, 255, 255, 15);
                border: 1px solid rgba(0, 0, 0, 15);
                border-radius: 8px;
            }
        """)
        detail_layout = QVBoxLayout(self.detailWidget)
        detail_layout.setContentsMargins(14, 14, 14, 14)
        detail_layout.setSpacing(10)

        self.detailTitle = SubtitleLabel(t("表情详细预览"), self.detailWidget)
        detail_layout.addWidget(self.detailTitle)

        self.detailPreviewLabel = QLabel(self.detailWidget)
        self.detailPreviewLabel.setAlignment(Qt.AlignCenter)
        self.detailPreviewLabel.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.detailPreviewLabel.setFixedSize(250, 250)
        self.detailPreviewLabel.setStyleSheet(
            "background-color: rgba(0, 0, 0, 5); border: 1px solid rgba(0, 0, 0, 15); border-radius: 8px;"
        )
        detail_layout.addWidget(self.detailPreviewLabel, alignment=Qt.AlignCenter)

        self.detailInfoLabel = BodyLabel(t("未选中表情"), self.detailWidget)
        self.detailInfoLabel.setWordWrap(True)
        self.detailInfoLabel.setFixedWidth(250)
        self.detailInfoLabel.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        detail_layout.addWidget(self.detailInfoLabel)

        detail_layout.addStretch()

        self.thanksLabel = BodyLabel(self.detailWidget)
        self.thanksLabel.setText(t('致谢：基于 <a href="https://github.com/VanillaNahida" style="color: #0078d4; text-decoration: underline;">VanillaNahida</a> 的项目二次开发'))
        self.thanksLabel.setOpenExternalLinks(True)
        self.thanksLabel.setStyleSheet("color: #888888; font-size: 11px;")
        detail_layout.addWidget(self.thanksLabel)

        self.contentLayout.addWidget(self.detailWidget)
        self.mainLayout.addLayout(self.contentLayout, stretch=1)

        # 6. 右上角状态气泡管理器
        self.tooltip = StateToolTipManager(self)
        self.tooltip.closed.connect(self._on_tooltip_closed)

        # 信号绑定
        self.userComboBox.currentIndexChanged.connect(self.onUserChanged)
        self.emojiFolderComboBox.currentIndexChanged.connect(self.onCategoryChanged)
        self.previewListWidget.verticalScrollBar().valueChanged.connect(self.onScrollBarMoved)
        self.previewListWidget.itemSelectionChanged.connect(self.onItemSelectionChanged)

        # 初始化定位与加载
        self.log(t("💬 QQNT表情包批量提取工具启动成功"))
        self.log(t("💡建议在使用前提前打开要提取表情包的账户，随便选择一个聊天窗口，将表情全部加载出来，这样提取的表情包更齐全。"))
        self.populateUserComboBox()
        disable_wheel_scroll_adjustment(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.tooltip.reposition()

    def show_log_dialog(self):
        """弹出日志查看窗口"""
        dialog = LogDialog(self.log_history, self.window())
        dialog.exec()

    def _set_busy(self, busy: bool):
        """设置操作中的繁忙状态，禁用/启用操作按钮以防重入"""
        self.exportSelectedButton.setEnabled(not busy)
        self.exportAllButton.setEnabled(not busy)
        self.importSelectedButton.setEnabled(not busy)
        self.importAllButton.setEnabled(not busy)
        self.scanButton.setEnabled(not busy)
        self.selectReadDirButton.setEnabled(not busy)
        self.selectDirButton.setEnabled(not busy)
        self.userComboBox.setEnabled(not busy)
        self.emojiFolderComboBox.setEnabled(not busy)

    def _on_back_clicked(self):
        """点击返回按钮：若有后台任务运行则弹窗确认中断"""
        if self._current_worker and self._current_worker.isRunning():
            reply = QMessageBox.question(
                self,
                t("确认中断并返回"),
                t("当前正在处理表情文件，确定要中断当前任务并返回吗？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.log(t("💬 用户确认中断任务并返回"))
                self._current_worker.cancel()
                self._current_worker.wait(2000)
                self.tooltip.cancel()
                self._set_busy(False)
                self.back_requested.emit()
        else:
            self.back_requested.emit()

    def _on_tooltip_closed(self):
        """当用户点击右上角气泡关闭按钮时触发中断"""
        if self._current_worker and self._current_worker.isRunning():
            self.log(t("💬 用户点击了提示气泡的关闭按钮，正在中断当前任务..."))
            self._current_worker.cancel()

    def shutdown(self):
        """窗口或页面退出时，停止后台任务并等待"""
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.cancel()
            self._current_worker.wait(2000)
        self.tooltip.cancel()

    def toggle_config(self):
        """手动展开/收起配置面板"""
        if self.configCard.isVisible():
            self.collapse_config()
        else:
            self.expand_config()

    def collapse_config(self, animated=True):
        """收起配置面板"""
        if not self.configCard.isVisible() and self.summaryRow.isVisible():
            return
        self._update_summary_label()
        self.summaryRow.setVisible(True)
        self.collapseConfigButton.set_direction(0, animated=animated)
        self.collapseConfigButton.setToolTip(t("展开配置面板"))

        if not animated:
            self.configCard.setVisible(False)
            return

        target_h = self.configCard.height()
        self._animate_config(
            height_from=target_h,
            height_to=0,
            on_finish=lambda: self.configCard.setVisible(False)
        )

    def expand_config(self, animated=True):
        """展开配置面板"""
        if self.configCard.isVisible() and not self.summaryRow.isVisible():
            return
        self.configCard.setVisible(True)
        self.summaryRow.setVisible(False)
        self.collapseConfigButton.set_direction(180, animated=animated)
        self.collapseConfigButton.setToolTip(t("收起配置面板"))

        if not animated:
            self.configCard.setMaximumHeight(16777215)
            return

        target_h = self.configCard.sizeHint().height()
        self._animate_config(
            height_from=0,
            height_to=target_h,
            on_finish=lambda: self.configCard.setMaximumHeight(16777215)
        )

    def _animate_config(self, height_from, height_to, on_finish=None):
        if self._config_anim is not None:
            self._config_anim.stop()
        self._config_anim = QPropertyAnimation(self.configCard, b'maximumHeight', self)
        self._config_anim.setDuration(260)
        self._config_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._config_anim.setStartValue(height_from)
        self._config_anim.setEndValue(height_to)

        def _finish():
            if on_finish:
                on_finish()
            self._config_anim = None

        self._config_anim.finished.connect(_finish)
        self._config_anim.start()

    def _update_summary_label(self):
        user_text = self.userComboBox.currentText() or t("未选择账号")
        cat_text = self.emojiFolderComboBox.currentText() or t("未选择分类")
        self.summaryLabel.setText(_tf("📌 当前配置：{user}  /  {category}", user=user_text, category=cat_text))

    def onCategoryChanged(self):
        """分类选择改变：仅更新当前配置摘要，绝不自动扫描"""
        if self._populating_categories:
            return
        folder = self.getSelectedFolder()
        if not folder:
            return
        self._update_summary_label()

    def _category_display_name(self, folder_name):
        mapping = {
            "personal_emoji": t("个人表情 (personal_emoji)"),
            "emoji-recv": t("接收到表情[谨慎加载,内含巨量表情] (emoji-recv)"),
            "marketface": t("商店表情 (marketface)"),
            "BaseEmojiSyastems": t("系统表情[已支持APNG动图转GIF导出] (BaseEmojiSyastems)"),
            "emoji-related": t("候选表情[打字时系统推荐] (emoji-related)"),
            "pic": t("收藏的图片 [注意：包含聊天接收的图片，和收藏图片混杂在一起，暂无法避免] (Pic)")
        }
        return mapping.get(folder_name, _tf("{folder} (其他分类)", folder=folder_name))

    def update_texts(self, lang):
        """刷新 QQ 扫描页面的多语言文本。"""
        self.btnBack.setToolTip(t("返回主面板"))
        self.titleLabel.setText(t("扫描QQ文件"))
        self.btnLog.setToolTip(t("操作日志"))
        self.configTitle.setText(t("QQ 数据配置"))
        self.expandConfigButton.setText(t("展开配置"))
        self.collapseConfigButton.setToolTip(t("收起配置面板") if self.configCard.isVisible() else t("展开配置面板"))
        self.readPathEdit.setPlaceholderText(t("自动定位中，或手动选择..."))
        self.selectReadDirButton.setText(t("定位目录"))
        self.readPathLabel.setText(t("数据路径:"))
        self.savePathEdit.setPlaceholderText(t("请选择表情包保存路径..."))
        self.selectDirButton.setText(t("浏览..."))
        self.savePathLabel.setText(t("保存路径:"))
        self.helpButton.setToolTip(t("使用帮助"))
        self.userLabel.setText(t("选择账号:"))
        self.emojiFolderLabel.setText(t("选择分类:"))
        self.scanButton.setText(t("扫描表情包预览"))
        self.exportSelectedButton.setText(t("导出选中"))
        self.exportAllButton.setText(t("导出全部"))
        self.importSelectedButton.setText(t("入库选中"))
        self.importAllButton.setText(t("入库全部"))
        self.selectAllButton.setText(t("全选已加载"))
        self.clearSelectionButton.setText(t("清空选择"))
        self.detailTitle.setText(t("表情详细预览"))
        self.detailInfoLabel.setText(t("未选中表情"))
        self._update_summary_label()
        self.thanksLabel.setText(t(
            '致谢：基于 <a href="https://github.com/VanillaNahida" '
            'style="color: #0078d4; text-decoration: underline;">VanillaNahida</a> 的项目二次开发'
        ))

        current_folder = self.getSelectedFolder()
        if current_folder:
            index = self.emojiFolderComboBox.findData(current_folder)
            if index >= 0:
                self.emojiFolderComboBox.setItemText(
                    index, self._category_display_name(current_folder)
                )

    def selectSavePath(self):
        directory = QFileDialog.getExistingDirectory(self, t("💬 请选择表情包保存路径"))
        if directory:
            self.savePathEdit.setText(directory)
            self.savePath = directory
            if self.config:
                self.config.set("qq_save_path", directory)
            self.log(_tf("✅ 已将保存路径设置为: {path}", path=directory))

    def selectReadPath(self):
        directory = QFileDialog.getExistingDirectory(
            self, 
            t("选择QQ聊天记录所在目录（即包含QQ号数字文件夹的 Tencent Files 目录）")
        )
        if directory:
            self.log(_tf("✅ 已选择数据目录: {path}", path=directory))
            self.userdata_save_path_cache = directory
            if self.config:
                self.config.set("qq_data_path", directory)
            self.populateUserComboBox()
        else:
            self.log(t("💬 取消选择数据目录"))

    def get_selected_qq(self):
        data = self.userComboBox.currentData()
        if data:
            return str(data)
        txt = self.userComboBox.currentText()
        if '（' in txt and '）' in txt:
            return txt.split('（')[-1].split('）')[0].strip()
        elif '(' in txt and ')' in txt:
            return txt.split('(')[-1].split(')')[0].strip()
        return txt.strip()

    def getSelectedFolder(self):
        data = self.emojiFolderComboBox.currentData()
        if data:
            return str(data)
        selected_folder_text = self.emojiFolderComboBox.currentText()
        if not selected_folder_text:
            return None
        for key in ("personal_emoji", "emoji-recv", "marketface",
                    "BaseEmojiSyastems", "emoji-related", "pic"):
            if selected_folder_text == self._category_display_name(key):
                return key
        suffix = t(" (其他分类)")
        if selected_folder_text.endswith(suffix):
            return selected_folder_text[:-len(suffix)].strip()
        return selected_folder_text

    def populateUserComboBox(self):
        cached_path = self.userdata_save_path_cache
        if not cached_path and self.config:
            saved = self.config.get("qq_data_path", None)
            if saved and os.path.exists(saved):
                cached_path = saved

        userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, cached_path)

        if userdata_save_path and os.path.exists(userdata_save_path):
            self.readPathEdit.setText(userdata_save_path)
            self.userdata_save_path_cache = userdata_save_path
            if self.config:
                self.config.set("qq_data_path", userdata_save_path)

            numeric_subdirs = QQExtractor.get_numeric_subdirectories(userdata_save_path)
            self.userComboBox.clear()
            if numeric_subdirs:
                for subdir in numeric_subdirs:
                    nickname = QQExtractor.get_user_nickname(subdir)
                    if nickname:
                        display_name = f"{nickname}（{subdir}）"
                        self.userComboBox.addItem(display_name, userData=subdir)
                    else:
                        self.userComboBox.addItem(subdir, userData=subdir)
                self.log(_tf("✅ 成功加载了 {count} 个QQ用户文件夹", count=len(numeric_subdirs)))
            else:
                self.log(_tf(
                    "⚠️ 在目录 [{path}] 下未找到任何QQ号数据文件夹（纯数字命名且含有nt_qq）",
                    path=userdata_save_path
                ))
        else:
            self.readPathEdit.setText("")
            self.userComboBox.clear()
            self.log(t("⚠️ 未能自动定位到QQ聊天数据文件夹，请手动点击按钮 [选择数据目录] 指定！"))

        self.onUserChanged()

    def sanitize_filename(self, name):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '')
        return name.strip()

    def _is_marketface(self):
        folder = self.getSelectedFolder()
        return bool(folder and folder.lower() == "marketface")

    def _get_marketface_data(self, file_path):
        """读取 marketface 恢复后的 GIF 数据及临时可读路径。"""
        if not self._is_marketface():
            return None, None
        data = QQExtractor.read_marketface_data(file_path)
        if data is None:
            return None, None
        return data, QQExtractor.get_marketface_gif_path(file_path)

    def onUserChanged(self):
        selected_qq = self.get_selected_qq()
        self._populating_categories = True
        try:
            self.emojiFolderComboBox.clear()
            if not selected_qq:
                return

            userdata_save_path = self.userdata_save_path_cache or QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
            if not userdata_save_path:
                return

            emoji_root = QQExtractor.get_emoji_root(userdata_save_path, selected_qq)

            if emoji_root.exists() and emoji_root.is_dir():
                try:
                    subdirs = [d for d in os.listdir(emoji_root) if os.path.isdir(emoji_root / d)]
                    for subdir in sorted(subdirs):
                        display_name = self._category_display_name(subdir)
                        self.emojiFolderComboBox.addItem(display_name, userData=subdir)
                except Exception as e:
                    self.log(_tf("⚠️ 读取表情分类出错: {error}", error=e))
            else:
                self.log(_tf("⚠️ 未找到该账户的 Emoji 目录: {path}", path=emoji_root))

            # 收藏图片分类：Pic 目录存在时提供「收藏图片」扫描入口（扫描 Pic/日期/Ori 下原图）
            pic_root = QQExtractor.get_pic_root(userdata_save_path, selected_qq)
            if pic_root.exists() and pic_root.is_dir():
                self.emojiFolderComboBox.addItem(self._category_display_name('pic'), userData='pic')
        finally:
            self._populating_categories = False

        self._update_summary_label()

    def onScrollBarMoved(self, value):
        scroll_bar = self.previewListWidget.verticalScrollBar()
        max_val = scroll_bar.maximum()
        if max_val > 0 and value > max_val * 0.9:
            if self.loaded_emoji_count < len(self.emoji_file_paths) and not self.is_loading:
                self.loadMoreEmojis()

    def loadMoreEmojis(self):
        if self.is_loading:
            return
        self.is_loading = True

        start_idx = self.loaded_emoji_count
        end_idx = min(start_idx + self.batch_size, len(self.emoji_file_paths))

        if start_idx >= end_idx:
            self.is_loading = False
            return

        self.log(_tf(
            "💬 正在加载预览图 {start} - {end} ...",
            start=start_idx + 1, end=end_idx
        ))

        batch_paths = self.emoji_file_paths[start_idx:end_idx]

        for idx, file_path_str in enumerate(batch_paths):
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            marketface_data = None
            marketface_gif_path = None
            marketface_frames = 0
            if self._is_marketface():
                marketface_info = QQExtractor.get_marketface_info(file_path_str)
                if marketface_info is not None:
                    marketface_data, marketface_frames = marketface_info
                    marketface_gif_path = QQExtractor.get_marketface_gif_path(file_path_str)
                    actual_ext = "gif"

            if actual_ext:
                try:
                    if marketface_data is not None:
                        file_data = marketface_data
                    else:
                        with open(file_path_str, 'rb') as f:
                            file_data = f.read()

                    pixmap = QPixmap()
                    if pixmap.loadFromData(file_data):
                        is_animated = False
                        badge_text = "GIF"

                        if marketface_data is not None:
                            is_animated = marketface_frames > 1
                            badge_text = "GIF" if is_animated else ""
                        elif actual_ext.lower() == 'png' and QQExtractor.is_apng_file(file_path_str):
                            is_animated = True
                            badge_text = "APNG"
                        else:
                            try:
                                reader = QImageReader(file_path_str)
                                if reader.supportsAnimation():
                                    is_animated = reader.imageCount() > 1
                            except Exception:
                                pass

                        canvas = QPixmap(100, 100)
                        canvas.fill(Qt.transparent)

                        scaled_pixmap = pixmap.scaled(
                            100, 100, 
                            Qt.KeepAspectRatio, 
                            Qt.SmoothTransformation
                        )

                        painter = QPainter(canvas)
                        x = (100 - scaled_pixmap.width()) // 2
                        y = (100 - scaled_pixmap.height()) // 2
                        painter.drawPixmap(x, y, scaled_pixmap)

                        if is_animated:
                            rect = QRect(55, 84, 45, 16)
                            painter.fillRect(rect, QColor(0, 0, 0, 160))
                            painter.setPen(QColor(255, 255, 255))
                            font = QFont("Arial", 8, QFont.Bold)
                            painter.setFont(font)
                            painter.drawText(rect, Qt.AlignCenter, badge_text)

                        painter.end()

                        icon = QIcon(canvas)
                        from PySide6.QtWidgets import QListWidgetItem
                        item = QListWidgetItem(icon, "")
                        item.setData(Qt.UserRole, file_path_str)
                        item.setData(Qt.UserRole + 1, is_animated)
                        item.setData(Qt.UserRole + 2, icon)
                        display_ext = "GIF" if marketface_data is not None else (
                            "APNG" if badge_text == "APNG" else actual_ext.upper()
                        )
                        item.setToolTip(_tf(
                            "格式: {format}\n路径: {path}",
                            format=display_ext,
                            path=os.path.basename(file_path_str)
                        ))
                        self.previewListWidget.addItem(item)
                except Exception:
                    pass

            if idx % 10 == 0 or idx == len(batch_paths) - 1:
                QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        self.loaded_emoji_count = end_idx
        self.log(_tf(
            "✅ 已加载表情预览：{loaded}/{total}",
            loaded=self.loaded_emoji_count, total=len(self.emoji_file_paths)
        ))
        self.is_loading = False

    def onItemSelectionChanged(self):
        current_item = self.previewListWidget.currentItem()

        if hasattr(self, 'detail_movie') and self.detail_movie:
            try:
                self.detail_movie.stop()
            except Exception:
                pass
            self.detail_movie = None

        self.detailPreviewLabel.clear()

        if not current_item or not current_item.isSelected():
            self.detailInfoLabel.setText(t("未选中表情"))
            return

        file_path_str = current_item.data(Qt.UserRole)
        is_animated = current_item.data(Qt.UserRole + 1)

        if not file_path_str or not os.path.exists(file_path_str):
            self.detailInfoLabel.setText(t("文件不存在"))
            return

        marketface_data, marketface_gif_path = self._get_marketface_data(file_path_str)
        try:
            file_size_kb = os.path.getsize(file_path_str) / 1024
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            file_name = os.path.basename(file_path_str)

            format_display = actual_ext.upper() if actual_ext else t("未知")
            if marketface_data is not None:
                format_display = "GIF"
            elif actual_ext and actual_ext.lower() == 'png' and QQExtractor.is_apng_file(file_path_str):
                format_display = t("APNG (动态图片)")

            info_text = _tf(
                "<b>文件名:</b><br/>{name}<br/><br/>"
                "<b>格式:</b> {format}<br/>"
                "<b>大小:</b> {size:.2f} KB<br/><br/>"
                "<b>保存路径:</b><br/>{path}",
                name=file_name, format=format_display, size=file_size_kb,
                path=file_path_str
            )
            self.detailInfoLabel.setText(info_text)
        except Exception as e:
            self.detailInfoLabel.setText(_tf("获取信息失败: {error}", error=e))

        try:
            play_path = file_path_str
            if marketface_gif_path:
                play_path = marketface_gif_path
            elif is_animated:
                if QQExtractor.is_apng_file(file_path_str):
                    converted_gif = QQExtractor.convert_apng_to_gif(file_path_str)
                    if converted_gif:
                        play_path = converted_gif

            if marketface_gif_path or is_animated:
                self.detail_movie = QMovie(play_path)
                reader = QImageReader(play_path)
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
                if pixmap.load(file_path_str):
                    scaled_pixmap = pixmap.scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.detailPreviewLabel.setPixmap(scaled_pixmap)
                else:
                    self.detailPreviewLabel.setText(t("图片加载失败"))
        except Exception as e:
            self.detailPreviewLabel.setText(_tf("预览失败: {error}", error=e))

    def scanEmojis(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        userdata_save_path = self.userdata_save_path_cache or QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
        if not userdata_save_path:
            self.log(t("❌ 未找到QQ数据路径"))
            return

        target_path = QQExtractor.get_category_path(userdata_save_path, selected_qq, selected_folder)

        if not target_path or not target_path.exists():
            self.log(_tf("❌ 未找到该用户的表情分类目录: {path}", path=target_path))
            QMessageBox.warning(
                self, t("警告"),
                t("未找到该分类的本地目录，可能是该账号在本地未生成对应分类，或者路径不正确。")
            )
            return

        if hasattr(self, 'detail_movie') and self.detail_movie:
            try:
                self.detail_movie.stop()
            except Exception:
                pass
            self.detail_movie = None
        self.detailPreviewLabel.clear()
        self.detailInfoLabel.setText(t("未选中表情"))

        self.previewListWidget.clear()
        self.emoji_file_paths = []
        self.loaded_emoji_count = 0
        self.log(_tf("💬 开始智能扫描分类 [{folder}] 表情包路径...", folder=selected_folder))

        self.tooltip.show(t("正在扫描表情包..."), _tf("正在扫描分类 [{folder}]...", folder=selected_folder))
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        self.emoji_file_paths = QQExtractor.scan_emojis(target_path, selected_folder)
        total_valid = len(self.emoji_file_paths)

        if total_valid == 0:
            self.log(t("❌ 未筛选出任何有效的表情包图片"))
            self.tooltip.finish(t("扫描完成"), t("未筛选出任何有效的表情包图片"))
            return

        self.log(_tf("✅ 扫描并筛选完毕，共发现 {count} 个有效表情图片。", count=total_valid))
        self.tooltip.finish(t("扫描完成"), _tf("共发现 {count} 个有效表情图片", count=total_valid))

        # 扫描成功后自动折叠收起配置卡片，将最大视野留给预览网格
        self.collapse_config()

        self.loadMoreEmojis()

    @staticmethod
    def _unique_dest_path(dst_dir, stem, ext):
        """生成不重复的目标文件名（如同名则追加 _1、_2...）"""
        ext = ext.lstrip('.') if ext else ''
        dest_file = os.path.join(dst_dir, f"{stem}.{ext}" if ext else stem)
        suffix_number = 1
        while os.path.exists(dest_file):
            dest_file = os.path.join(dst_dir, f"{stem}_{suffix_number}.{ext}" if ext else f"{stem}_{suffix_number}")
            suffix_number += 1
        return dest_file

    def copy_files_with_progress(self, file_paths, dst_dir):
        """导出文件（通过后台异步线程执行，主线程保持响应）"""
        if self._current_worker and self._current_worker.isRunning():
            QMessageBox.warning(self, t("提示"), t("当前有正在进行的操作，请稍候或先中止当前任务！"))
            return

        total_files = len(file_paths)
        self._set_busy(True)
        self.tooltip.show(
            t("正在导出表情..."),
            _tf("准备导出 {total} 个表情...", total=total_files)
        )

        worker = QQExportWorker(
            file_paths=file_paths,
            dst_dir=dst_dir,
            is_marketface=self._is_marketface(),
            parent=self
        )
        self._current_worker = worker

        worker.progress.connect(self._on_export_progress)
        worker.log_msg.connect(self.log)
        worker.finished_all.connect(self._on_export_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.start()

    def _on_export_progress(self, done, total, src_name, dest_name):
        self.log(_tf(
            "导出 [{done}/{total}]: {source} -> {destination}",
            done=done, total=total,
            source=src_name,
            destination=dest_name
        ))
        percent = int(done * 100 / max(total, 1))
        self.tooltip.update(_tf("正在处理: {done}/{total} ({percent}%)",
                                done=done, total=total, percent=percent))

    def _on_export_finished(self, copied_count, dst_dir, was_cancelled):
        self._set_busy(False)
        self._current_worker = None

        if was_cancelled:
            self.log(_tf("⚠️ 导出已被用户中断！已导出 {count} 个表情文件。", count=copied_count))
            self.tooltip.finish(
                t("导出已中断"),
                _tf("已由用户手动中断，已导出 {count} 个表情文件", count=copied_count)
            )
            return

        self.log(_tf("✅ 成功导出 {count} 个表情文件！", count=copied_count))
        self.tooltip.finish(
            t("导出完成"),
            _tf("已成功导出 {count} 个表情文件", count=copied_count)
        )
        self.log(t("✅ 完成！正在打开输出文件夹……"))
        try:
            subprocess.Popen(['explorer', os.path.abspath(dst_dir)])
            QMessageBox.information(self, t("完成"), t("表情提取成功！"))
        except Exception as e:
            self.log(_tf("❌ 无法打开资源管理器: {error}", error=e))

    def selectAllLoaded(self):
        for i in range(self.previewListWidget.count()):
            item = self.previewListWidget.item(i)
            item.setSelected(True)
        self.log(_tf(
            "✅ 已全选当前加载的 {count} 个表情",
            count=self.previewListWidget.count()
        ))

    def clearSelection(self):
        self.previewListWidget.clearSelection()
        self.log(t("✅ 已清空当前的选择"))

    def exportSelected(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if not self.savePath:
            message = t("❌ 你还没有选择保存路径呢，请先选择保存路径！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_items = self.previewListWidget.selectedItems()
        if len(selected_items) == 0:
            message = t("❌ 您尚未选择任何表情！请先在预览区选中表情后再导出。")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("请先在预览区选中表情后再导出！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导出选中"),
            _tf("确定导出当前选中的 {count} 个表情？", count=len(selected_items)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导出操作"))
            return

        display_name = QQExtractor.get_display_name(selected_qq)
        safe_name = self.sanitize_filename(display_name)
        output_dir = f"{self.savePath}/{safe_name}_{selected_folder}_提取的选中表情"
        self.log(_tf("✅ 正在复制选中的表情文件到: {path}", path=output_dir))
        selected_paths = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        self.copy_files_with_progress(selected_paths, output_dir)

    def exportAll(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if not self.savePath:
            message = t("❌ 你还没有选择保存路径呢，请先选择保存路径！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if len(self.emoji_file_paths) == 0:
            userdata_save_path = self.userdata_save_path_cache or QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
            if userdata_save_path:
                target_path = QQExtractor.get_category_path(userdata_save_path, selected_qq, selected_folder)
                self.emoji_file_paths = QQExtractor.scan_emojis(target_path, selected_folder)

        if len(self.emoji_file_paths) == 0:
            message = t("❌ 该表情分类下未发现任何有效的图片文件，无法导出！")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("该分类下未发现任何有效的表情图片文件！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导出全部"),
            _tf(
                "当前不管界面是否完全加载，将直接导出扫描到的该分类下所有 {count} 个表情？",
                count=len(self.emoji_file_paths)
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导出操作"))
            return

        display_name = QQExtractor.get_display_name(selected_qq)
        safe_name = self.sanitize_filename(display_name)
        output_dir = f"{self.savePath}/{safe_name}_{selected_folder}_提取的全部表情"
        self.log(_tf("✅ 正在复制所有表情文件到: {path}", path=output_dir))
        self.copy_files_with_progress(self.emoji_file_paths, output_dir)

    def import_files_with_progress(self, file_paths):
        """导入表情到资源库（通过后台异步线程执行，主线程保持响应）"""
        if self._current_worker and self._current_worker.isRunning():
            QMessageBox.warning(self, t("提示"), t("当前有正在进行的操作，请稍候或先中止当前任务！"))
            return

        main_win = self.window()
        if not hasattr(main_win, 'storage') or not main_win.storage:
            message = t("❌ 导入失败，无法获取表情包资源库存储服务！")
            self.log(message)
            self.tooltip.finish(t("导入失败"), t("无法获取表情包资源库存储服务！"))
            QMessageBox.warning(self, t("错误"), t("无法获取表情包资源库存储服务！"))
            return

        storage = main_win.storage
        selected_qq = self.get_selected_qq()
        selected_folder = self.getSelectedFolder()
        category_name = f"QQ_{selected_qq}_{selected_folder}"
        total_files = len(file_paths)

        self._set_busy(True)
        self.tooltip.show(
            t("正在入库表情..."),
            _tf("准备入库 {total} 个表情到分类 [{category}]...", total=total_files, category=category_name)
        )
        self.log(_tf(
            "💬 开始导入表情到资源库，分类: [{category}]...",
            category=category_name
        ))

        worker = QQImportWorker(
            storage=storage,
            file_paths=file_paths,
            category_name=category_name,
            is_marketface=self._is_marketface(),
            parent=self
        )
        self._current_worker = worker

        worker.progress.connect(self._on_import_progress)
        worker.log_msg.connect(self.log)
        worker.finished_all.connect(self._on_import_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.start()

    def _on_import_progress(self, done, total, filename, is_duplicated):
        dup_text = t("(重复已被合并)") if is_duplicated else ""
        selected_qq = self.get_selected_qq()
        selected_folder = self.getSelectedFolder()
        category_name = f"QQ_{selected_qq}_{selected_folder}"
        self.log(_tf(
            "导入 [{done}/{total}]: {filename} -> {category} {duplicate}",
            done=done, total=total, filename=filename,
            category=category_name, duplicate=dup_text
        ))
        percent = int(done * 100 / max(total, 1))
        self.tooltip.update(
            _tf("正在入库: {done}/{total} ({percent}%)",
                done=done, total=total, percent=percent)
        )

    def _on_import_finished(self, imported_count, dup_count, fail_count, category_name, was_cancelled):
        self._set_busy(False)
        self._current_worker = None

        main_win = self.window()
        refresh_library = getattr(main_win, "refresh_library", None)
        if callable(refresh_library):
            refresh_library()

        if was_cancelled:
            self.log(_tf("⚠️ 导入已被用户中断！已处理并入库 {imported} 个表情。", imported=imported_count))
            self.tooltip.finish(
                t("导入已中断"),
                _tf("已由用户手动中断，成功入库 {count} 个表情", count=imported_count)
            )
            return

        self.log(_tf(
            "✅ 导入完成！成功导入并分类 {imported} 个表情，其中 {duplicated} 个重复已被合并过滤，失败 {failed} 个。",
            imported=imported_count, duplicated=dup_count, failed=fail_count
        ))
        self.tooltip.finish(
            t("入库完成"),
            _tf("成功入库 {count} 个表情 (合并去重 {dup} 个)", count=imported_count, dup=dup_count)
        )
        QMessageBox.information(
            self, t("完成"),
            _tf(
                "表情导入成功！\n分类: {category}\n共导入并去重处理: {count} 个",
                category=category_name, count=imported_count
            )
        )

    def _on_worker_failed(self, error_msg):
        self._set_busy(False)
        self._current_worker = None
        self.log(_tf("❌ 操作出错: {error}", error=error_msg))
        self.tooltip.finish(t("操作出错"), str(error_msg))
        QMessageBox.critical(self, t("错误"), _tf("操作出错: {error}", error=error_msg))

    def importSelected(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_items = self.previewListWidget.selectedItems()
        if len(selected_items) == 0:
            message = t("❌ 您尚未选择任何表情！请先在预览区选中表情后再导入。")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("请先在预览区选中表情后再导入！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导入选中"),
            _tf(
                "确定将当前选中的 {count} 个表情导入到资源库？（会经过自动清洗和去重过滤）",
                count=len(selected_items)
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导入操作"))
            return

        selected_paths = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        self.import_files_with_progress(selected_paths)

    def importAll(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if len(self.emoji_file_paths) == 0:
            userdata_save_path = self.userdata_save_path_cache or QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
            if userdata_save_path:
                target_path = QQExtractor.get_category_path(userdata_save_path, selected_qq, selected_folder)
                self.emoji_file_paths = QQExtractor.scan_emojis(target_path, selected_folder)

        if len(self.emoji_file_paths) == 0:
            message = t("❌ 该表情分类下未发现任何有效的图片文件，无法导入！")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("该分类下未发现任何有效的表情图片文件！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导入全部"),
            _tf(
                "当前不管界面是否完全加载，将直接导入扫描到的该分类下所有 {count} 个表情到资源库？（自动清洗和去重）",
                count=len(self.emoji_file_paths)
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导入操作"))
            return

        self.import_files_with_progress(self.emoji_file_paths)

    def log(self, message):
        """记录日志至内存列表，并更新状态提示"""
        self.log_history.append(message)
        if len(self.log_history) > 2000:
            self.log_history = self.log_history[-1000:]

    def showHelp(self):
        help_text = t(
            "使用帮助：\n\n"
            "1. 使用时请确保已登录过QQ并加载过全部表情包\n"
            "2. 提取的时候会自动创建以QQ号开头的文件夹\n"
            "3. 选择一个账号后，点击'扫描表情包预览'获取表情包，然后选择表情，最后'导出选中'或'导出全部'。\n"
            "4. 导出的表情包比账号内实际的表情包要多属正常现象，因为QQ会缓存一些表情包\n\n"
            "  注意：如果没有找到任何用户，请确保QQ已经在本地登录过。并确保路径正确\n"
            "  可以尝试手动指定聊天数据文件夹的所在位置"
        )

        QMessageBox.information(
            self,
            t("使用帮助"),
            help_text,
            QMessageBox.Ok
        )
