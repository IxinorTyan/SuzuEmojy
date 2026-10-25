import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QWidget

from fluent_ui.views.gallery_view import GalleryInterface
from services.i18n import i18n_engine, t


MODULE = 'fluent_ui.views.gallery_view'


class FileActionGallery(GalleryInterface):
    def __init__(self):
        # Exercise real menus without opening the user's library or inbox.
        QWidget.__init__(self)
        self.current_category = '全部表情'
        self.storage = Mock()
        self.storage.get_all_categories.return_value = {}
        self.clipboard = Mock()
        self.show_error = Mock()
        self.show_success = Mock()


class GalleryFileActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name) / '中文 表情, & 符号'
        folder.mkdir()
        self.image = folder / '图片 空格.png'
        self.image.touch()
        self.view = FileActionGallery()
        self.addCleanup(self.view.deleteLater)

    def test_menu_actions_keep_the_original_path_when_card_is_reused(self):
        card = SimpleNamespace(image_path=str(self.image))
        menu = self.view._build_single_context_menu(card)
        actions = menu.actions()[-2:]
        self.assertEqual(actions[0].text(), t('在资源管理器中显示'))
        self.assertEqual(actions[1].text(), t('复制文件路径'))
        self.assertTrue(actions[0].icon().isNull())
        self.assertTrue(actions[1].icon().isNull())
        card.image_path = 'recycled-card.png'
        with patch.object(self.view, '_show_image_in_explorer') as reveal, \
                patch.object(self.view, '_copy_image_path') as copy_path:
            actions[0].trigger()
            actions[1].trigger()
        reveal.assert_called_once_with(str(self.image))
        copy_path.assert_called_once_with(str(self.image))
        self.view.clipboard.paste_to_window.assert_not_called()

    def test_explorer_receives_quoted_unicode_path_without_a_shell(self):
        with patch(MODULE + '.subprocess.Popen') as launch:
            self.view._show_image_in_explorer(str(self.image))
        launch.assert_called_once_with(f'explorer.exe /select,"{self.image}"')
        self.view.clipboard.auto_paste.cancel.assert_called_once()
        self.view.show_error.assert_not_called()
        self.view.storage.add_recent_image.assert_not_called()

    def test_copy_writes_only_absolute_path_text_and_cancels_pending_paste(self):
        clipboard = Mock()
        events = []
        self.view.clipboard.auto_paste.cancel.side_effect = lambda: events.append('cancel')
        clipboard.setText.side_effect = lambda text: events.append('copy')
        # Relative paths must also work, independent of the caller's path format.
        relative = str(self.image.relative_to(self.temp.name))
        previous_directory = os.getcwd()
        try:
            os.chdir(self.temp.name)
            with patch.object(QApplication, 'clipboard', return_value=clipboard):
                self.view._copy_image_path(relative)
        finally:
            os.chdir(previous_directory)
        clipboard.setText.assert_called_once_with(str(self.image))
        clipboard.setMimeData.assert_not_called()
        self.assertEqual(events, ['cancel', 'copy'])
        self.view.show_success.assert_called_once_with(t('已复制文件路径'))
        self.view.clipboard.paste_to_window.assert_not_called()
        self.view.clipboard.copy_image_to_clipboard.assert_not_called()
        self.view.storage.add_recent_image.assert_not_called()

    def test_missing_file_or_directory_does_not_open_or_replace_clipboard(self):
        with patch(MODULE + '.subprocess.Popen') as launch, \
                patch.object(QApplication, 'clipboard') as clipboard:
            for path in (self.image.parent / 'missing.png', self.image.parent):
                for action in (self.view._show_image_in_explorer, self.view._copy_image_path):
                    with self.subTest(path=path, action=action.__name__):
                        self.view.show_error.reset_mock()
                        action(str(path))
                        self.view.show_error.assert_called_once_with(t('文件不存在'), str(path))
            launch.assert_not_called()
            clipboard.assert_not_called()
        self.view.show_success.assert_not_called()

    def test_file_removed_after_menu_creation_is_checked_on_click(self):
        menu = self.view._build_single_context_menu(SimpleNamespace(image_path=str(self.image)))
        self.image.unlink()
        with patch(MODULE + '.subprocess.Popen') as launch:
            menu.actions()[-2].trigger()
        launch.assert_not_called()
        self.view.show_error.assert_called_once_with(t('文件不存在'), str(self.image))

    def test_explorer_launch_failure_is_reported(self):
        with patch(MODULE + '.subprocess.Popen', side_effect=OSError('launch failed')):
            self.view._show_image_in_explorer(str(self.image))
        self.view.show_error.assert_called_once_with(t('无法打开资源管理器'), 'launch failed')

    def test_new_labels_exist_in_every_supported_translation(self):
        keys = ('在资源管理器中显示', '复制文件路径', '已复制文件路径', '无法打开资源管理器')
        for language in ('zh', 'zh_TW', 'en', 'ja'):
            with self.subTest(language=language):
                translations = json.loads(
                    (Path(i18n_engine.translations_dir) / f'{language}.json').read_text(encoding='utf-8')
                )
                for key in keys:
                    self.assertTrue(translations[key])


if __name__ == '__main__':
    unittest.main()
