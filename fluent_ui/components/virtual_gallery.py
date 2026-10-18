"""Bounded card population, with all loading work deferred until scrolling settles."""

from time import perf_counter

from PySide6.QtCore import QObject, QSignalBlocker, QTimer

from fluent_ui.components.emoji_card import EmojiCard


class VirtualGallery(QObject):
    WORK_BUDGET_SECONDS = 0.002
    MAX_CARDS_PER_TICK = 4
    MAX_PENDING_DECODES = 2

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.cards = {}
        self._missing = []
        self._retired = []
        self._thumbnails = []
        self.render_timer = QTimer(self)
        self.render_timer.setInterval(16)
        self.render_timer.timeout.connect(self.render_chunk)
        view.scroll_area.scrollingChanged.connect(self._scrolling_changed)

    def _scrolling_changed(self, scrolling):
        if scrolling:
            self.render_timer.stop()
            self.view._lazy_load_timer.stop()
            self.view._is_loading = False
            for card in self.cards.values():
                card.set_loading_paused(True)
        elif self.view.isVisible():
            self.refresh()

    def refresh_geometry(self):
        view = self.view
        size = view.config.get('thumbnail_size', 120)
        columns = max(1, (view.scroll_area.viewport().width() - 32) // (size + view.LAYOUT_SPACING))
        view._current_columns = columns
        view.gallery_layout.configure(len(view._all_current_images), columns, size)
        view.gallery_layout.activate()

    def index_range(self, padding=0):
        view = self.view
        size = view.config.get('thumbnail_size', 120)
        stride = size + view.LAYOUT_SPACING
        columns = max(1, view._current_columns)
        height = max(1, view.scroll_area.viewport().height())
        top = view.scroll_area.verticalScrollBar().value() - padding * height
        bottom = top + (2 * padding + 1) * height
        first_row = max(0, (top - view.LAYOUT_TOP_MARGIN - size + stride - 1) // stride)
        last_row = max(first_row, (bottom - view.LAYOUT_TOP_MARGIN) // stride + 1)
        count = len(view._all_current_images)
        return min(count, first_row * columns), min(count, last_row * columns)

    def _ordered_indices(self):
        first, last = self.index_range(1)
        visible_first, visible_last = self.index_range()
        return list(range(visible_first, visible_last)) + list(range(visible_last, last)) + list(range(first, visible_first))

    def refresh(self):
        self.refresh_geometry()
        if self.view.scroll_area.is_scrolling or not self.view.isVisible():
            return
        ordered = self._ordered_indices()
        desired = set(ordered)
        self._retired = [index for index in self.cards if index not in desired]
        self._missing = [index for index in ordered if index not in self.cards]
        self._thumbnails = []
        self.view._is_loading = bool(self._missing or self._retired)
        if self.view._is_loading:
            self.render_timer.start()
        self.schedule_thumbnails()

    def _create_card(self, path):
        view = self.view
        card = EmojiCard(path, size=view.config.get('thumbnail_size', 120), load_image=False)
        card.customContextMenuRequested.connect(lambda pos, w=card: view.show_context_menu(w, pos))
        card.clicked.connect(view.on_image_clicked)
        card.delete_requested.connect(view.on_delete_requested)
        card.hover_started.connect(lambda path, w=card: view.on_hover_started(w, path))
        card.hover_ended.connect(lambda w=card: view.on_hover_ended(w))
        card.selection_changed.connect(view.on_selection_changed)
        return card

    def render_chunk(self):
        view = self.view
        if view.scroll_area.is_scrolling or not view.isVisible():
            self.render_timer.stop()
            return
        started = perf_counter()
        for _ in range(self.MAX_CARDS_PER_TICK):
            if self._missing:
                index = self._missing.pop(0)
                path = view._all_current_images[index]
                if self._retired:
                    card = self.cards.pop(self._retired.pop())
                    view.gallery_layout.removeWidget(card)
                    card.clear_resources()
                    card.image_path = path
                    card.update_size(view.config.get('thumbnail_size', 120), load_image=False)
                else:
                    card = self._create_card(path)
                card.set_loading_paused(False)
                with QSignalBlocker(card):
                    card.set_selectable(view.is_selection_mode)
                    card.set_selected(path in view.selected_paths)
                self.cards[index] = card
                view.gallery_layout.addWidget(card, index // view._current_columns, index % view._current_columns)
                card.show()
            elif self._retired:
                card = self.cards.pop(self._retired.pop())
                view.gallery_layout.removeWidget(card)
                card.clear_resources()
                card.hide()
                card.deleteLater()
            else:
                break
            if perf_counter() - started >= self.WORK_BUDGET_SECONDS:
                break
        view._all_card_widgets = [card for _, card in sorted(self.cards.items())]
        view._loaded_count = len(self.cards)
        view._active_thumbnail_widgets = set(self.cards.values())
        view.gallery_layout.activate()
        # Pipeline decoding with population: visible images must not wait for
        # all the off-screen placeholder cards to be constructed first.
        self.schedule_thumbnails()
        if not self._missing and not self._retired:
            self.render_timer.stop()
            view._is_loading = False

    def schedule_thumbnails(self):
        if self.view.scroll_area.is_scrolling:
            return
        size = self.view.config.get('thumbnail_size', 120)
        self._thumbnails = [index for index in self._ordered_indices() if index in self.cards and (
            self.cards[index]._pending_thumbnail_result is not None
            or (self.cards[index]._loading_paused and self.cards[index]._is_loading)
            or self.cards[index].needs_reload(size)
        )]
        self._thumbnails.sort(key=lambda index: self.cards[index]._pending_thumbnail_result is None)
        if self._thumbnails and not self.view._lazy_load_timer.isActive():
            self.view._lazy_load_timer.start(8)

    def load_thumbnails(self):
        view = self.view
        if view.scroll_area.is_scrolling or not view.isVisible():
            return
        started = perf_counter()
        for _ in range(self.MAX_CARDS_PER_TICK):
            if not self._thumbnails:
                break
            index = self._thumbnails.pop(0)
            card = self.cards.get(index)
            if card is not None:
                card.set_loading_paused(False)
                if card.needs_reload(view.config.get('thumbnail_size', 120)):
                    if sum(c._is_loading for c in self.cards.values()) >= self.MAX_PENDING_DECODES:
                        self._thumbnails.insert(0, index)
                        break
                    card.update_size(view.config.get('thumbnail_size', 120))
            if perf_counter() - started >= self.WORK_BUDGET_SECONDS:
                break
        if self._thumbnails:
            view._lazy_load_timer.start(8)

    def clear(self):
        self.render_timer.stop()
        self.view._lazy_load_timer.stop()
        self._missing.clear()
        self._retired.clear()
        self._thumbnails.clear()
        self.cards.clear()
        self.view._is_loading = False

    def suspend(self):
        self.render_timer.stop()
        self.view._lazy_load_timer.stop()
        self.view._is_loading = False
        for card in self.cards.values():
            card.set_loading_paused(True)
