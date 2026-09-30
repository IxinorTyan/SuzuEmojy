import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from fluent_ui.components.update_dialog import UpdateDialog
from services.i18n import i18n_engine
from services.update_service import UpdateService
from test_updates import make_release


class UpdateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_release(self.root)
        self.service = UpdateService(self.root)
        self.dialog = UpdateDialog(service=self.service)

    def tearDown(self):
        self.dialog.shutdown()
        self.dialog.close()
        self.dialog.deleteLater()
        QApplication.processEvents()
        self.temp.cleanup()

    def wait_worker(self):
        for _ in range(200):
            QApplication.processEvents()
            if self.dialog.worker is None:
                return
            QTest.qWait(10)
        self.fail("Update worker did not finish")

    def test_construction_never_checks_network(self):
        with patch("services.update_service.requests.get") as get:
            other = UpdateDialog(service=self.service)
            QApplication.processEvents()
            get.assert_not_called()
            other.deleteLater()

    def test_manual_click_checks_in_worker(self):
        with patch.object(self.service, "check", return_value=None) as check:
            self.dialog.open_manual()
            self.wait_worker()
            check.assert_called_once()
        self.assertFalse(self.dialog.action_button.isEnabled())

    def test_ready_package_does_not_install_or_check_automatically(self):
        with patch.object(self.service, "ready", return_value={"version": "1.14.0", "directory": "stage-a"}), \
                patch.object(self.service, "check") as check, patch.object(self.service, "launch_installer") as launch:
            self.dialog.open_manual()
            QApplication.processEvents()
            self.assertTrue(self.dialog.action_button.isEnabled())
            self.dialog.close()
            check.assert_not_called()
            launch.assert_not_called()

    def test_network_failure_leaves_retry_and_release_page(self):
        with patch.object(self.service, "check", side_effect=OSError("offline")):
            self.dialog.check()
            self.wait_worker()
        self.assertIn("offline", self.dialog.notes.toPlainText())
        self.assertTrue(self.dialog.check_button.isEnabled())
        self.assertTrue(self.dialog.page_button.isEnabled())

    def test_active_import_prevents_restart(self):
        self.dialog._prepared({"version": "1.14.0"})
        with patch("fluent_ui.components.update_dialog.active_tasks", return_value=[object()]), \
                patch.object(self.service, "launch_installer") as launch:
            self.dialog.action()
            launch.assert_not_called()

    def test_translations_have_no_missing_update_labels(self):
        for lang in ("zh", "zh_TW", "en", "ja"):
            i18n_engine.set_language(lang)
            dialog = UpdateDialog(service=self.service)
            self.assertNotIn("MISSING", dialog.windowTitle())
            self.assertNotIn("MISSING", dialog.action_button.text())
            dialog._prepared({"version": "1.14.0"})
            self.assertNotIn("MISSING", dialog.status.text())
            dialog.deleteLater()
        i18n_engine.set_language("zh")


if __name__ == "__main__":
    unittest.main()
