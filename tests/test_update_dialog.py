import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from fluent_ui.components.update_dialog import UpdateDialog
from services.i18n import i18n_engine
from services.update_errors import UpdateError, UpdateNetworkError
from services.update_protocol import MANIFEST
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

    def test_worker_reports_mirror_source_to_ui(self):
        def check(report=None):
            report("GitHub")
            report("gh-proxy.com")
            return None
        with patch.object(self.service, "check", side_effect=check):
            self.dialog.check()
            self.wait_worker()
        self.assertIn("gh-proxy.com", self.dialog.source_label.text())
        self.assertFalse(self.dialog.source_label.isHidden())

    def test_all_source_errors_can_be_copied_and_check_retried(self):
        error = UpdateNetworkError("check", [
            ("GitHub", "https://api.github.com/example", UpdateError("timeout", "direct timeout")),
            ("gh-proxy.com", "https://gh-proxy.com/example", UpdateError("dns", "getaddrinfo failed")),
        ])
        with patch.object(self.service, "check", side_effect=[error, None]):
            self.dialog.check()
            self.wait_worker()
            details = self.dialog.notes.toPlainText()
            for expected in ("https://api.github.com/example", "https://gh-proxy.com/example", "direct timeout", "getaddrinfo failed"):
                self.assertIn(expected, details)
            self.assertFalse(self.dialog.copy_error_button.isHidden())
            clipboard = Mock()
            with patch("fluent_ui.components.update_dialog.QApplication.clipboard", return_value=clipboard):
                self.dialog.copy_error_button.click()
            clipboard.setText.assert_called_once_with(details)
            self.dialog.check()
            self.wait_worker()
        self.assertTrue(self.dialog.copy_error_button.isHidden())
        self.assertEqual(self.dialog.notes.toPlainText(), "")

    def available_release(self):
        return dict(version="1.14.0", date="2026-10-25", notes="Release notes", size=1234)

    def test_incomplete_installation_explains_missing_file_and_disables_download(self):
        for filename in (MANIFEST, "SuzuEmojyUpdater.exe"):
            with self.subTest(filename=filename):
                make_release(self.root)
                (self.root / filename).unlink()
                self.dialog._checked(self.available_release())
                self.assertFalse(self.dialog.action_button.isEnabled())
                details = self.dialog.notes.toPlainText()
                self.assertIn(filename, details)
                self.assertIn("Release notes", details)
                self.assertFalse(self.dialog.copy_error_button.isHidden())

    def test_download_failure_can_retry_same_release(self):
        release = self.available_release()
        self.dialog._checked(release)
        self.assertTrue(self.dialog.action_button.isEnabled())
        error = UpdateNetworkError("download", [("GitHub", "https://github.com/example", UpdateError("timeout"))])
        prepared = dict(version="1.14.0", directory="stage-test")
        with patch.object(self.service, "download", side_effect=[error, prepared]) as download:
            self.dialog.action()
            self.wait_worker()
            self.assertIs(self.dialog.release, release)
            self.assertTrue(self.dialog.action_button.isEnabled())
            self.assertFalse(self.dialog.copy_error_button.isHidden())
            self.dialog.action()
            self.wait_worker()
        self.assertEqual(download.call_count, 2)
        self.assertEqual(self.dialog.prepared, prepared)
        self.assertTrue(self.dialog.copy_error_button.isHidden())

    def test_rechecking_prepared_update_resets_stale_action(self):
        self.dialog._prepared(dict(version="1.14.0", directory="stage-test"))
        with patch.object(self.service, "check", side_effect=OSError("offline")):
            self.dialog.check()
            self.wait_worker()
        self.assertIsNone(self.dialog.prepared)
        self.assertIsNone(self.dialog.release)
        self.assertFalse(self.dialog.action_button.isEnabled())

    def test_active_import_prevents_restart(self):
        self.dialog._prepared({"version": "1.14.0"})
        with patch("fluent_ui.components.update_dialog.active_tasks", return_value=[object()]), \
                patch.object(self.service, "launch_installer") as launch:
            self.dialog.action()
            launch.assert_not_called()

    def test_translations_have_no_missing_update_labels(self):
        for lang in ("zh", "zh_TW", "en", "ja"):
            i18n_engine.load_language(lang)
            dialog = UpdateDialog(service=self.service)
            self.assertNotIn("MISSING", dialog.windowTitle())
            self.assertNotIn("MISSING", dialog.action_button.text())
            self.assertNotIn("MISSING", dialog.copy_error_button.text())
            dialog._source_changed("gh-proxy.com")
            self.assertNotIn("MISSING", dialog.source_label.text())
            dialog._error(UpdateNetworkError("download", [("GitHub", "https://github.com/example", UpdateError("timeout"))]))
            self.assertNotIn("MISSING", dialog.notes.toPlainText())
            dialog._prepared({"version": "1.14.0"})
            self.assertNotIn("MISSING", dialog.status.text())
            dialog.deleteLater()
        i18n_engine.load_language("zh")


if __name__ == "__main__":
    unittest.main()
