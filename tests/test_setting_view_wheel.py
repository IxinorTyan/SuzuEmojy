import unittest
from PySide6.QtWidgets import QApplication, QWidget, QScrollBar
from PySide6.QtCore import Qt, QPointF, QPoint, QCoreApplication
from PySide6.QtGui import QWheelEvent, QKeyEvent
from qfluentwidgets import RangeConfigItem, RangeValidator, SpinBox, Slider, ComboBox, FluentIcon as FIF

from services.config import ConfigService
from fluent_ui.views.setting_view import (
    SpinBoxRangeSettingCard,
    SettingInterface,
    AboutInterface,
    disable_wheel_scroll_adjustment,
)
from fluent_ui.views.exchange_view import ExchangeInterface
from fluent_ui.views.qq_scan_view import QQScanInterface
from fluent_ui.views.tg_sticker_view import TGStickerInterface


def _simulate_wheel(widget, angle_delta_y=-120):
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, angle_delta_y),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )
    event.ignore()
    widget.wheelEvent(event)
    return event.isAccepted()


class TestSettingWheelDisabled(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def test_spinbox_range_setting_card_wheel_disabled(self):
        config_item = RangeConfigItem("Test", "Val", 50, RangeValidator(10, 100))
        config_item.value = 50
        card = SpinBoxRangeSettingCard(config_item, FIF.ZOOM, "Test Title")

        self.assertEqual(card.spinBox.value(), 50)
        self.assertEqual(card.slider.value(), 50)

        accepted = _simulate_wheel(card.spinBox, angle_delta_y=-120)
        self.assertFalse(accepted, "SpinBox 不应接受滚轮事件")
        self.assertEqual(card.spinBox.value(), 50, "SpinBox 数值不应被滚轮改变")

        accepted = _simulate_wheel(card.slider, angle_delta_y=-120)
        self.assertFalse(accepted, "Slider 不应接受滚轮事件")
        self.assertEqual(card.slider.value(), 50, "Slider 数值不应被滚轮改变")

        up_event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Up, Qt.NoModifier)
        QCoreApplication.sendEvent(card.spinBox, up_event)
        self.assertEqual(card.spinBox.value(), 51, "键盘上下键应依然能调节 SpinBox 数值")

    def test_setting_interface_all_inputs_wheel_disabled(self):
        cfg = ConfigService()
        interface = SettingInterface(cfg)

        from PySide6.QtWidgets import QAbstractSpinBox, QAbstractSlider, QComboBox
        target_types = (QAbstractSpinBox, QAbstractSlider, QComboBox, SpinBox, Slider, ComboBox)

        input_widgets = [
            child for child in interface.findChildren(QWidget)
            if isinstance(child, target_types) and not isinstance(child, QScrollBar)
        ]

        self.assertGreaterEqual(len(input_widgets), 16)

        for widget in input_widgets:
            if isinstance(widget, (SpinBox, QAbstractSpinBox)):
                old_val = widget.value()
                accepted = _simulate_wheel(widget, -120)
                self.assertFalse(accepted, f"{widget} 不应接受滚轮事件")
                self.assertEqual(widget.value(), old_val, f"{widget} 数值不应改变")
            elif isinstance(widget, (Slider, QAbstractSlider)):
                old_val = widget.value()
                accepted = _simulate_wheel(widget, -120)
                self.assertFalse(accepted, f"{widget} 不应接受滚轮事件")
                self.assertEqual(widget.value(), old_val, f"{widget} 数值不应改变")
            elif isinstance(widget, (ComboBox, QComboBox)):
                old_idx = widget.currentIndex()
                accepted = _simulate_wheel(widget, -120)
                self.assertFalse(accepted, f"{widget} 不应接受滚轮事件")
                self.assertEqual(widget.currentIndex(), old_idx, f"{widget} 选项不应改变")

    def test_secondary_setting_views_wheel_disabled(self):
        qq_view = QQScanInterface()
        tg_view = TGStickerInterface()
        exchange_view = ExchangeInterface()
        about_view = AboutInterface()

        for view in [qq_view, tg_view, exchange_view, about_view]:
            from PySide6.QtWidgets import QAbstractSpinBox, QAbstractSlider, QComboBox
            target_types = (QAbstractSpinBox, QAbstractSlider, QComboBox, SpinBox, Slider, ComboBox)

            for child in view.findChildren(QWidget):
                if isinstance(child, target_types) and not isinstance(child, QScrollBar):
                    accepted = _simulate_wheel(child, -120)
                    self.assertFalse(accepted, f"{type(view).__name__} 中的 {type(child).__name__} 不应接受滚轮事件")


if __name__ == "__main__":
    unittest.main()
