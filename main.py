import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    
    from PySide6.QtCore import QSharedMemory
    shared_memory = QSharedMemory("SuzuEmojy_App_Instance")
    if not shared_memory.create(1):
        sys.exit(0)
    
    from services.config import ConfigService
    config_service = ConfigService()
    
    if config_service.get("use_system_font", False):
        try:
            system_font = app.font().family()
            if system_font:
                from qfluentwidgets import setFontFamilies
                setFontFamilies([system_font])
        except Exception as e:
            print(f"Failed to set system font: {e}")
            
    from PySide6.QtWidgets import QSystemTrayIcon
    from PySide6.QtGui import QIcon
    from qfluentwidgets import setTheme, Theme, RoundMenu, Action
    from services.storage import StorageService
    from services.clipboard import ClipboardService
    from services.i18n import i18n_engine, t
    
    app.setQuitOnLastWindowClosed(False)
    
    from PySide6.QtWidgets import QProgressDialog
    from services.migration_manager import MigrationManager
    from services.deduplication_pipeline import DeduplicationPipeline
    from fluent_ui.main_window import MainWindow

    # ----------------------------------------------------
    # 1. 启动阶段：检查并执行老数据迁移至 SQLite 数据库
    # ----------------------------------------------------
    migration_mgr = MigrationManager()
    if migration_mgr.needs_migration():
        progress_dialog = QProgressDialog("正在升级数据引擎到 SQLite...", None, 0, 100)
        progress_dialog.setWindowModality(Qt.ApplicationModal)
        progress_dialog.setCancelButton(None)  # 禁止中途取消
        progress_dialog.setWindowTitle("数据升级中")
        progress_dialog.show()
        app.processEvents()  # 刷新界面显示

        def update_progress(msg, percent):
            progress_dialog.setLabelText(f"正在升级数据: {msg}")
            progress_dialog.setValue(int(percent))
            app.processEvents()  # 确保 UI 实时更新不假死

        migration_mgr.run_migration(progress_callback=update_progress)
        progress_dialog.close()

    # ----------------------------------------------------
    # 2. 检查并建立全库表情包特征索引
    # ----------------------------------------------------
    dedup_pipeline = DeduplicationPipeline()
    dedup_pipeline.run_full_deduplication()

    storage_service = StorageService()
    clipboard_service = ClipboardService()
    
    # 初始化多语言引擎
    i18n_engine.init(config_service)
    
    theme_mode = config_service.get("theme_mode", "system")
    if theme_mode == "dark":
        setTheme(Theme.DARK)
    elif theme_mode == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.AUTO)

    window = MainWindow(storage_service, clipboard_service, config_service)
    
    tray_icon = QSystemTrayIcon()
    if getattr(sys, 'frozen', False):
        icon_dir = sys._MEIPASS
    else:
        icon_dir = os.path.dirname(__file__)
    icon_path = os.path.join(icon_dir, "hi.ico")
    if os.path.exists(icon_path):
        tray_icon.setIcon(QIcon(icon_path))
        window.setWindowIcon(QIcon(icon_path))
    else:
        pass
    
    tray_icon.setToolTip("SuzuEmojy")
    
    tray_menu = RoundMenu()
    
    show_action = Action(t("显示主面板"), triggered=window.show_gallery)
    tray_menu.addAction(show_action)
    
    def open_settings():
        window.show_settings()
        
    settings_action = Action(t("设置"), triggered=open_settings)
    tray_menu.addAction(settings_action)
    
    tray_menu.addSeparator()
    
    quit_action = Action(t("退出"), triggered=app.quit)
    tray_menu.addAction(quit_action)
    
    # 动态刷新托盘菜单文案
    def update_tray_texts(lang):
        show_action.setText(t("显示主面板"))
        settings_action.setText(t("设置"))
        quit_action.setText(t("退出"))
    i18n_engine.language_changed.connect(update_tray_texts)
    
    def on_tray_activated(reason):
        if reason == QSystemTrayIcon.Trigger or reason == QSystemTrayIcon.DoubleClick:
            window.show_gallery()
        elif reason == QSystemTrayIcon.Context:
            from PySide6.QtGui import QCursor
            tray_menu.exec(QCursor.pos())
            
    tray_icon.activated.connect(on_tray_activated)
    tray_icon.show()
    
    window.show()
    
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
