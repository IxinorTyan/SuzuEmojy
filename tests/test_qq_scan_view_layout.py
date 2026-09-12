import unittest
import sys
from PySide6.QtWidgets import QApplication
from fluent_ui.views.qq_scan_view import QQScanInterface, LogDialog


class TestQQScanViewLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_qq_scan_interface_layout_and_toggle(self):
        w = QQScanInterface()
        w.show()

        # 验证初始状态：配置卡片展开，摘要栏隐藏
        self.assertFalse(w.configCard.isHidden())
        self.assertTrue(w.summaryRow.isHidden())

        # 验证折叠行为
        w.collapse_config(animated=False)
        self.assertTrue(w.configCard.isHidden())
        self.assertFalse(w.summaryRow.isHidden())

        # 验证再次展开
        w.expand_config(animated=False)
        self.assertFalse(w.configCard.isHidden())
        self.assertTrue(w.summaryRow.isHidden())

        # 验证按钮存在性
        self.assertTrue(hasattr(w, 'exportSelectedButton'))
        self.assertTrue(hasattr(w, 'exportAllButton'))
        self.assertTrue(hasattr(w, 'importSelectedButton'))
        self.assertTrue(hasattr(w, 'importAllButton'))
        self.assertTrue(hasattr(w, 'selectAllButton'))
        self.assertTrue(hasattr(w, 'clearSelectionButton'))
        self.assertTrue(hasattr(w, 'btnLog'))

        # 验证 StateToolTip 气泡管理器
        self.assertTrue(hasattr(w, 'tooltip'))
        w.tooltip.show("测试", "正在进行...")
        self.assertTrue(w.tooltip.is_active())
        w.tooltip.update("处理中 50%")
        # 验证切换分类不会自动触发扫描
        w.emojiFolderComboBox.addItem("测试分类", userData="test")
        w.emojiFolderComboBox.setCurrentIndex(w.emojiFolderComboBox.count() - 1)
        self.assertEqual(w.previewListWidget.count(), 0)
        self.assertFalse(w.configCard.isHidden())

        w.close()

    def test_log_dialog(self):
        log_records = ["日志条目 1", "日志条目 2"]
        dialog = LogDialog(log_records)
        self.assertIn("日志条目 1", dialog.logTextEdit.toPlainText())
        self.assertIn("日志条目 2", dialog.logTextEdit.toPlainText())

        # 测试清空
        dialog._on_clear()
        self.assertEqual(dialog.logTextEdit.toPlainText(), "")


if __name__ == '__main__':
    unittest.main()
