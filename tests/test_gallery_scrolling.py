import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, QTimer, Qt
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGridLayout, QVBoxLayout, QWidget

from fluent_ui.components.emoji_card import EmojiCard, ThumbnailCache, _thumbnail_pool
from fluent_ui.components.quick_panel import QuickPanel
from fluent_ui.components.responsive_scroll_area import ResponsiveScrollArea
from fluent_ui.components.virtual_grid_layout import VirtualGridLayout
from fluent_ui.views.gallery_view import GalleryInterface


class ResponsiveScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = ResponsiveScrollArea()
        self.area.resize(600, 400)
        content = QWidget()
        content.setMinimumSize(1500, 5000)
        self.area.setWidget(content)
        self.area.show()
        self.app.processEvents()
        self.bar = self.area.verticalScrollBar()
        self.bar.setSingleStep(20)
        self.bar.setValue(500)
        self.lines = patch.object(QApplication, 'wheelScrollLines', return_value=3)
        self.lines.start()
        self.addCleanup(self.lines.stop)

    def tearDown(self):
        self.area.close()
        self.area.deleteLater()
        self.app.processEvents()

    def wheel(self, angle=0, pixels=None, modifiers=Qt.NoModifier):
        event = QWheelEvent(
            QPointF(100, 100), QPointF(100, 100), pixels or QPoint(),
            QPoint(0, angle), Qt.NoButton, modifiers, Qt.ScrollUpdate, False,
        )
        QApplication.sendEvent(self.area.viewport(), event)

    def test_touchpad_pixels_follow_immediately_without_extra_inertia(self):
        self.wheel(angle=-120, pixels=QPoint(0, -37))
        self.assertEqual(self.bar.value(), 537)
        QTest.qWait(150)
        self.assertEqual(self.bar.value(), 537)

    def test_wheel_starts_promptly_and_settles_within_150ms(self):
        self.wheel(angle=-120)
        QTest.qWait(35)
        self.assertGreater(self.bar.value(), 500)
        QTest.qWait(115)
        self.assertEqual(self.bar.value(), 560)

    def test_reversing_discards_pending_forward_distance(self):
        self.wheel(angle=-120)
        QTest.qWait(35)
        turning_point = self.bar.value()
        self.wheel(angle=120)
        QTest.qWait(35)
        self.assertLess(self.bar.value(), turning_point)
        QTest.qWait(115)
        self.assertEqual(self.bar.value(), turning_point - 60)

    def test_touchpad_interrupts_mouse_animation(self):
        self.wheel(angle=-120)
        QTest.qWait(35)
        before = self.bar.value()
        self.wheel(pixels=QPoint(0, 23))
        self.assertEqual(self.bar.value(), before - 23)
        QTest.qWait(150)
        self.assertEqual(self.bar.value(), before - 23)

    def test_high_resolution_wheel_keeps_fractional_distance(self):
        for _ in range(20):
            self.wheel(angle=-1)
        QTest.qWait(150)
        self.assertEqual(self.bar.value(), 510)

    def test_dragging_scrollbar_stops_wheel_animation(self):
        self.wheel(angle=-120)
        self.area.scrollDelagate.vScrollBar.sliderPressed.emit()
        self.bar.setValue(900)
        QTest.qWait(150)
        self.assertEqual(self.bar.value(), 900)


class LoadingGallery(GalleryInterface):
    """真实滚动区、布局和异步卡片，不使用用户图库或收件箱。"""

    def __init__(self):
        QWidget.__init__(self)
        class Config(dict):
            def set(self, key, value):
                self[key] = value

        self.config = Config(thumbnail_size=80, render_batch_size=200)
        self.LAYOUT_TOP_MARGIN = 8
        self.LAYOUT_SPACING = 10
        self._all_current_images = []
        self._all_card_widgets = []
        self._active_thumbnail_widgets = set()
        self._pending_import_images = []
        self._loaded_count = 0
        self._is_loading = False
        self.is_selection_mode = False
        self.selected_paths = set()
        self._responsive_layout_timer = QTimer(self)
        self._responsive_layout_timer.setSingleShot(True)
        self._responsive_layout_timer.setInterval(50)
        self._responsive_layout_timer.timeout.connect(self._trigger_responsive_layout)
        self._lazy_load_timer = QTimer(self)
        self._lazy_load_timer.setSingleShot(True)
        self._lazy_load_timer.timeout.connect(self._apply_lazy_loading)
        layout = QVBoxLayout(self)
        self.scroll_area = ResponsiveScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        layout.addWidget(self.scroll_area)
        self.grid_container = QWidget()
        self.gallery_layout = VirtualGridLayout(self.grid_container)
        self.gallery_layout.setSpacing(10)
        self.gallery_layout.setContentsMargins(16, 8, 16, 16)
        self.gallery_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll_area.setWidget(self.grid_container)
        self.scroll_area.viewport().installEventFilter(self)


class GalleryRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Suzu scroll ')
        self.path = Path(self.temp.name) / '缩略图.png'
        image = QImage(500, 400, QImage.Format_RGB32)
        image.fill(Qt.green)
        self.assertTrue(image.save(str(self.path)))
        self.view = LoadingGallery()
        self.view.resize(640, 480)
        self.view.show()
        QTest.qWait(80)
        self.view._all_current_images = [str(self.path)] * 1000

    def tearDown(self):
        self.view.clear_gallery()
        _thumbnail_pool.waitForDone(5000)
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_for_render(self):
        for _ in range(600):
            self.app.processEvents()
            # 让 Python QRunnable 线程也能获得 GIL，模拟 app.exec() 的等待。
            time.sleep(0.005)
            if not self.view._is_loading:
                return
        self.fail('分帧渲染未完成')

    def settle(self):
        # Also release Python decode threads, as the real app.exec() does.
        for _ in range(50):
            self.app.processEvents()
            time.sleep(.005)
        self.wait_for_render()

    def test_initial_render_is_bounded_and_scroll_range_covers_whole_library(self):
        self.view._load_next_batch()
        self.settle()
        grid = self.view._virtual_gallery
        first, last = grid.index_range(1)
        self.assertEqual(set(grid.cards), set(range(first, last)))
        self.assertLess(len(grid.cards), 150)
        bar = self.view.scroll_area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 10000)
        original_maximum = bar.maximum()
        for _ in range(200):
            self.app.processEvents()
            time.sleep(.005)
            if all(card._is_loaded for card in grid.cards.values()):
                break
        self.assertTrue(all(card._is_loaded for card in grid.cards.values()))
        self.assertEqual(bar.maximum(), original_maximum)

    def test_jump_to_end_creates_only_nearby_cards_without_changing_range(self):
        self.view._load_next_batch()
        self.settle()
        initial_cards = set(self.view._all_card_widgets)
        bar = self.view.scroll_area.verticalScrollBar()
        maximum = bar.maximum()
        bar.setValue(maximum)
        self.settle()
        grid = self.view._virtual_gallery
        first, last = grid.index_range(1)
        self.assertEqual(set(grid.cards), set(range(first, last)))
        self.assertIn(999, grid.cards)
        self.assertLess(len(grid.cards), 150)
        self.assertEqual(bar.maximum(), maximum)
        self.assertTrue(initial_cards & set(grid.cards.values()), '跨屏应复用卡片')

    def test_active_scroll_creates_no_cards_and_submits_no_thumbnail_loads(self):
        self.view._load_next_batch()
        self.settle()
        grid = self.view._virtual_gallery
        old_indices = set(grid.cards)
        with patch.object(grid, '_create_card', wraps=grid._create_card) as create, \
                patch('fluent_ui.components.emoji_card._thumbnail_pool.start') as decode:
            bar = self.view.scroll_area.verticalScrollBar()
            for value in range(1000, 7000, 400):
                bar.setValue(value)
                self.app.processEvents()
                time.sleep(.005)
            self.assertTrue(self.view.scroll_area.is_scrolling)
            self.assertEqual(set(grid.cards), old_indices)
            create.assert_not_called()
            decode.assert_not_called()
        self.settle()
        self.assertNotEqual(set(grid.cards), old_indices)

    def test_slow_decodes_do_not_build_an_unbounded_queue(self):
        self.view._all_current_images = [f'slow-{index}.png' for index in range(1000)]
        with patch('fluent_ui.components.emoji_card._thumbnail_pool.start') as submit:
            self.view._load_next_batch()
            self.settle()
            grid = self.view._virtual_gallery
            for _ in range(100):
                grid.load_thumbnails()
            self.assertEqual(submit.call_count, 2)
            loading = [card for card in grid.cards.values() if card._is_loading]
            self.assertEqual(len(loading), 2)
            first = loading[0]
            image = QImage(64, 64, QImage.Format_RGB32)
            image.fill(Qt.green)
            first._on_thumbnail_loaded(first.image_path, image, first._load_request_id, 64)
            grid.load_thumbnails()
            self.assertEqual(submit.call_count, 3)

    def test_pending_render_yields_immediately_to_scroll(self):
        self.view._load_next_batch()
        grid = self.view._virtual_gallery
        grid.render_timer.stop()
        self.view.scroll_area._mark_scrolling()
        grid.render_chunk()
        self.assertEqual(len(grid.cards), 0)

    def test_middle_scroll_keeps_population_and_decoding_paused_at_rest(self):
        self.view._load_next_batch()
        self.settle()
        grid = self.view._virtual_gallery
        viewport = self.view.scroll_area.viewport()
        point = viewport.mapToGlobal(QPoint(100, 100))
        with patch.object(grid, '_create_card') as create, patch('fluent_ui.components.emoji_card._thumbnail_pool.start') as decode:
            for kind, buttons in ((QEvent.MouseButtonPress, Qt.MiddleButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
                event = QMouseEvent(kind, QPointF(100, 100), QPointF(point), Qt.MiddleButton, buttons, Qt.NoModifier)
                QApplication.sendEvent(viewport, event)
            middle = self.view.scroll_area._middle_scroller
            middle.timer.stop()
            self.assertTrue(middle.active)
            self.view.scroll_area._finish_scrolling()
            grid.render_chunk()
            grid.load_thumbnails()
            create.assert_not_called()
            decode.assert_not_called()
            middle.stop()

    def test_visible_decoding_starts_before_overscan_cards_finish(self):
        self.view._all_current_images = [f'pipeline-{index}.png' for index in range(1000)]
        self.view._load_next_batch()
        grid = self.view._virtual_gallery
        self.view.scroll_area._settle_timer.stop()
        self.view.scroll_area.is_scrolling = False
        grid.refresh()
        grid.render_timer.stop()
        with patch('fluent_ui.components.emoji_card._thumbnail_pool.start') as submit:
            grid.render_chunk()
            self.assertTrue(grid._missing, '不应等待预加载区域全部建好')
            self.assertTrue(self.view._is_loading)
            grid.load_thumbnails()
            self.assertGreater(submit.call_count, 0)
            first, last = grid.index_range()
            loading = [index for index, card in grid.cards.items() if card._is_loading]
            self.assertTrue(all(first <= index < last for index in loading))

    def test_inflight_decode_is_unpaused_even_before_it_returns_a_result(self):
        self.view._all_current_images = [f'inflight-{index}.png' for index in range(1000)]
        self.view._load_next_batch()
        grid = self.view._virtual_gallery
        self.view.scroll_area._settle_timer.stop()
        self.view.scroll_area.is_scrolling = False
        grid.refresh()
        grid.render_timer.stop()
        with patch('fluent_ui.components.emoji_card._thumbnail_pool.start'):
            grid.render_chunk()
            grid.load_thumbnails()
            loading = [card for card in grid.cards.values() if card._is_loading]
            self.assertTrue(loading)
            grid._scrolling_changed(True)
            self.assertTrue(all(card._loading_paused for card in loading))
            grid.refresh()
            grid.load_thumbnails()
            card = loading[0]
            self.assertFalse(card._loading_paused)
            card._on_thumbnail_loaded(card.image_path, QImage(64, 64, QImage.Format_RGB32), card._load_request_id, 64)
            self.assertTrue(card._is_loaded)
            self.assertIsNone(card._pending_thumbnail_result)

    def test_render_yields_after_one_expensive_card(self):
        self.view._load_next_batch()
        self.view.scroll_area._settle_timer.stop()
        self.view.scroll_area.is_scrolling = False
        self.view._virtual_gallery.refresh()
        self.view._render_timer.stop()
        with patch('fluent_ui.components.virtual_gallery.perf_counter', side_effect=[0, .01]):
            self.view._render_chunk()
        self.assertEqual(self.view._loaded_count, 1)
        self.assertFalse(self.view._all_card_widgets[0]._is_loaded)

    def test_resize_updates_virtual_positions_and_keeps_population_bounded(self):
        self.view._load_next_batch()
        self.settle()
        self.view.resize(720, 800)
        self.settle()
        grid = self.view._virtual_gallery
        self.assertLess(len(grid.cards), 250)
        for index, card in grid.cards.items():
            item = self.view.gallery_layout.itemAtPosition(index // self.view._current_columns,
                                                          index % self.view._current_columns)
            self.assertIs(item.widget(), card)

    def test_reopen_and_same_data_refresh_preserve_cards_and_loaded_pixmaps(self):
        self.view._load_next_batch()
        self.settle()
        grid = self.view._virtual_gallery
        first_card = grid.cards[0]
        for _ in range(200):
            self.app.processEvents()
            time.sleep(.005)
            if first_card._is_loaded:
                break
        self.assertTrue(first_card._is_loaded)
        key = first_card.pixmap().cacheKey()
        geometry = first_card.geometry()
        # Idle cache trimming must not evict the QLabel's currently visible pixmap.
        ThumbnailCache()._on_idle_timeout()
        self.view.hide()
        self.view.show()
        with patch.object(self.view, '_filter_images', return_value=list(self.view._all_current_images)), \
                patch.object(self.view, 'clear_gallery', wraps=self.view.clear_gallery) as clear:
            self.view.on_images_changed()
            self.settle()
            clear.assert_not_called()
        self.assertIs(grid.cards[0], first_card)
        self.assertEqual(first_card.pixmap().cacheKey(), key)
        self.assertEqual(first_card.geometry(), geometry)

    def test_hide_mid_render_then_hotkey_style_show_resumes_missing_cards(self):
        self.view._load_next_batch()
        self.view.scroll_area._settle_timer.stop()
        self.view.scroll_area.is_scrolling = False
        self.view._virtual_gallery.refresh()
        self.view._render_timer.stop()
        # Leave a partial batch regardless of how quickly this machine can
        # create cards within the renderer's time budget.
        with patch('fluent_ui.components.virtual_gallery.perf_counter', side_effect=[0, .01]):
            self.view._render_chunk()
        self.assertLessEqual(len(self.view._all_card_widgets), 2)
        self.view.hide()
        self.view.show()
        self.settle()
        grid = self.view._virtual_gallery
        first, last = grid.index_range(1)
        self.assertEqual(set(grid.cards), set(range(first, last)))

    def test_ten_thousand_images_do_not_accumulate_widgets_on_repeated_jumps(self):
        self.view._all_current_images = [str(self.path)] * 10000
        self.view._load_next_batch()
        self.settle()
        grid = self.view._virtual_gallery
        bar = self.view.scroll_area.verticalScrollBar()
        original_maximum = bar.maximum()
        for fraction in (.75, .25, 1, 0):
            bar.setValue(int(original_maximum * fraction))
            self.settle()
            self.assertLess(len(grid.cards), 150)
            self.assertLess(len(self.view.grid_container.findChildren(EmojiCard)), 150)
            self.assertEqual(bar.maximum(), original_maximum)
            for index, card in grid.cards.items():
                self.assertEqual(card.image_path, self.view._all_current_images[index])

    def test_shift_selection_includes_unmaterialized_images(self):
        self.view._all_current_images = [f'{index}.png' for index in range(10000)]
        self.view.selected_paths = {'20.png'}
        self.view._shift_selected_paths = set()
        with patch.object(self.view, 'update_selection_count'):
            self.view._execute_shift_selection('20.png', '9020.png')
        self.assertEqual(len(self.view.selected_paths), 9001)
        self.assertIn('5000.png', self.view.selected_paths)

    def test_reorder_updates_full_data_order_even_when_cards_do_not_exist(self):
        self.view._all_current_images = [f'{index}.png' for index in range(10000)]
        reordered = list(reversed(self.view._all_current_images))
        with patch.object(self.view, '_load_next_batch'):
            self.view._reorder_widgets(reordered)
        self.assertEqual(self.view._all_current_images, reordered)

    def test_ctrl_wheel_keeps_thumbnail_zoom(self):
        event = QWheelEvent(
            QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, 120),
            Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False,
        )
        with patch.object(self.view, '_apply_thumbnail_size') as resize:
            QApplication.sendEvent(self.view.scroll_area.viewport(), event)
            resize.assert_called_once_with(90)
        self.assertEqual(self.view.config['thumbnail_size'], 90)

    def test_clear_cancels_render_and_pending_scroll(self):
        self.view._load_next_batch()
        self.view.clear_gallery()
        QTest.qWait(80)
        self.assertEqual(self.view._loaded_count, 0)
        self.assertEqual(self.view.gallery_layout.count(), 0)
        self.assertFalse(self.view._render_timer.isActive())


class ThumbnailPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.card = EmojiCard('pending.png', size=80, load_image=False)
        self.image = QImage(64, 64, QImage.Format_RGB32)
        self.image.fill(Qt.green)

    def tearDown(self):
        self.card.clear_resources()
        self.card.deleteLater()
        self.app.processEvents()

    def test_decoded_thumbnail_does_not_change_pixmap_during_motion(self):
        self.card.set_loading_paused(True)
        self.card._on_thumbnail_loaded('pending.png', self.image, self.card._load_request_id, 64)
        self.assertFalse(self.card._is_loaded)
        self.assertTrue(self.card.pixmap().isNull())
        self.card.set_loading_paused(False)
        self.assertTrue(self.card._is_loaded)
        self.assertFalse(self.card.pixmap().isNull())

    def test_recycled_card_drops_old_pending_image(self):
        self.card.set_loading_paused(True)
        self.card._on_thumbnail_loaded('pending.png', self.image, self.card._load_request_id, 64)
        self.card.clear_resources()
        self.card.image_path = 'different.png'
        self.card.set_loading_paused(False)
        self.assertFalse(self.card._is_loaded)
        self.assertTrue(self.card.pixmap().isNull())

    def test_repeated_same_size_update_never_clears_the_existing_pixmap(self):
        self.card._on_thumbnail_loaded('pending.png', self.image, self.card._load_request_id, 64)
        key = self.card.pixmap().cacheKey()
        with patch.object(self.card, '_load_thumbnail_async') as load:
            self.card.update_size(80)
            load.assert_not_called()
        self.assertEqual(self.card.pixmap().cacheKey(), key)

    def test_slow_image_does_not_block_an_independent_fast_decode(self):
        blocked = threading.Event()
        started = threading.Event()
        token = str(time.monotonic_ns())
        self.card.image_path = 'slow-' + token + '.png'
        fast = EmojiCard('fast-' + token + '.png', size=80, load_image=False)

        def reader_for(path):
            reader = Mock()
            reader.size.return_value = QSize(64, 64)

            def read():
                if path == self.card.image_path:
                    started.set()
                    blocked.wait(5)
                return self.image.copy()

            reader.read.side_effect = read
            return reader

        try:
            with patch('fluent_ui.components.emoji_card.QImageReader', side_effect=reader_for):
                self.card.update_size(80)
                fast.update_size(80)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not (started.is_set() and fast._is_loaded):
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertTrue(started.is_set())
                self.assertTrue(fast._is_loaded, '慢图解码期间，小图仍应能完成')
                self.assertFalse(self.card._is_loaded)
        finally:
            blocked.set()
            _thumbnail_pool.waitForDone(5000)
            self.card.clear_resources()
            fast.clear_resources()
            fast.deleteLater()
            self.app.processEvents()


class QuickPanelScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_quick_panel_defers_loading_while_scrolling(self):
        storage = Mock()
        storage.get_recent_images.return_value = [f'{index}.png' for index in range(30)]
        panel = QuickPanel(storage, Mock(), {})
        try:
            panel._perform_search()
            panel.show()
            self.app.processEvents()
            panel._trigger_responsive_layout(force=True)
            with patch('fluent_ui.components.emoji_card._thumbnail_pool.start') as decode:
                panel.scroll_area._mark_scrolling()
                panel._apply_lazy_loading()
                panel._load_thumbnail_chunk()
                decode.assert_not_called()
                self.assertTrue(all(card._loading_paused for card in panel._all_card_widgets))
                panel.scroll_area._settle_timer.stop()
                panel.scroll_area._finish_scrolling()
                panel._load_thumbnail_chunk()
                self.assertLessEqual(decode.call_count, 2)
                self.assertGreater(decode.call_count, 0)
        finally:
            panel.hide_panel()
            for card in panel._all_card_widgets:
                card.clear_resources()
            panel.preview_popup.deleteLater()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
