import sys
import os
import glob
import warnings


def _lock_ffmpeg_path_for_frozen_app():
    """在 Nuitka/PyInstaller 冻结环境中锁定随程序发布的 FFmpeg。"""
    is_frozen = bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))
    if not is_frozen:
        return

    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    pattern = os.path.join(
        executable_dir,
        "imageio_ffmpeg",
        "binaries",
        "ffmpeg*.exe",
    )
    ffmpeg_candidates = sorted(glob.glob(pattern))
    if ffmpeg_candidates:
        os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg_candidates[0]
        print(f"[INFO] Locked FFmpeg executable: {ffmpeg_candidates[0]}")
    else:
        print(f"[WARNING] Bundled FFmpeg executable not found under: {os.path.dirname(pattern)}")


_lock_ffmpeg_path_for_frozen_app()

# 忽略 requests 与 urllib3 版本轻微不兼容产生的非致命警告
try:
    from requests.exceptions import RequestsDependencyWarning
    warnings.filterwarnings("ignore", category=RequestsDependencyWarning)
except ImportError:
    pass

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
    
    app_font = app.font()
    if app_font.pointSize() <= 0:
        app_font.setPointSize(9)
        app.setFont(app_font)

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
    # 2. 初始化存储服务（轻量，不做全量扫描）
    # ----------------------------------------------------
    storage_service = StorageService()
    clipboard_service = ClipboardService(config_service)

    # 启动旧图库后台异步补全 sync_key 索引迁移
    try:
        from services.sync_migration import SyncKeyMigrationManager
        sync_migration_mgr = SyncKeyMigrationManager(storage_service)
        sync_migration_mgr.start_async_migration()
    except Exception as e:
        print(f"[WARNING] 启动后台 sync_key 迁移失败: {e}")

    # ----------------------------------------------------
    # 3. 重型维护任务放到后台守护线程，不阻塞 UI 显示
    #    - cleanup_dead_links：清除失效文件的残留记录
    #    - run_full_deduplication：全库哈希补全 + 去重索引
    # ----------------------------------------------------
    import threading
    from services.deduplication_pipeline import DeduplicationPipeline

    def _background_startup_tasks():
        try:
            storage_service.cleanup_dead_links()
            print("[INFO] 后台死链自愈完成")
        except Exception as e:
            print(f"[ERROR] 后台死链自愈失败: {e}")
        try:
            dedup_pipeline = DeduplicationPipeline()
            dedup_pipeline.run_full_deduplication()
            print("[INFO] 后台去重索引建立完成")
        except Exception as e:
            print(f"[ERROR] 后台去重索引失败: {e}")

    _bg_thread = threading.Thread(target=_background_startup_tasks, daemon=True, name="startup-maintenance")
    _bg_thread.start()
    
    # 初始化多语言引擎
    i18n_engine.init(config_service)
    
    # 外观模式使用 appearance_mode；兼容旧版本保存的 theme_mode
    theme_mode = config_service.get(
        "appearance_mode",
        config_service.get("theme_mode", "system"),
    )
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
    icon_path = os.path.join(icon_dir, "ico.ico")
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
