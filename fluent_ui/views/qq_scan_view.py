from PySide6.QtCore import Qt, QSize, Signal, QCoreApplication, QRect
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter, QFrame,
    QFileDialog, QMessageBox, QLabel, QListWidget, QSizePolicy
)
from PySide6.QtGui import (
    QIcon, QFont, QPixmap, QPainter, QColor, QMovie, QImageReader
)
from qfluentwidgets import (
    LineEdit, PushButton, PrimaryPushButton, ComboBox, ProgressBar,
    TextEdit, FluentIcon as FIF, TransparentToolButton,
    TitleLabel, BodyLabel, SubtitleLabel, ScrollArea, CardWidget, StrongBodyLabel
)

import os
import shutil
import subprocess
from pathlib import Path

from services.qq_extractor import QQExtractor

class QQScanInterface(QWidget):
    """QQNT表情包批量提取工具界面 (View)"""
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("QQScanInterface")
        
        self.savePath = None
        self.default_ini_path = r'C:\Users\Public\Documents\Tencent\QQ\UserDataInfo.ini'
        self.userdata_save_path_cache = None
        
        # 懒加载相关的状态变量
        self.emoji_file_paths = []     # 存放当前分类下所有待加载表情包文件的完整路径
        self.loaded_emoji_count = 0    # 已渲染到列表中的表情包数量
        self.batch_size = 100          # 每次懒加载的表情包数量
        self.is_loading = False        # 是否正在加载，防止重复触发
        self.active_movies = {}        # 存放当前正在播放动图的项目 {item: (label, movie)}
        self.detail_movie = None

        self._init_ui()

    def _init_ui(self):
        # 主布局：垂直布局，顶栏 + 内容区
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

        self.titleLabel = TitleLabel("扫描QQ文件", self.topBar)

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

        card_title = StrongBodyLabel("QQ 数据配置", self.configCard)
        config_layout.addWidget(card_title)

        self.formLayout = QFormLayout()
        self.formLayout.setSpacing(8)
        self.formLayout.setLabelAlignment(Qt.AlignLeft)

        # 数据读取路径选择
        read_path_layout = QHBoxLayout()
        read_path_layout.setSpacing(6)
        self.readPathEdit = LineEdit(self.configCard)
        self.readPathEdit.setReadOnly(True)
        self.readPathEdit.setPlaceholderText("自动定位中，或手动选择...")
        self.selectReadDirButton = PushButton('定位目录', self.configCard)
        self.selectReadDirButton.setFixedWidth(80)
        self.selectReadDirButton.clicked.connect(self.selectReadPath)
        read_path_layout.addWidget(self.readPathEdit)
        read_path_layout.addWidget(self.selectReadDirButton)
        
        read_path_label = BodyLabel('数据路径:', self.configCard)
        self.formLayout.addRow(read_path_label, read_path_layout)

        # 保存路径选择
        save_path_layout = QHBoxLayout()
        save_path_layout.setSpacing(6)
        self.savePathEdit = LineEdit(self.configCard)
        self.savePathEdit.setPlaceholderText("请选择表情包保存路径...")
        self.selectDirButton = PushButton('浏览...', self.configCard)
        self.selectDirButton.setFixedWidth(80)
        self.selectDirButton.clicked.connect(self.selectSavePath)
        save_path_layout.addWidget(self.savePathEdit)
        save_path_layout.addWidget(self.selectDirButton)
        
        save_path_label = BodyLabel('保存路径:', self.configCard)
        self.formLayout.addRow(save_path_label, save_path_layout)

        # 选择用户下拉框
        user_layout = QHBoxLayout()
        user_layout.setSpacing(6)
        self.userComboBox = ComboBox(self.configCard)
        self.userComboBox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.helpButton = TransparentToolButton(FIF.HELP, self.configCard)
        self.helpButton.setFixedSize(28, 28)
        self.helpButton.setToolTip("使用帮助")
        self.helpButton.clicked.connect(self.showHelp)
        user_layout.addWidget(self.userComboBox, 1)
        user_layout.addWidget(self.helpButton)
        
        user_label = BodyLabel('选择账号:', self.configCard)
        self.formLayout.addRow(user_label, user_layout)

        # 选择分类下拉框
        self.emojiFolderComboBox = ComboBox(self.configCard)
        self.emojiFolderComboBox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        emoji_folder_label = BodyLabel('选择分类:', self.configCard)
        self.formLayout.addRow(emoji_folder_label, self.emojiFolderComboBox)

        config_layout.addLayout(self.formLayout)
        self.leftLayout.addWidget(self.configCard)

        # 2. 操作卡片
        self.actionsCard = CardWidget(self.leftWidget)
        actions_layout = QVBoxLayout(self.actionsCard)
        actions_layout.setContentsMargins(14, 12, 14, 12)
        actions_layout.setSpacing(10)

        actions_title = StrongBodyLabel("操作与提取", self.actionsCard)
        actions_layout.addWidget(actions_title)

        # 核心扫描按钮
        self.scanButton = PrimaryPushButton(FIF.SEARCH, '扫描表情包预览', self.actionsCard)
        self.scanButton.setFixedHeight(34)
        self.scanButton.clicked.connect(self.scanEmojis)
        actions_layout.addWidget(self.scanButton)

        # 导出操作 (双列并排)
        export_btn_layout = QHBoxLayout()
        export_btn_layout.setSpacing(8)
        self.exportSelectedButton = PushButton(FIF.DOWNLOAD, '导出选中', self.actionsCard)
        self.exportSelectedButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.exportSelectedButton.clicked.connect(self.exportSelected)
        self.exportAllButton = PushButton(FIF.FOLDER, '导出全部', self.actionsCard)
        self.exportAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.exportAllButton.clicked.connect(self.exportAll)
        export_btn_layout.addWidget(self.exportSelectedButton, 1)
        export_btn_layout.addWidget(self.exportAllButton, 1)
        actions_layout.addLayout(export_btn_layout)

        # 导入操作 (双列并排)
        import_btn_layout = QHBoxLayout()
        import_btn_layout.setSpacing(8)
        self.importSelectedButton = PushButton(FIF.SAVE, '入库选中', self.actionsCard)
        self.importSelectedButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.importSelectedButton.clicked.connect(self.importSelected)
        self.importAllButton = PushButton(FIF.APPLICATION, '入库全部', self.actionsCard)
        self.importAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        self.statusLabel = BodyLabel('', self.leftWidget)
        self.statusLabel.setStyleSheet("color: #666666; font-size: 11px;")
        self.leftLayout.addWidget(self.statusLabel)

        self.thanksLabel = BodyLabel(self.leftWidget)
        self.thanksLabel.setText('致谢：基于 <a href="https://github.com/VanillaNahida" style="color: #0078d4; text-decoration: underline;">VanillaNahida</a> 的项目二次开发')
        self.thanksLabel.setOpenExternalLinks(True)
        self.thanksLabel.setStyleSheet("color: #888888; font-size: 11px;")
        self.leftLayout.addWidget(self.thanksLabel)

        self.leftScrollArea.setWidget(self.leftWidget)

        # ====== 右侧表情包预览区域 ======
        self.rightWidget = QWidget(self.splitter)
        self.rightWidget.setMinimumWidth(320)
        self.rightLayout = QVBoxLayout(self.rightWidget)
        self.rightLayout.setContentsMargins(0, 0, 0, 0)
        self.rightLayout.setSpacing(10)

        preview_header_layout = QHBoxLayout()
        preview_label = SubtitleLabel('表情包预览区', self.rightWidget)
        preview_header_layout.addWidget(preview_label)
        preview_header_layout.addStretch()
        
        self.selectAllButton = PushButton('全选已加载', self.rightWidget)
        self.selectAllButton.clicked.connect(self.selectAllLoaded)
        preview_header_layout.addWidget(self.selectAllButton)
        
        self.clearSelectionButton = PushButton('清空选择', self.rightWidget)
        self.clearSelectionButton.clicked.connect(self.clearSelection)
        preview_header_layout.addWidget(self.clearSelectionButton)
        
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
        self.rightLayout.addWidget(self.previewListWidget)

        # ====== 最右侧单个表情大图预览区域 ======
        self.detailWidget = QWidget(self)
        self.detailLayout = QVBoxLayout(self.detailWidget)
        self.detailLayout.setContentsMargins(10, 0, 0, 0)
        self.detailLayout.setSpacing(10)
        self.detailWidget.setFixedWidth(280)

        detail_title = SubtitleLabel('表情详细预览', self.detailWidget)
        self.detailLayout.addWidget(detail_title)

        self.detailPreviewLabel = QLabel(self.detailWidget)
        self.detailPreviewLabel.setAlignment(Qt.AlignCenter)
        self.detailPreviewLabel.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.detailPreviewLabel.setFixedSize(250, 250)
        self.detailPreviewLabel.setStyleSheet("background-color: rgba(0, 0, 0, 5); border: 1px solid rgba(0, 0, 0, 15); border-radius: 5px;")
        self.detailLayout.addWidget(self.detailPreviewLabel, alignment=Qt.AlignCenter)

        self.detailInfoLabel = BodyLabel("未选中表情", self.detailWidget)
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

        # 合并到主水平内容布局
        content_layout = QHBoxLayout()
        content_layout.addWidget(self.splitter, stretch=1)
        content_layout.addWidget(self.detailWidget)
        
        self.mainLayout.addLayout(content_layout)

        # 槽函数绑定
        self.userComboBox.currentIndexChanged.connect(self.onUserChanged)
        self.previewListWidget.verticalScrollBar().valueChanged.connect(self.onScrollBarMoved)
        self.previewListWidget.itemSelectionChanged.connect(self.onItemSelectionChanged)

        # 初始化定位
        self.log("💬 QQNT表情包批量提取工具启动成功")
        self.log("💡建议在使用前提前打开要提取表情包的账户，随便选择一个聊天窗口，将表情全部加载出来，这样提取的表情包更齐全。")
        self.populateUserComboBox()

    def set_font(self, widget):
        pass

    def selectSavePath(self):
        directory = QFileDialog.getExistingDirectory(self, "💬 请选择表情包保存路径")
        if directory:
            self.savePathEdit.setText(directory)
            self.savePath = directory
            self.log(f"✅ 已将保存路径设置为: {directory}")

    def selectReadPath(self):
        directory = QFileDialog.getExistingDirectory(
            self, 
            "选择QQ聊天记录所在目录（即包含QQ号数字文件夹的 Tencent Files 目录）"
        )
        if directory:
            self.log(f"✅ 已选择数据目录: {directory}")
            self.userdata_save_path_cache = directory
            self.populateUserComboBox()
        else:
            self.log("💬 取消选择数据目录")

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
        name_mapping = {
            'personal_emoji': '个人表情 (personal_emoji)',
            'emoji-recv': '接收到表情[谨慎加载,内含巨量表情] (emoji-recv)',
            'marketface': '商店表情 (marketface)',
            'BaseEmojiSyastems': '系统表情[已支持APNG动图转GIF导出] (BaseEmojiSyastems)',
            'emoji-related': '候选表情[打字时系统推荐] (emoji-related)'
        }
        for key, val in name_mapping.items():
            if val == selected_folder_text:
                return key
        if " (其他分类)" in selected_folder_text:
            return selected_folder_text.replace(" (其他分类)", "").strip()
        return selected_folder_text

    def populateUserComboBox(self):
        userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)

        if userdata_save_path and os.path.exists(userdata_save_path):
            self.readPathEdit.setText(userdata_save_path)
            self.userdata_save_path_cache = userdata_save_path
            
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
                self.log(f"✅ 成功加载了 {len(numeric_subdirs)} 个QQ用户文件夹")
            else:
                self.log(f"⚠️ 在目录 [{userdata_save_path}] 下未找到任何QQ号数据文件夹（纯数字命名且含有nt_qq）")
        else:
            self.readPathEdit.setText("")
            self.userComboBox.clear()
            self.log("⚠️ 未能自动定位到QQ聊天数据文件夹，请手动点击按钮 [选择数据目录] 指定！")
        
        self.onUserChanged()

    def sanitize_filename(self, name):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '')
        return name.strip()

    def onUserChanged(self):
        selected_qq = self.get_selected_qq()
        self.emojiFolderComboBox.clear()
        if not selected_qq:
            return

        userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
        if not userdata_save_path:
            return

        file_path = Path(os.path.join(userdata_save_path, selected_qq))
        emoji_root = file_path / "nt_qq" / "nt_data" / "Emoji"

        if emoji_root.exists() and emoji_root.is_dir():
            try:
                subdirs = [d for d in os.listdir(emoji_root) if os.path.isdir(emoji_root / d)]
                name_mapping = {
                    'personal_emoji': '个人表情 (personal_emoji)',
                    'emoji-recv': '接收到表情[谨慎加载,内含巨量表情] (emoji-recv)',
                    'marketface': '商店表情 (marketface)',
                    'BaseEmojiSyastems': '系统表情[已支持APNG动图转GIF导出] (BaseEmojiSyastems)',
                    'emoji-related': '候选表情[打字时系统推荐] (emoji-related)'
                }
                for subdir in subdirs:
                    display_name = name_mapping.get(subdir, f"{subdir} (其他分类)")
                    self.emojiFolderComboBox.addItem(display_name, userData=subdir)
            except Exception as e:
                self.log(f"⚠️ 读取表情分类出错: {e}")
        else:
            self.log(f"⚠️ 未找到该账户的 Emoji 目录: {emoji_root}")

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

        self.log(f"💬 正在加载预览图 {start_idx + 1} - {end_idx} ...")
        self.progressBar.setMaximum(len(self.emoji_file_paths))
        
        batch_paths = self.emoji_file_paths[start_idx:end_idx]
        
        for idx, file_path_str in enumerate(batch_paths):
            current_count = start_idx + idx + 1
            self.progressBar.setValue(current_count)
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            if actual_ext:
                try:
                    with open(file_path_str, 'rb') as f:
                        file_data = f.read()
                    
                    pixmap = QPixmap()
                    if pixmap.loadFromData(file_data):
                        # 检测是否为动图（GIF 或 APNG）
                        is_animated = False
                        badge_text = "GIF"
                        
                        # 1. 检查是否为 APNG
                        if actual_ext.lower() == 'png' and QQExtractor.is_apng_file(file_path_str):
                            is_animated = True
                            badge_text = "APNG"
                        else:
                            # 2. 检查标准动画格式（如 GIF）
                            try:
                                reader = QImageReader(file_path_str)
                                if reader.supportsAnimation():
                                    is_animated = reader.imageCount() > 1
                            except Exception:
                                pass

                        # 创建 100x100 的透明背景画布，保证预览图大小绝对 1:1
                        canvas = QPixmap(100, 100)
                        canvas.fill(Qt.transparent)

                        scaled_pixmap = pixmap.scaled(
                            100, 100, 
                            Qt.KeepAspectRatio, 
                            Qt.SmoothTransformation
                        )

                        # 将缩放后的表情包居中绘制到画布上
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
                        display_ext = "APNG" if badge_text == "APNG" else actual_ext.upper()
                        item.setToolTip(f"格式: {display_ext}\n路径: {os.path.basename(file_path_str)}")
                        self.previewListWidget.addItem(item)
                except Exception:
                    pass
            
            if idx % 10 == 0 or idx == len(batch_paths) - 1:
                QCoreApplication.processEvents()
                
        self.loaded_emoji_count = end_idx
        self.log(f"✅ 已加载表情预览：{self.loaded_emoji_count}/{len(self.emoji_file_paths)}")
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
            self.detailInfoLabel.setText("未选中表情")
            return
            
        file_path_str = current_item.data(Qt.UserRole)
        is_animated = current_item.data(Qt.UserRole + 1)
        
        if not file_path_str or not os.path.exists(file_path_str):
            self.detailInfoLabel.setText("文件不存在")
            return
            
        try:
            file_size_kb = os.path.getsize(file_path_str) / 1024
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            file_name = os.path.basename(file_path_str)
            
            format_display = actual_ext.upper() if actual_ext else '未知'
            if actual_ext and actual_ext.lower() == 'png' and QQExtractor.is_apng_file(file_path_str):
                format_display = "APNG (动态图片)"

            info_text = f"<b>文件名:</b><br/>{file_name}<br/><br/>"
            info_text += f"<b>格式:</b> {format_display}<br/>"
            info_text += f"<b>大小:</b> {file_size_kb:.2f} KB<br/><br/>"
            info_text += f"<b>保存路径:</b><br/>{file_path_str}"
            self.detailInfoLabel.setText(info_text)
        except Exception as e:
            self.detailInfoLabel.setText(f"获取信息失败: {e}")
            
        try:
            play_path = file_path_str
            if is_animated:
                # 若为 APNG 格式，转换为临时 GIF 播放
                if QQExtractor.is_apng_file(file_path_str):
                    converted_gif = QQExtractor.convert_apng_to_gif(file_path_str)
                    if converted_gif:
                        play_path = converted_gif

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
                    self.detailPreviewLabel.setText("图片加载失败")
        except Exception as e:
            self.detailPreviewLabel.setText(f"预览失败: {e}")

    def scanEmojis(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            self.log("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            QMessageBox.information(self, '提示', '你还没有选择QQ号呢，请先选择一个QQ号！')
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            self.log("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            QMessageBox.information(self, '提示', '你还没有选择表情分类呢，请先选择一个分类！')
            return

        userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
        if not userdata_save_path:
            self.log("❌ 未找到QQ数据路径")
            return

        file_path = Path(os.path.join(userdata_save_path, selected_qq))
        emoji_path = file_path / "nt_qq" / "nt_data" / "Emoji" / selected_folder
        
        if not emoji_path.exists():
            self.log(f"❌ 未找到该用户的表情分类目录: {emoji_path}")
            QMessageBox.warning(self, '警告', '未找到该分类的本地目录，可能是该账号在本地未生成对应分类，或者路径不正确。')
            return

        if hasattr(self, 'detail_movie') and self.detail_movie:
            try:
                self.detail_movie.stop()
            except Exception:
                pass
            self.detail_movie = None
        self.detailPreviewLabel.clear()
        self.detailInfoLabel.setText("未选中表情")

        self.previewListWidget.clear()
        self.emoji_file_paths = []
        self.loaded_emoji_count = 0
        self.log(f"💬 开始智能扫描分类 [{selected_folder}] 表情包路径...")

        self.progressBar.setMaximum(100)
        self.progressBar.setValue(30)
        QCoreApplication.processEvents()

        self.emoji_file_paths = QQExtractor.scan_emojis(emoji_path, selected_folder)
        total_valid = len(self.emoji_file_paths)
        
        if total_valid == 0:
            self.log("❌ 未筛选出任何有效的表情包图片")
            self.progressBar.setValue(100)
            return

        self.log(f"✅ 扫描并筛选完毕，共发现 {total_valid} 个有效表情图片。")
        self.progressBar.setValue(100)
        self.loadMoreEmojis()

    def copy_files_with_progress(self, file_paths, dst_dir):
        try:
            if not os.path.exists(dst_dir):
                os.makedirs(dst_dir)

            total_files = len(file_paths)
            self.progressBar.setMaximum(total_files)
            self.progressBar.setValue(0)

            copied_count = 0
            for idx, src_file in enumerate(file_paths):
                if not src_file or not os.path.exists(src_file):
                    continue
                
                actual_ext = QQExtractor.get_actual_extension(src_file)
                filename_no_ext = os.path.splitext(os.path.basename(src_file))[0]
                
                # 如果检测到是 APNG 格式的表情，将其转码为通用动图 GIF 导出
                if actual_ext and actual_ext.lower() == 'png' and QQExtractor.is_apng_file(src_file):
                    dest_file = os.path.join(dst_dir, f"{filename_no_ext}.gif")
                    converted_path = QQExtractor.convert_apng_to_gif(src_file, dest_file)
                    if converted_path:
                        copied_count += 1
                        self.progressBar.setValue(copied_count)
                        self.log(f"导出(APNG转GIF) [{copied_count}/{total_files}]: {os.path.basename(src_file)} -> {os.path.basename(dest_file)}")
                    else:
                        dest_file = os.path.join(dst_dir, f"{filename_no_ext}.png")
                        shutil.copy2(src_file, dest_file)
                        copied_count += 1
                        self.progressBar.setValue(copied_count)
                        self.log(f"导出(回退PNG) [{copied_count}/{total_files}]: {os.path.basename(src_file)} -> {os.path.basename(dest_file)}")
                else:
                    filename = os.path.basename(src_file)
                    if actual_ext:
                        if not filename.lower().endswith(f".{actual_ext}"):
                            dest_file = os.path.join(dst_dir, f"{filename}.{actual_ext}")
                        else:
                            dest_file = os.path.join(dst_dir, filename)
                    else:
                        dest_file = os.path.join(dst_dir, filename)

                    shutil.copy2(src_file, dest_file)
                    copied_count += 1
                    self.progressBar.setValue(copied_count)
                    self.log(f"导出 [{copied_count}/{total_files}]: {os.path.basename(src_file)} -> {os.path.basename(dest_file)}")
                
                if idx % 5 == 0 or idx == total_files - 1:
                    QCoreApplication.processEvents()
            self.log(f"✅ 成功导出 {copied_count} 个表情文件！")
        except Exception as e:
            self.log(f"❌ 导出文件时出错: {e}")

    def selectAllLoaded(self):
        for i in range(self.previewListWidget.count()):
            item = self.previewListWidget.item(i)
            item.setSelected(True)
        self.log(f"✅ 已全选当前加载的 {self.previewListWidget.count()} 个表情")

    def clearSelection(self):
        self.previewListWidget.clearSelection()
        self.log("✅ 已清空当前的选择")

    def exportSelected(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            self.log("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            QMessageBox.information(self, '提示', '你还没有选择QQ号呢，请先选择一个QQ号！')
            return

        if not self.savePath:
            self.log("❌ 你还没有选择保存路径呢，请先选择保存路径！")
            QMessageBox.information(self, '提示', '你还没有选择保存路径呢，请先选择保存路径！')
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            self.log("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            QMessageBox.information(self, '提示', '你还没有选择表情分类呢，请先选择一个分类！')
            return

        selected_items = self.previewListWidget.selectedItems()
        if len(selected_items) == 0:
            self.log("❌ 您尚未选择任何表情！请先在右侧预览区选中表情后再导出。")
            QMessageBox.warning(self, '提示', '请先在右侧预览区选中表情后再导出！')
            return

        reply = QMessageBox.question(
            self,
            "确认导出选中",
            f"确定导出当前选中的 {len(selected_items)} 个表情？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导出操作")
            return

        display_name = QQExtractor.get_display_name(selected_qq)
        safe_name = self.sanitize_filename(display_name)
        output_dir = f"{self.savePath}/{safe_name}_{selected_folder}_提取的选中表情"
        self.log(f"✅ 正在复制选中的表情文件到: {output_dir}")
        selected_paths = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        self.copy_files_with_progress(selected_paths, output_dir)
        self.log("✅ 完成！正在打开输出文件夹……")
        try:
            subprocess.Popen(['explorer', os.path.abspath(output_dir)])
            QMessageBox.information(self, '完成', '选中表情提取成功！')
        except Exception as e:
            self.log(f"❌ 无法打开资源管理器: {e}")

    def exportAll(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            self.log("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            QMessageBox.information(self, '提示', '你还没有选择QQ号呢，请先选择一个QQ号！')
            return

        if not self.savePath:
            self.log("❌ 你还没有选择保存路径呢，请先选择保存路径！")
            QMessageBox.information(self, '提示', '你还没有选择保存路径呢，请先选择保存路径！')
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            self.log("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            QMessageBox.information(self, '提示', '你还没有选择表情分类呢，请先选择一个分类！')
            return

        if len(self.emoji_file_paths) == 0:
            userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
            if userdata_save_path:
                file_path = Path(os.path.join(userdata_save_path, selected_qq))
                emoji_path = file_path / "nt_qq" / "nt_data" / "Emoji" / selected_folder
                self.emoji_file_paths = QQExtractor.scan_emojis(emoji_path, selected_folder)

        if len(self.emoji_file_paths) == 0:
            self.log("❌ 该表情分类下未发现任何有效的图片文件，无法导出！")
            QMessageBox.warning(self, '提示', '该分类下未发现任何有效的表情图片文件！')
            return

        reply = QMessageBox.question(
            self,
            "确认导出全部",
            f"当前不管界面是否完全加载，将直接导出扫描到的该分类下所有 {len(self.emoji_file_paths)} 个表情？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导出操作")
            return

        display_name = QQExtractor.get_display_name(selected_qq)
        safe_name = self.sanitize_filename(display_name)
        output_dir = f"{self.savePath}/{safe_name}_{selected_folder}_提取的全部表情"
        self.log(f"✅ 正在复制所有表情文件到: {output_dir}")
        self.copy_files_with_progress(self.emoji_file_paths, output_dir)
        self.log("✅ 完成！正在打开输出文件夹……")
        try:
            subprocess.Popen(['explorer', os.path.abspath(output_dir)])
            QMessageBox.information(self, '完成', '全部提取成功！')
        except Exception as e:
            self.log(f"❌ 无法打开资源管理器: {e}")

    def import_files_with_progress(self, file_paths):
        try:
            main_win = self.window()
            if not hasattr(main_win, 'storage') or not main_win.storage:
                self.log("❌ 导入失败，无法获取表情包资源库存储服务！")
                QMessageBox.warning(self, '错误', '无法获取表情包资源库存储服务！')
                return

            storage = main_win.storage

            selected_qq = self.get_selected_qq()
            selected_folder = self.getSelectedFolder()
            
            # 建立一个资源库分类名称，例如 QQ_123456_personal_emoji
            category_name = f"QQ_{selected_qq}_{selected_folder}"
            
            storage.add_category(category_name)

            total_files = len(file_paths)
            self.progressBar.setMaximum(total_files)
            self.progressBar.setValue(0)

            imported_count = 0
            dup_count = 0
            fail_count = 0
            
            self.log(f"💬 开始导入表情到资源库，分类: [{category_name}]...")

            for idx, src_file in enumerate(file_paths):
                if not src_file or not os.path.exists(src_file):
                    fail_count += 1
                    continue
                
                # 如果是 APNG 文件，先转为临时 GIF 进行入库，使其在资源库中完整支持动态效果
                target_file_to_save = src_file
                if QQExtractor.is_apng_file(src_file):
                    temp_gif = QQExtractor.convert_apng_to_gif(src_file)
                    if temp_gif:
                        target_file_to_save = temp_gif

                # 经过存储的清洗保存程序（自动转码 PNG/GIF 并提取 MD5/Pixel 感知哈希进行全局去重）
                dest_path, is_duplicated = storage.save_file(target_file_to_save)
                if dest_path:
                    # 将清洗去重后的表情加入到这个分类下
                    storage.add_image_to_category(dest_path, category_name)
                    if is_duplicated:
                        dup_count += 1
                    imported_count += 1
                else:
                    fail_count += 1
                
                self.progressBar.setValue(idx + 1)
                filename = os.path.basename(src_file)
                self.log(f"导入 [{idx + 1}/{total_files}]: {filename} -> {category_name} {'(重复已被合并)' if is_duplicated else ''}")
                
                if idx % 5 == 0 or idx == total_files - 1:
                    QCoreApplication.processEvents()
            
            self.log(f"✅ 导入完成！成功导入并分类 {imported_count} 个表情，其中 {dup_count} 个重复已被合并过滤，失败 {fail_count} 个。")
            QMessageBox.information(self, '完成', f"表情导入成功！\n分类: {category_name}\n共导入并去重处理: {imported_count} 个")
        except Exception as e:
            self.log(f"❌ 导入到资源库时出错: {e}")
            QMessageBox.critical(self, '错误', f"导入出错: {e}")

    def importSelected(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            self.log("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            QMessageBox.information(self, '提示', '你还没有选择QQ号呢，请先选择一个QQ号！')
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            self.log("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            QMessageBox.information(self, '提示', '你还没有选择表情分类呢，请先选择一个分类！')
            return

        selected_items = self.previewListWidget.selectedItems()
        if len(selected_items) == 0:
            self.log("❌ 您尚未选择任何表情！请先在右侧预览区选中表情后再导入。")
            QMessageBox.warning(self, '提示', '请先在右侧预览区选中表情后再导入！')
            return

        reply = QMessageBox.question(
            self,
            "确认导入选中",
            f"确定将当前选中的 {len(selected_items)} 个表情导入到资源库？（会经过自动清洗和去重过滤）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导入操作")
            return

        selected_paths = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        self.import_files_with_progress(selected_paths)

    def importAll(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            self.log("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            QMessageBox.information(self, '提示', '你还没有选择QQ号呢，请先选择一个QQ号！')
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            self.log("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            QMessageBox.information(self, '提示', '你还没有选择表情分类呢，请先选择一个分类！')
            return

        if len(self.emoji_file_paths) == 0:
            userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
            if userdata_save_path:
                file_path = Path(os.path.join(userdata_save_path, selected_qq))
                emoji_path = file_path / "nt_qq" / "nt_data" / "Emoji" / selected_folder
                self.emoji_file_paths = QQExtractor.scan_emojis(emoji_path, selected_folder)

        if len(self.emoji_file_paths) == 0:
            self.log("❌ 该表情分类下未发现任何有效的图片文件，无法导入！")
            QMessageBox.warning(self, '提示', '该分类下未发现任何有效的表情图片文件！')
            return

        reply = QMessageBox.question(
            self,
            "确认导入全部",
            f"当前不管界面是否完全加载，将直接导入扫描到的该分类下所有 {len(self.emoji_file_paths)} 个表情到资源库？（自动清洗和去重）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log("💬 用户取消了导入操作")
            return

        self.import_files_with_progress(self.emoji_file_paths)

    def log(self, message):
        self.logTextEdit.append(message)
        self.statusLabel.setText(message)
        scrollbar = self.logTextEdit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.logTextEdit.ensureCursorVisible()

    def showHelp(self):
        help_text = "使用帮助：\n\n" \
                    "1. 使用时请确保已登录过QQ并加载过全部表情包\n" \
                    "2. 提取的时候会自动创建以QQ号开头的文件夹\n" \
                    "3. 选择一个账号后，点击'扫描表情包预览'获取表情包，然后选择表情，最后'导出选中表情'或'导出全部表情'。\n" \
                    "4. 导出的表情包比账号内实际的表情包要多属正常现象，因为QQ会缓存一些表情包\n\n" \
                    "  注意：如果没有找到任何用户，请确保QQ已经在本地登录过。并确保路径正确\n" \
                    "  可以尝试手动指定聊天数据文件夹的所在位置"
        
        QMessageBox.information(
            self,
            "使用帮助",
            help_text,
            QMessageBox.Ok
        )
