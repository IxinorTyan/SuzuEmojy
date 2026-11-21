"""Qt signals for the shared image-import job."""

from PySide6.QtCore import QThread, Signal
from services.import_pipeline import ImportJob


class ImportThread(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    completed = Signal(int, int, int)

    def __init__(self, filepaths, storage, target_category, delete_after=False,
                 parent=None, file_categories=None, folders=None):
        super().__init__(parent)
        self.job = ImportJob(filepaths, storage, target_category, delete_after, file_categories, folders,
                             cancel=self.isInterruptionRequested, progress=self.progress.emit, status=self.status.emit)

    def __getattr__(self, name):
        if name in {"filepaths", "folders", "folder_stats", "issues", "parallelism", "was_cancelled"}:
            return getattr(self.job, name)
        raise AttributeError(name)

    def run(self):
        self.completed.emit(*self.job.run())
