import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap, QColor
from fluent_ui.views.about_view import (
    AboutInterface,
    CircularAvatarWidget,
    PillBadge,
    FeatureGridCard,
    AvatarLoader
)
from services.i18n import i18n_engine


class TestAboutView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self):
        self.about = AboutInterface()

    def tearDown(self):
        if hasattr(self.about, "avatar_thread") and self.about.avatar_thread.isRunning():
            self.about.avatar_thread.wait(1000)
        self.about.deleteLater()

    def test_ui_components_exist(self):
        """验证所有关于页核心组件与卡片均已正确构建。"""
        self.assertIsNotNone(self.about.btnBack)
        self.assertIsNotNone(self.about.titleLabel)
        self.assertIsNotNone(self.about.heroCard)
        self.assertIsNotNone(self.about.logoLabel)
        self.assertIsNotNone(self.about.appNameLabel)
        self.assertIsNotNone(self.about.badgeVersion)
        self.assertIsNotNone(self.about.btnRepo)
        self.assertIsNotNone(self.about.btnReleases)
        
        # 开发者个人介绍卡片
        self.assertIsNotNone(self.about.authorCard)
        self.assertIsNotNone(self.about.avatarWidget)
        self.assertIsNotNone(self.about.authorNameLabel)
        self.assertEqual(self.about.authorNameLabel.text(), "IxinorTyan")
        self.assertIsNotNone(self.about.authorBioLabel)
        self.assertIsNotNone(self.about.btnAuthorGithub)
        self.assertIsNotNone(self.about.btnCopyQQ)
        self.assertIsNotNone(self.about.btnCopyAction)

        # 核心特性矩阵
        self.assertIsNotNone(self.about.featureGroup)
        self.assertEqual(len([self.about.feat1, self.about.feat2, self.about.feat3,
                              self.about.feat4, self.about.feat5, self.about.feat6]), 6)

        # 吉祥物与致谢
        self.assertIsNotNone(self.about.mascotCard)
        self.assertIsNotNone(self.about.creditsCard)
        self.assertIsNotNone(self.about.licenseCard)

    def test_copy_qq_group(self):
        """验证一键复制 QQ 群号功能。"""
        clipboard = QApplication.clipboard()
        clipboard.clear()
        self.about._copy_qq_group()
        self.assertEqual(clipboard.text(), "834586488")

    def test_circular_avatar_widget(self):
        """验证头像组件绘制与图片设置。"""
        avatar = CircularAvatarWidget(size=64)
        self.assertEqual(avatar.width(), 64)
        self.assertEqual(avatar.height(), 64)

        # 绘制默认 fallback 状态
        avatar.repaint()

        # 设置测试 Pixmap 并绘制
        pix = QPixmap(100, 100)
        pix.fill(QColor(255, 0, 0))
        avatar.set_pixmap(pix)
        avatar.repaint()
        self.assertIsNotNone(avatar._pixmap)

    def test_pill_badge_styling(self):
        """验证徽章组件主题样式更新。"""
        badge_accent = PillBadge("v1.2.0", is_accent=True)
        badge_normal = PillBadge("GPL-3.0", is_accent=False)
        badge_accent.update_style()
        badge_normal.update_style()
        self.assertEqual(badge_accent.text(), "v1.2.0")
        self.assertEqual(badge_normal.text(), "GPL-3.0")

    def test_language_switch_update_texts(self):
        """验证多语言切换时所有文本均能正确响应。"""
        for lang in ["zh", "en", "ja", "zh_TW"]:
            i18n_engine.set_language(lang)
            self.about.update_texts(lang)
            # 验证主要文本非空
            self.assertTrue(len(self.about.titleLabel.text()) > 0)
            self.assertTrue(len(self.about.appSloganLabel.text()) > 0)
            self.assertTrue(len(self.about.authorBioLabel.text()) > 0)
            self.assertTrue(len(self.about.mascotDesc.text()) > 0)

        # 还原回中文
        i18n_engine.set_language("zh")
        self.about.update_texts("zh")

    def test_back_signal(self):
        """验证返回按钮信号正常触发。"""
        signal_received = []
        self.about.back_requested.connect(lambda: signal_received.append(True))
        self.about.btnBack.click()
        self.assertTrue(signal_received[0])

    def test_card_heights_not_collapsed(self):
        """验证所有卡片高度均已自适应展开，无高度塌陷或重叠错位。"""
        self.about.resize(860, 640)
        self.about.show()
        for _ in range(10):
            QApplication.processEvents()

        self.assertGreater(self.about.heroCard.height(), 100)
        self.assertGreater(self.about.authorCard.height(), 120)
        self.assertGreater(self.about.featureGridWidget.height(), 200)
        self.assertGreater(self.about.feat1.height(), 50)
        self.assertGreater(self.about.mascotCard.height(), 60)
        self.assertGreater(self.about.creditsCard.height(), 150)
        self.assertGreater(self.about.licenseCard.height(), 100)


if __name__ == "__main__":
    unittest.main()
