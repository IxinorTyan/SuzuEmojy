"""Batch package dialog with ordered pipeline work outside the GUI thread."""

import os
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from qfluentwidgets import TextEdit, LineEdit, PushButton, PrimaryPushButton, BodyLabel

from services.i18n import t
from services.tg_downloader import TGStickerDownloader
from services.tg_batch_export import TGPackageBatchRunner, package_filename_stem


class BatchPackageWorker(QThread):
    progress = Signal(int, str, int, int, str)

    def __init__(self, links, directory, config, storage, importer_factory, parent=None):
        super().__init__(parent)
        self.links = links
        self.directory = Path(directory)
        self.config = config
        self.storage = storage
        self.importer_factory = importer_factory
        self.cancel_event = Event()
        self.results = []
        self.error = None
        self.used_names = set()
        self.used_filenames = set()

    def cancel(self):
        self.cancel_event.set()

    def _process(self, item, cancel_event):
        if cancel_event.is_set():
            raise InterruptedError()
        title = item.pack.title or item.pack.name
        name, suffix = title, 2
        while name.casefold() in self.used_names:
            name = f"{title} ({suffix})"
            suffix += 1
        self.used_names.add(name.casefold())
        stem = package_filename_stem(title)
        filename, suffix = stem, 2
        while (filename.casefold() in self.used_filenames
               or (self.directory / (filename + ".zip")).exists()):
            filename = f"{stem} ({suffix})"
            suffix += 1
        self.used_filenames.add(filename.casefold())
        output = self.directory / (filename + ".zip")
        importer = self.importer_factory(
            item.downloader, self.storage, item.pack.stickers, name,
            export_path=str(output), source_url=item.pack.source_url,
            prepared_sources=item.sources, cancel_event=cancel_event,
        )
        errors = []
        phase = ["import"]

        def stage_changed(stage):
            phase[0] = stage
            self.progress.emit(item.index, stage, 0, 0, "")

        importer.stage_changed.connect(stage_changed, Qt.DirectConnection)
        importer.progress.connect(
            lambda done, total, message: self.progress.emit(item.index, phase[0], done, total, message),
            Qt.DirectConnection,
        )
        importer.failed.connect(errors.append, Qt.DirectConnection)
        importer.run()
        if errors:
            raise RuntimeError(errors[0])
        if not output.is_file():
            if cancel_event.is_set():
                raise InterruptedError()
            raise RuntimeError(t("资源包未生成"))
        message = t("已导出：处理成功 {success} 张，获取失败跳过 {skipped} 张。").format(
            success=importer.imported_count, skipped=importer.skipped_count,
        )
        details = list(importer.skipped_details)
        return dict(path=str(output), skipped=importer.skipped_count, skipped_details=details,
                    message="\n".join([message, str(output), *details]))

    def run(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # A same-title pack must not mix with an existing library category.
            self.used_names = {name.casefold() for name in self.storage.get_all_categories()}
            runner = TGPackageBatchRunner(
                self.links, lambda: TGStickerDownloader(**self.config), self._process,
                self.progress.emit, self.cancel_event,
            )
            self.results = runner.run()
        except Exception as exc:
            self.error = str(exc)


class TGBatchPackageDialog(QDialog):
    def __init__(self, parent, directory, config, storage, importer_factory):
        super().__init__(parent)
        self.config, self.storage = config, storage
        self.importer_factory = importer_factory
        self.worker = None
        self.setWindowTitle(t("批量打包 Telegram 贴纸"))
        self.resize(920, 640)
        self.setMinimumSize(680, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(BodyLabel(t("每行输入一个 TG 链接，按顺序处理，每个贴纸包生成独立 ZIP。"), self))
        self.links_edit = TextEdit(self)
        self.links_edit.setPlaceholderText("https://t.me/addstickers/Kei_Aris\nhttps://t.me/addstickers/...")
        self.links_edit.setMaximumHeight(150)
        layout.addWidget(self.links_edit)
        path_row = QHBoxLayout()
        self.path_edit = LineEdit(self)
        self.path_edit.setText(directory)
        self.browse_button = PushButton(t("浏览..."), self)
        self.browse_button.clicked.connect(self._browse)
        path_row.addWidget(BodyLabel(t("保存路径:"), self))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        layout.addLayout(path_row)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels([t("贴纸包 / 链接"), t("阶段"), t("进度"), t("结果 / 详情")])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setToolTip(t("双击任意行查看完整详情。"))
        self.table.cellDoubleClicked.connect(self._show_details)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 85)
        layout.addWidget(self.table, 1)
        self.summary = BodyLabel(t("解析、下载与入库打包可交错进行；单包失败后继续下一包。"), self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.start_button = PrimaryPushButton(t("开始批量打包"), self)
        self.start_button.clicked.connect(self._start)
        self.close_button = PushButton(t("关闭"), self)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _browse(self):
        directory = QFileDialog.getExistingDirectory(self, t("选择资源包保存目录"), self.path_edit.text())
        if directory:
            self.path_edit.setText(directory)

    def _running(self):
        return self.worker is not None and self.worker.isRunning()

    def _start(self):
        if self._running():
            return
        links = [line.strip() for line in self.links_edit.toPlainText().splitlines() if line.strip()]
        directory = self.path_edit.text().strip()
        if not links or not directory:
            QMessageBox.warning(self, t("提示"), t("请输入 TG 链接并选择保存目录。"))
            return
        self.table.setRowCount(len(links))
        for row, link in enumerate(links):
            for column, text in enumerate((link, t("等待中"), "", "")):
                self.table.setItem(row, column, QTableWidgetItem(text))
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = BatchPackageWorker(
            links, os.path.abspath(directory), self.config, self.storage, self.importer_factory, self,
        )
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        for widget in (self.links_edit, self.path_edit, self.browse_button, self.start_button):
            widget.setEnabled(False)
        self.close_button.setText(t("取消"))
        self.summary.setText(t("正在批量打包..."))
        self.worker.start()

    def _progress(self, row, stage, done, total, message):
        labels = {
            "parse": "解析中", "parsed": "等待下载", "download": "下载中",
            "ready": "等待入库", "import": "入库中", "export": "打包中",
            "done": "已完成", "failed": "失败", "cancelled": "已取消",
        }
        if stage == "parsed":
            self.table.item(row, 0).setText(message)
            self.table.item(row, 0).setToolTip(self.worker.links[row])
            message = ""
        self.table.item(row, 1).setText(t(labels[stage]))
        self.table.item(row, 2).setText(f"{done}/{total}" if total else "")
        self.table.item(row, 3).setText(message.replace("\n", " "))
        self.table.item(row, 3).setToolTip(message)

    def _show_details(self, row, column):
        item = self.table.item(row, 3)
        if item is None or not item.toolTip():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(t("结果 / 详情"))
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        content = TextEdit(dialog)
        content.setReadOnly(True)
        content.setPlainText(item.toolTip())
        layout.addWidget(content)
        close_button = PushButton(t("关闭"), dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _finished(self):
        counts = {status: sum(r["status"] == status for r in self.worker.results)
                  for status in ("done", "failed", "cancelled")}
        if self.worker.error:
            self.summary.setText(t("批量打包失败") + ": " + self.worker.error)
            for row in range(self.table.rowCount()):
                if self.table.item(row, 1).text() not in (t("已完成"), t("失败"), t("已取消")):
                    self._progress(row, "failed", 0, 0, self.worker.error)
        else:
            self.summary.setText(t("批量处理结束：成功 {success} 包，失败 {failed} 包，取消 {cancelled} 包。").format(
                success=counts["done"], failed=counts["failed"], cancelled=counts["cancelled"],
            ))
        for widget in (self.links_edit, self.path_edit, self.browse_button, self.start_button, self.close_button):
            widget.setEnabled(True)
        self.close_button.setText(t("关闭"))

    def reject(self):
        if self._running():
            self.worker.cancel()
            self.summary.setText(t("正在取消，等待当前操作收尾；已完成的 ZIP 将保留。"))
            self.close_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self._running():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

    def shutdown(self):
        if self._running():
            self.worker.cancel()
            self.worker.wait()
