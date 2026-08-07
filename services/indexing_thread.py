import sys
import logging
from typing import Optional, Dict, Any
from PySide6.QtCore import QThread, Signal

from services.deduplicator import Deduplicator
from services.feature_db import FeatureDB

logger = logging.getLogger(__name__)


class IndexingThread(QThread):
    """
    后台异步哈希与画质补全线程:
    在独立后台 QThread 线程中扫描图片目录，为数据库中缺失或不完整的感知哈希(pHash/dHash)进行自动补全计算，
    并实时通过 PySide6 信号 (progress_signal / finished_signal) 传回处理进度与统计结果，
    保证计算过程完全非阻塞，不卡顿 PySide6 GUI 主界面渲染。
    """

    # 1. 进度信号: 传递 (current, total) 当前已处理数量与总文件数
    progress_signal = Signal(int, int)

    # 2. 完成信号: 传递扫描统计结果字典 {"total": ..., "new_indexed": ..., "failed": ...}
    finished_signal = Signal(dict)

    def __init__(
        self,
        image_dir: str = "data/images",
        feature_db: Optional[FeatureDB] = None,
        parent=None
    ):
        super().__init__(parent)
        self.image_dir = image_dir
        self.feature_db = feature_db

    def run(self):
        """
        后台线程核心执行逻辑:
        实例化 Deduplicator 并触发全库/增量感知哈希与特征索引补全计算。
        """
        try:
            # 实例化去重与索引服务
            deduplicator = Deduplicator(feature_db=self.feature_db)

            # 定义进度回调，向 GUI 主线程发射 progress_signal 信号
            def on_progress(current: int, total: int):
                self.progress_signal.emit(current, total)

            # 执行增量扫描与哈希特征计算
            stats = deduplicator.scan_and_index_images(
                image_dir=self.image_dir,
                progress_callback=on_progress
            )

            # 计算完成，发射统计结果信号
            self.finished_signal.emit(stats)
            logger.info(f"[IndexingThread] 后台索引与补全计算完成: {stats}")

        except Exception as e:
            logger.error(f"[IndexingThread] 后台索引线程发生未捕获异常: {e}")
            err_stats = {"total": 0, "new_indexed": 0, "failed": 0, "error": str(e)}
            self.finished_signal.emit(err_stats)


if __name__ == '__main__':
    from PySide6.QtCore import QCoreApplication

    print("=== IndexingThread 后台异步哈希与画质补全线程测试 ===")
    app = QCoreApplication(sys.argv)

    # 实例化索引后台线程
    indexing_thread = IndexingThread(image_dir="data/images")

    # 绑定进度和完成信号
    def on_indexing_progress(current: int, total: int):
        percentage = (current / total * 100) if total > 0 else 100.0
        print(f"[后台进度] 正在计算索引: {current}/{total} ({percentage:.1f}%)")

    def on_indexing_finished(stats: Dict[str, Any]):
        print(f"[后台完成] 索引与画质补全完成，统计数据: {stats}")
        app.quit()

    indexing_thread.progress_signal.connect(on_indexing_progress)
    indexing_thread.finished_signal.connect(on_indexing_finished)

    # 启动后台线程（非阻塞主线程）
    print("正在启动后台 QThread 线程...")
    indexing_thread.start()

    sys.exit(app.exec())
