"""On-demand visual cleanup, with bounded result pages and cancellable workers."""
import os
import time

from PySide6.QtCore import Qt, QSize, QThread, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QMovie, QImageReader
from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QGridLayout, QScrollArea, QMessageBox,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, SubtitleLabel, PushButton, PrimaryPushButton,
    ComboBox, Slider, CheckBox, ProgressBar, isDarkTheme,
)

from services.i18n import t
from services.similarity import scan, group_features, ScanCancelled
from fluent_ui.components.exchange_task_dialog import ExchangeWorker


def similarity_icon():
    """Code-native line icon, matching the toolbar's 20px Fluent glyphs."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('white' if isDarkTheme() else '#333333'), 1.5))
    painter.drawRoundedRect(3, 3, 13, 12, 2, 2)
    painter.drawLine(1, 7, 1, 18)
    painter.drawLine(1, 18, 11, 18)
    painter.drawEllipse(6, 6, 2, 2)
    painter.drawLine(5, 12, 9, 9)
    painter.drawLine(9, 9, 12, 12)
    painter.drawEllipse(12, 12, 7, 7)
    painter.drawLine(18, 18, 22, 22)
    painter.end()
    return QIcon(pixmap)


class SimilarityWorker(QThread):
    progress = Signal(str, int, int)

    def __init__(self, paths, cache_path, kind, threshold, features=None, parent=None):
        super().__init__(parent)
        self.paths, self.cache_path, self.kind = paths, cache_path, kind
        self.threshold = threshold
        self.features = features
        self.groups, self.failures = [], []
        self.error = None
        self.cancelled = False
        self._last_progress = 0

    def report(self, phase, current, total):
        now = time.monotonic()
        if current == total or now - self._last_progress >= 0.08:
            self._last_progress = now
            self.progress.emit(phase, current, total)

    def run(self):
        try:
            if self.features is None:
                self.features, self.failures = scan(
                    self.paths, self.cache_path, self.kind, self.isInterruptionRequested,
                    lambda n, total: self.report('扫描', n, total))
            self.groups = group_features(
                self.features, self.threshold, self.isInterruptionRequested,
                lambda n, total: self.report('匹配', n, total))
        except ScanCancelled:
            self.cancelled = True
        except Exception as error:
            self.error = str(error)


class ImageLabel(QLabel):
    clicked = Signal()

    def __init__(self, feature, size, parent=None, autoplay=False):
        super().__init__(parent)
        self.feature = feature
        self.setFixedSize(size)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.movie = None
        self.autoplay = autoplay
        reader = QImageReader(feature.path)
        original = reader.size()
        if original.isValid():
            reader.setScaledSize(original.scaled(size, Qt.KeepAspectRatio))
        picture = reader.read()
        self.still = QPixmap.fromImage(picture)
        if self.still.isNull():
            self.setText(t('无法预览'))
        else:
            self.setPixmap(self.still.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def play(self):
        if self.feature.animated and self.movie is None:
            self.movie = QMovie(self.feature.path, parent=self)
            self.movie.setCacheMode(QMovie.CacheNone)
            self.movie.setScaledSize(QSize(self.feature.width, self.feature.height).scaled(
                self.size(), Qt.KeepAspectRatio))
            self.setMovie(self.movie)
            self.movie.start()

    def stop(self):
        if self.movie:
            self.movie.stop()
            self.setMovie(None)
            self.movie.setFileName('')
            self.movie.deleteLater()
            self.movie = None
            self.setPixmap(self.still.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def enterEvent(self, event):
        self.play()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.autoplay:
            self.stop()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def description(feature, categories):
    text = f'{feature.width} × {feature.height}  ·  {feature.size / 1024:.1f} KB'
    if feature.animated:
        text += f'  ·  {feature.duration / 1000:.2f} s'
    return text + '\n' + (', '.join(categories.get(feature.path, [])) or t('无分类'))


class GroupPreview(QDialog):
    def __init__(self, group, index, categories, parent):
        super().__init__(parent)
        self.group, self.index, self.categories = group, index, categories
        self.setWindowTitle(t('同组图片预览'))
        self.layout = QVBoxLayout(self)
        self.picture = None
        self.details = BodyLabel(self)
        self.details.setWordWrap(True)
        self.layout.addWidget(self.details)
        row = QHBoxLayout()
        previous = PushButton(t('上一张'), self)
        following = PushButton(t('下一张'), self)
        previous.clicked.connect(lambda: self.navigate(-1))
        following.clicked.connect(lambda: self.navigate(1))
        row.addWidget(previous)
        row.addWidget(following)
        self.layout.addLayout(row)
        self.navigate(0)

    def navigate(self, step):
        self.index = (self.index + step) % len(self.group)
        if self.picture:
            self.picture.stop()
            self.layout.removeWidget(self.picture)
            self.picture.deleteLater()
        feature = self.group[self.index]
        screen = self.screen().availableGeometry().size()
        size = QSize(min(680, int(screen.width() * .7)), min(500, int(screen.height() * .65)))
        self.picture = ImageLabel(feature, size, self, autoplay=True)
        self.layout.insertWidget(0, self.picture, alignment=Qt.AlignCenter)
        self.picture.play()
        self.details.setText(f'{self.index + 1} / {len(self.group)}  ·  {os.path.basename(feature.path)}\n'
                             + description(feature, self.categories))

    def done(self, result):
        self.picture.stop()
        super().done(result)


class SimilarityDialog(QDialog):
    PAGE_SIZE = 36

    def __init__(self, gallery):
        super().__init__(gallery)
        self.gallery, self.storage = gallery, gallery.storage
        self.setWindowTitle(t('感知哈希去重'))
        self.setWindowModality(Qt.WindowModal)
        self.resize(960, 720)
        self.setMinimumSize(660, 480)
        self.worker = None
        self.features = None
        self.groups, self.entries = [], []
        self.selected = set()
        self.page = 0
        self.deleting = False
        self.delete_worker = None
        self.closing = False
        self.categories = {}
        self.failures = []
        self.pictures = []
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 16, 20, 16)
        self.layout.addWidget(SubtitleLabel(t('感知哈希去重'), self))
        hint = BodyLabel(t('结果为疑似相似图片，请逐张比较后勾选删除。动图悬停播放，点击图片放大。'), self)
        hint.setWordWrap(True)
        self.layout.addWidget(hint)
        row = QHBoxLayout()
        self.scope = ComboBox(self)
        self.scope.addItem(t('全部资源'), userData=None)
        for name in self.storage.get_all_categories():
            if name != '全部表情':
                self.scope.addItem(name, userData=name)
        self.kind = ComboBox(self)
        for name, value in [('全部图片', 'all'), ('仅静态图', 'static'), ('仅动图', 'animated')]:
            self.kind.addItem(t(name), userData=value)
        self.scan_button = PrimaryPushButton(t('开始扫描'), self)
        self.cancel_button = PushButton(t('取消'), self)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.scope)
        row.addWidget(self.kind)
        row.addStretch()
        row.addWidget(self.scan_button)
        row.addWidget(self.cancel_button)
        self.layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(BodyLabel(t('严格'), self))
        self.threshold = Slider(Qt.Horizontal, self)
        self.threshold.setRange(0, 16)
        self.threshold.setValue(6)
        row.addWidget(self.threshold)
        row.addWidget(BodyLabel(t('宽松'), self))
        self.threshold_label = CaptionLabel('6', self)
        row.addWidget(self.threshold_label)
        self.layout.addLayout(row)
        self.status = BodyLabel(t('选择范围后点击开始扫描。'), self)
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)
        self.progress = ProgressBar(self)
        self.progress.hide()
        self.layout.addWidget(self.progress)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName('similarityResults')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.previous = PushButton(t('上一页'), self)
        self.next = PushButton(t('下一页'), self)
        self.page_label = CaptionLabel(self)
        self.clear_button = PushButton(t('清空选择'), self)
        self.delete_button = PushButton(t('删除所选（0）'), self)
        for widget in (self.previous, self.page_label, self.next):
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.clear_button)
        row.addWidget(self.delete_button)
        self.layout.addLayout(row)
        self.rematch_timer = QTimer(self)
        self.rematch_timer.setSingleShot(True)
        self.rematch_timer.setInterval(180)
        self.rematch_timer.timeout.connect(self.rematch)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.setInterval(120)
        self.resize_timer.timeout.connect(self.render_page)
        self.threshold.valueChanged.connect(self.threshold_changed)
        self.threshold.sliderReleased.connect(self.rematch)
        self.scan_button.clicked.connect(self.start_scan)
        self.cancel_button.clicked.connect(self.cancel)
        self.scope.currentIndexChanged.connect(self.invalidate)
        self.kind.currentIndexChanged.connect(self.invalidate)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        self.clear_button.clicked.connect(self.clear_selection)
        self.delete_button.clicked.connect(self.delete_selected)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.render_page()
        self.apply_theme()

    def apply_theme(self):
        from qfluentwidgets import qconfig
        self.setStyleSheet('QDialog { background: ' + ('#202020' if isDarkTheme() else '#f9f9f9') + '; }')
        background = '#292929' if isDarkTheme() else '#f3f3f3'
        self.scroll.setStyleSheet(
            f'QScrollArea#similarityResults, QWidget#similarityCards {{ background: {background}; border: none; }}')
        # Signal is automatically disconnected when the dialog is destroyed.
        if not getattr(self, '_theme_connected', False):
            qconfig.themeChangedFinished.connect(self.apply_theme)
            self._theme_connected = True

    def invalidate(self):
        self.rematch_timer.stop()
        self.features = None
        self.groups, self.entries, self.failures = [], [], []
        self.selected.clear()
        self.page = 0
        self.status.setText(t('范围已变更，请点击开始扫描。'))
        self.render_page()

    def refresh_categories(self):
        selected = self.scope.currentData()
        self.scope.blockSignals(True)
        self.scope.clear()
        self.scope.addItem(t('全部资源'), userData=None)
        for name in self.storage.get_all_categories():
            if name != '全部表情':
                self.scope.addItem(name, userData=name)
        for index in range(self.scope.count()):
            if self.scope.itemData(index) == selected:
                self.scope.setCurrentIndex(index)
                break
        self.scope.blockSignals(False)
        self.invalidate()

    def threshold_changed(self, value):
        self.threshold_label.setText(str(value))
        if not self.threshold.isSliderDown():
            self.rematch_timer.start()

    def start_scan(self):
        if self.worker or self.deleting:
            return
        paths = self.storage.get_images_by_category(self.scope.currentData())
        self.categories = {p: list(cats) for p, cats in self.storage.get_image_to_categories_map().items()}
        self.failures = []
        self.start_worker(paths, None)

    def rematch(self):
        self.rematch_timer.stop()
        if self.features is not None and not self.worker and not self.deleting:
            self.start_worker([], self.features)

    def start_worker(self, paths, features):
        self.rematch_timer.stop()
        self.selected.clear()
        self.groups, self.entries = [], []
        self.page = 0
        self.render_page()
        self.set_busy(True)
        self.status.setText(t('正在扫描…') if features is None else t('正在重新匹配…'))
        self.progress.setValue(0)
        self.worker = SimilarityWorker(paths, os.path.join(self.storage.data_dir, 'cache', 'similarity-v1.db'),
                                       self.kind.currentData(), self.threshold.value(), features, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def set_busy(self, busy):
        for widget in (self.scope, self.kind, self.scan_button, self.threshold, self.scroll):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy and not self.deleting)
        self.progress.setVisible(busy)
        self.delete_button.setEnabled(not busy and bool(self.selected))
        self.clear_button.setEnabled(not busy and bool(self.selected))
        self.previous.setEnabled(not busy and self.page > 0)
        self.next.setEnabled(not busy and (self.page + 1) * self.PAGE_SIZE < len(self.entries))

    def on_progress(self, phase, current, total):
        self.status.setText(f'{t(phase)}：{current} / {total}')
        self.progress.setValue(int(current * 100 / max(total, 1)))

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        self.set_busy(False)
        if worker.error:
            self.features = None
            self.status.setText(t('扫描失败：') + worker.error)
        elif worker.cancelled:
            self.features = None
            self.status.setText(t('已取消，已完成的特征缓存可在下次扫描时复用。'))
        else:
            self.features = worker.features
            self.groups = worker.groups
            self.failures = worker.failures or self.failures
            self.entries = [(g, i) for g, group in enumerate(self.groups) for i in range(len(group))]
            self.status.setText(t('扫描完成：') + f'{len(self.groups)} ' + t('组疑似相似图片')
                                + f' · {len(self.entries)} ' + t('张图片')
                                + (f' · {len(self.failures)} ' + t('个文件无法读取，已跳过') if self.failures else ''))
            self.status.setToolTip('\n'.join(f'{os.path.basename(p)}: {error}' for p, error in self.failures[:20]))
            self.scan_button.setText(t('重新扫描'))
        worker.deleteLater()
        self.render_page()
        if self.closing:
            self.close()

    def cancel(self):
        if self.worker:
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status.setText(t('正在取消…'))

    def render_page(self):
        for picture in self.pictures:
            picture.stop()
        self.pictures = []
        container = QWidget(self)
        container.setObjectName('similarityCards')
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignTop)
        section, grid, column = None, None, 0
        columns = max(1, (self.scroll.viewport().width() - 24) // 191)
        page_entries = self.entries[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]
        for group_index, item_index in page_entries:
            group = self.groups[group_index]
            feature = group[item_index]
            if section != group_index:
                section, column = group_index, 0
                label = f'{t("相似组")} {group_index + 1} · {t("动图" if feature.animated else "静态图")} · {len(group)} {t("张")}'
                if item_index:
                    label += ' · ' + t('续')
                layout.addWidget(SubtitleLabel(label, container))
                grid = QGridLayout()
                grid.setAlignment(Qt.AlignLeft)
                layout.addLayout(grid)
            card = QWidget(container)
            card.setFixedWidth(185)
            card_layout = QVBoxLayout(card)
            picture = ImageLabel(feature, QSize(165, 140), card)
            picture.clicked.connect(lambda g=group, i=item_index: self.preview(g, i))
            self.pictures.append(picture)
            card_layout.addWidget(picture)
            details = CaptionLabel(description(feature, self.categories), card)
            details.setWordWrap(True)
            card_layout.addWidget(details)
            checkbox = CheckBox(t('选择删除'), card)
            checkbox.setChecked(feature.path in self.selected)
            checkbox.toggled.connect(lambda checked, p=feature.path: self.select(p, checked))
            card_layout.addWidget(checkbox)
            grid.addWidget(card, column // columns, column % columns, Qt.AlignTop)
            column += 1
        if not page_entries:
            layout.addWidget(BodyLabel(t('暂无相似图片'), container))
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(container)
        pages = max(1, (len(self.entries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        self.page_label.setText(f'{self.page + 1} / {pages}')
        self.update_selection()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'resize_timer'):
            self.resize_timer.start()

    def preview(self, group, index):
        dialog = GroupPreview(group, index, self.categories, self)
        dialog.exec()
        dialog.deleteLater()

    def change_page(self, delta):
        self.page += delta
        self.render_page()
        self.scroll.verticalScrollBar().setValue(0)

    def select(self, path, checked):
        if checked:
            self.selected.add(path)
        else:
            self.selected.discard(path)
        self.update_selection()

    def update_selection(self):
        self.delete_button.setText(t('删除所选') + f'（{len(self.selected)}）')
        enabled = bool(self.selected) and not self.worker and not self.deleting
        self.delete_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)

    def clear_selection(self):
        self.selected.clear()
        self.render_page()

    def delete_selected(self):
        if not self.selected or self.worker or self.deleting:
            return
        answer = QMessageBox.question(
            self, t('确认删除'),
            t('将从资源库及所有相关分类中永久删除所选图片，此操作不可恢复。')
            + f'\n{t("已选择")} {len(self.selected)} {t("张图片")}\n'
            + t('疑似相似不代表内容相同，请确认已逐张比较。'),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        paths = sorted(self.selected)
        self.deleting = True
        self.rematch_timer.stop()
        for picture in self.pictures:
            picture.stop()
        self.set_busy(True)
        self.status.setText(t('正在删除所选图片…'))
        self.progress.setValue(0)
        try:
            self.delete_worker = ExchangeWorker(
                lambda report: self.storage.delete_images_batch(
                    paths, progress_callback=lambda n, total: report(n, total, t('正在删除所选图片…'))),
                self)
            self.delete_worker.progress.connect(self.deletion_progress)
            self.delete_worker.finished.connect(self.deletion_finished)
            self.delete_worker.start()
        except Exception as error:
            if self.delete_worker:
                self.delete_worker.deleteLater()
                self.delete_worker = None
            self.deleting = False
            self.set_busy(False)
            self.status.setText(t('删除失败：') + str(error))

    def deletion_progress(self, current, total, message):
        self.status.setText(f'{message} {current} / {total}')
        self.progress.setValue(int(current * 100 / max(total, 1)))

    def deletion_finished(self):
        worker = self.delete_worker
        self.delete_worker = None
        error, result = worker.error, worker.result or {}
        worker.deleteLater()
        self.deleting = False
        self.selected.clear()
        self.set_busy(False)
        self.features = [f for f in (self.features or []) if os.path.isfile(f.path)]
        try:
            self.gallery.on_images_changed()
        except Exception as refresh_error:
            error = error or refresh_error
        if self.closing:
            self.close()
        elif error or result.get('failed'):
            # Some files may already be gone even if index persistence failed.
            remaining = {f.path for f in self.features}
            self.groups = [[f for f in group if f.path in remaining] for group in self.groups]
            self.groups = [group for group in self.groups if len(group) > 1]
            self.entries = [(g, i) for g, group in enumerate(self.groups) for i in range(len(group))]
            self.page = min(self.page, max(0, (len(self.entries) - 1) // self.PAGE_SIZE))
            self.render_page()
            details = str(error) if error else '\n'.join(result.get('failure_details', {}).values())
            self.status.setText(t('删除失败：') + (details or str(result.get('failed'))))
        else:
            self.rematch()

    def shutdown(self):
        if self.delete_worker:
            self.delete_worker.wait()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        self.rematch_timer.stop()
        self.resize_timer.stop()
        if self.worker or self.deleting:
            self.closing = True
            self.cancel()
            event.ignore()
            return
        for picture in self.pictures:
            picture.stop()
        self.closing = False
        event.accept()
        QDialog.done(self, QDialog.Rejected)
