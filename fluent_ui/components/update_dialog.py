"""User-initiated update workflow; background work never touches Qt widgets."""
import gc
import threading

from PySide6.QtCore import QThread, Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QMessageBox, QVBoxLayout,
)
from qfluentwidgets import BodyLabel, PrimaryPushButton, ProgressBar, PushButton, TextEdit, isDarkTheme

from services.i18n import t
from services.update_service import Cancelled, RELEASES_URL, UpdateService, current_version
from services.update_protocol import MANIFEST


class UpdateWorker(QThread):
    progress = Signal(int)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None
        self.cancelled = False

    def run(self):
        try:
            self.result = self.operation(self.progress.emit)
        except Cancelled:
            self.cancelled = True
        except Exception as exc:
            self.error = str(exc)


def active_tasks():
    """Include parentless QThreads, as several existing tools own those directly."""
    tasks = []
    for obj in gc.get_objects():
        if isinstance(obj, QThread):
            try:
                if obj != QThread.currentThread() and obj.isRunning():
                    tasks.append(obj)
            except RuntimeError:  # Already-deleted Qt wrapper
                pass
    tasks.extend(th for th in threading.enumerate()
                 if th.name in ("startup-maintenance", "SyncKeyMigrationThread") and th.is_alive())
    return tasks


class UpdateDialog(QDialog):
    def __init__(self, parent=None, service=None):
        super().__init__(parent)
        self.service = service or UpdateService()
        self.worker = None
        self.release = None
        self.prepared = None
        self.installing = False
        self.setWindowTitle(t("软件更新"))
        self.setObjectName("updateDialog")
        self.setStyleSheet("QDialog#updateDialog { background: " + ("#202020" if isDarkTheme() else "#f9f9f9") + "; }")
        self.resize(700, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self.status = BodyLabel(t("当前版本") + "：v" + current_version(self.service.root))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notes = TextEdit()
        self.notes.setReadOnly(True)
        layout.addWidget(self.notes)
        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.check_button = PushButton(t("检查更新"))
        self.action_button = PrimaryPushButton(t("下载更新"))
        self.action_button.setEnabled(False)
        self.cancel_button = PushButton(t("取消下载"))
        self.cancel_button.hide()
        self.page_button = PushButton(t("打开发布页"))
        self.later_button = PushButton(t("稍后"))
        for button in (self.check_button, self.action_button, self.cancel_button, self.page_button, self.later_button):
            row.addWidget(button)
        self.check_button.clicked.connect(self.check)
        self.action_button.clicked.connect(self.action)
        self.cancel_button.clicked.connect(self.service.cancel.set)
        self.page_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        self.later_button.clicked.connect(self.close)
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def open_manual(self):
        self.show()
        self.raise_()
        self.activateWindow()
        if self.worker is not None:
            return
        try:
            self.prepared = self.service.ready()
        except Exception as exc:
            self._error(str(exc))
            return
        if self.prepared:
            self._prepared(self.prepared)
        else:
            self.check()

    def _run(self, operation, finished, cancellable=False):
        if self.worker is not None:
            return
        self.service.cancel.clear()
        self.check_button.setEnabled(False)
        self.action_button.setEnabled(False)
        self.cancel_button.setVisible(cancellable)
        self.worker = UpdateWorker(operation, self)
        self.worker.progress.connect(self.progress.setValue)

        def complete():
            worker = self.worker
            self.worker = None
            self.check_button.setEnabled(True)
            self.cancel_button.hide()
            self.progress.hide()
            if worker.cancelled:
                self.status.setText(t("下载已取消"))
                self.action_button.setEnabled(self.release is not None)
            elif worker.error:
                self._error(worker.error)
            else:
                finished(worker.result)
            worker.deleteLater()
        self.worker.finished.connect(complete)
        self.worker.start()

    def _error(self, error):
        self.installing = False
        self.later_button.setEnabled(True)
        self.setWindowModality(Qt.NonModal)
        self.status.setText(t("更新失败，请重试或打开发布页"))
        self.notes.setPlainText(error)
        self.action_button.setEnabled(self.release is not None or self.prepared is not None)

    def check(self):
        if self.worker is not None:
            return
        self.release = None
        self.status.setText(t("正在检查更新…"))
        self._run(lambda progress: self.service.check(), self._checked)

    def _checked(self, release):
        if not release:
            self.status.setText(t("当前已是最新版本"))
            self.notes.clear()
            return
        self.release = release
        self.prepared = None
        self.status.setText(t("当前版本") + "：v" + current_version(self.service.root) + " → v" + release["version"] + " · " + release["date"][:10] + " · %.1f MB" % (release["size"] / 1024 ** 2))
        self.notes.setPlainText(release["notes"])
        self.action_button.setText(t("下载更新"))
        self.action_button.setEnabled((self.service.root / MANIFEST).is_file())
        if not (self.service.root / MANIFEST).is_file():
            self.status.setText(self.status.text() + "\n" + t("源码运行仅支持检查，请使用正式发布包进行更新"))

    def _prepared(self, item):
        self.prepared = item
        self.status.setText("v" + item["version"] + " · " + t("更新已准备好，点击重启后安装"))
        self.action_button.setText(t("重启并更新"))
        self.action_button.setEnabled(True)

    def action(self):
        if self.worker is not None:
            return
        if self.prepared:
            if active_tasks():
                self.status.setText(t("请等待导入、导出和后台任务结束后再更新"))
                return
            if QMessageBox.question(self, t("重启并更新"), t("现在退出软件并安装更新？用户数据将保留。"),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.installing = True
            self.later_button.setEnabled(False)
            self.setWindowModality(Qt.ApplicationModal)
            self.status.setText(t("正在准备重启更新…"))
            self._run(lambda progress: self.service.launch_installer(), self._quit)
        elif self.release:
            self.progress.setValue(0)
            self.progress.show()
            self.status.setText(t("正在下载并校验更新…"))
            self._run(lambda progress: self.service.download(self.release, progress), self._prepared, True)

    def _quit(self, result):
        # Wait for any work that started while the installer was being prepared.
        # aboutToQuit handlers then release application resources normally.
        for task in active_tasks():
            if isinstance(task, QThread):
                task.wait()
            else:
                task.join()
        QApplication.instance().quit()

    def reject(self):
        if not self.installing:
            super().reject()

    def closeEvent(self, event):
        if self.installing:
            event.ignore()
        else:
            super().closeEvent(event)

    def shutdown(self):
        self.service.cancel.set()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait()
