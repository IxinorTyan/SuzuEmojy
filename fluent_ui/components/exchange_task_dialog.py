"""Run exchange services off the GUI thread and retain workers until completion."""

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout


class ExchangeWorker(QThread):
    progress = Signal(int, int, str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation(self.progress.emit)
        except Exception as exc:
            self.error = exc


class ExchangeTaskDialog(QDialog):
    def __init__(self, parent, title, operation):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        self.label = QLabel(title, self)
        self.label.setWordWrap(True)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        self.worker = ExchangeWorker(operation, self)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.accept)

    def update_progress(self, current, total, message):
        self.label.setText(message)
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(current)

    def reject(self):
        # Do not destroy a worker or interrupt a database transaction via Escape.
        pass

    def closeEvent(self, event):
        event.ignore()


def run_exchange_task(parent, title, operation):
    dialog = ExchangeTaskDialog(parent, title, operation)
    QTimer.singleShot(0, dialog.worker.start)
    try:
        dialog.exec()
    finally:
        dialog.worker.wait()
    error, result = dialog.worker.error, dialog.worker.result
    dialog.deleteLater()
    if error is not None:
        raise error
    return result
