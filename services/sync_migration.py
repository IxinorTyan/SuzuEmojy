import os
import time
import sqlite3
import threading
import logging

logger = logging.getLogger(__name__)

class SyncKeyMigrationManager:
    """
    后台轻量异步迁移服务：
    为升级前旧图库中的历史图片补齐 sync_key、file_size 和 mtime_ns，
    使应用在启动后快速完成数据持久化，彻底避免首次粘贴时全量阻塞。
    """
    def __init__(self, storage_service):
        self.storage = storage_service
        self._thread = None
        self._stop_event = threading.Event()

    def start_async_migration(self):
        """启动低优先级后台迁移线程"""
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(target=self._run_migration, name="SyncKeyMigrationThread", daemon=True)
        self._thread.start()

    def stop(self):
        """通知停止迁移任务"""
        self._stop_event.set()

    def _run_migration(self):
        try:
            if not os.path.exists(self.storage.images_dir):
                return

            from services.hasher import compute_sync_key

            # 1. 查找数据库中已存在的有效记录
            db_synced = set()
            try:
                with sqlite3.connect(self.storage.features_db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT image_path FROM image_features WHERE sync_key IS NOT NULL")
                    for row in cursor.fetchall():
                        db_synced.add(row[0])
            except Exception as e:
                logger.warning(f"[SyncKeyMigration] 读取已同步记录失败: {e}")

            # 2. 扫描磁盘上所有受支持的表情文件
            all_files = sorted(os.listdir(self.storage.images_dir))
            pending_files = [
                fn for fn in all_files
                if fn.lower().endswith(self.storage.SUPPORTED_FORMATS) and fn not in db_synced
            ]

            if not pending_files:
                return

            logger.info(f"[SyncKeyMigration] 检测到 {len(pending_files)} 张历史图片需要后台补全 sync_key 索引")

            # 3. 分批计算并回填数据库，每批处理后让出 CPU 线程
            batch_size = 30
            batch_records = []

            for idx, fn in enumerate(pending_files):
                if self._stop_event.is_set():
                    break

                abspath = self.storage._to_abspath(fn)
                if not os.path.exists(abspath):
                    continue

                try:
                    stat_res = os.stat(abspath)
                    f_size = stat_res.st_size
                    f_mtime = getattr(stat_res, 'st_mtime_ns', int(stat_res.st_mtime * 1e9))
                    skey = compute_sync_key(abspath)
                    if skey:
                        batch_records.append((fn, skey, f_size, f_mtime))
                except Exception:
                    pass

                # 达到批次大小或遍历结束，批量写入一次
                if len(batch_records) >= batch_size or idx == len(pending_files) - 1:
                    if batch_records:
                        try:
                            with sqlite3.connect(self.storage.features_db_path) as conn:
                                cursor = conn.cursor()
                                cursor.executemany("""
                                    INSERT INTO image_features (image_path, sync_key, file_size, mtime_ns)
                                    VALUES (?, ?, ?, ?)
                                    ON CONFLICT(image_path) DO UPDATE SET
                                        sync_key = excluded.sync_key,
                                        file_size = excluded.file_size,
                                        mtime_ns = excluded.mtime_ns
                                """, batch_records)
                                conn.commit()
                        except Exception as e:
                            logger.warning(f"[SyncKeyMigration] 批量写入迁移数据失败: {e}")
                        batch_records.clear()

                    # 让出时间片，降低对前台主线程的 I/O 争用
                    time.sleep(0.02)

            logger.info("[SyncKeyMigration] 历史图片 sync_key 索引迁移完成")

        except Exception as e:
            logger.error(f"[SyncKeyMigration] 迁移过程发生异常: {e}")
