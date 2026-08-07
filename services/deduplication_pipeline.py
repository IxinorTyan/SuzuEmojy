import os
import logging
from typing import List, Dict, Any, Optional, Callable

from services.feature_db import FeatureDB
from services.deduplicator import Deduplicator
from services.quality_evaluator import evaluate_quality, rank_duplicate_group
from services.indexing_thread import IndexingThread

logger = logging.getLogger(__name__)


class DeduplicationPipeline:
    """
    去重业务总门面 (Facade) 类:
    将数据库 (FeatureDB)、去重引擎 (Deduplicator)、画质评估器 (QualityEvaluator) 和后台处理线程 (IndexingThread)
    组装成一个供 UI 直接调用的一站式服务接口。
    """

    def __init__(self, feature_db: Optional[FeatureDB] = None):
        # 1. 持有数据库与业务服务引用
        if feature_db is None:
            self.db = FeatureDB()
        else:
            self.db = feature_db

        self.deduplicator = Deduplicator(feature_db=self.db)
        self.evaluate_quality = evaluate_quality
        self.rank_duplicate_group = rank_duplicate_group
        self._indexing_thread: Optional[IndexingThread] = None

    def start_background_indexing(
        self,
        image_dir: str = "data/images",
        progress_callback: Optional[Callable[[int, int], None]] = None,
        finished_callback: Optional[Callable[[dict], None]] = None
    ) -> IndexingThread:
        """
        后台异步哈希与特征补全启动接口
        """
        if self._indexing_thread and self._indexing_thread.isRunning():
            logger.warning("[DeduplicationPipeline] 后台补全线程已在运行中")
            return self._indexing_thread

        self._indexing_thread = IndexingThread(image_dir=image_dir, feature_db=self.db)
        if progress_callback:
            self._indexing_thread.progress_signal.connect(progress_callback)
        if finished_callback:
            self._indexing_thread.finished_signal.connect(finished_callback)

        self._indexing_thread.start()
        return self._indexing_thread

    def run_full_deduplication(
        self,
        threshold: int = 5,
        image_dir: str = "data/images",
        hash_type: str = "phash"
    ) -> List[Dict[str, Any]]:
        """
        2. 方法 run_full_deduplication(threshold=5)：
        - 调用 deduplicator.scan_and_index_images 增量补全索引。
        - 查找所有重复组 (deduplicator.find_duplicate_groups)。
        - 遍历每个重复组，调用 quality_evaluator.rank_duplicate_group 进行画质打分和择优。
        - 返回供 UI 渲染的结构化报告列表。
        """
        # A. 建立/更新全库图片哈希索引
        stats = self.deduplicator.scan_and_index_images(image_dir=image_dir)
        logger.info(f"[DeduplicationPipeline] 扫描并建立索引完成: {stats}")

        # B. 查找所有重复/相似分组
        groups = self.deduplicator.find_duplicate_groups(threshold=threshold, hash_type=hash_type)

        reports = []
        for idx, group_paths in enumerate(groups, 1):
            if not group_paths or len(group_paths) < 2:
                continue

            full_paths = []
            for p in group_paths:
                if not os.path.isabs(p):
                    full_paths.append(os.path.join(self.db.base_dir, image_dir, p))
                else:
                    full_paths.append(p)

            # C. 组内画质评估与择优排序
            rank_result = self.rank_duplicate_group(full_paths)

            details = []
            for item in rank_result.get("details", []):
                p = item["path"]
                rel_path = os.path.basename(p)
                w, h = item.get("resolution", (0, 0))
                details.append({
                    "path": p,
                    "rel_path": rel_path,
                    "score": item.get("score", 0.0),
                    "resolution": f"{w}x{h}",
                    "format": item.get("format", ""),
                    "reason": item.get("reason", "")
                })

            rec_keep = rank_result.get("recommended_keep", "")
            cand_del = rank_result.get("candidates_to_delete", [])

            reports.append({
                "group_id": idx,
                "recommended_keep": rec_keep,
                "candidates_to_delete": cand_del,
                "details": details
            })

        return reports

    def check_new_image_before_save(
        self,
        image_path: str,
        threshold: int = 5
    ) -> Dict[str, Any]:
        """
        3. 方法 check_new_image_before_save(image_path)：
        - 保存新图前的实时重复拦截。
        - 比对已有图，若存在重复，比较品质得分并返回 REPLACE / SKIP / SAVE 建议。
        """
        similar_rel_paths = self.deduplicator.find_similar_for_image(image_path, threshold=threshold)
        if not similar_rel_paths:
            return {
                "action": "SAVE",
                "message": "无重复，可正常保存"
            }

        # 评估新图品质得分
        new_eval = self.evaluate_quality(image_path)
        new_score = new_eval.get("score", 0.0) if new_eval else 0.0

        # 获取数据库中相似图的品质得分
        best_existing_rel_path = similar_rel_paths[0]
        best_existing_abs_path = os.path.join(self.db.data_dir, "images", best_existing_rel_path)
        if not os.path.exists(best_existing_abs_path):
            best_existing_abs_path = best_existing_rel_path

        existing_eval = self.evaluate_quality(best_existing_abs_path)
        existing_score = existing_eval.get("score", 0.0) if existing_eval else 0.0

        # 新图比已有图清晰度明显更高时，建议覆盖替换
        if new_score > existing_score + 5.0:
            return {
                "action": "REPLACE",
                "existing_path": best_existing_abs_path,
                "new_score": new_score,
                "existing_score": existing_score,
                "message": f"发现更高清版本（得分 {new_score} vs {existing_score}），建议覆盖替换"
            }
        else:
            return {
                "action": "SKIP",
                "existing_path": best_existing_abs_path,
                "new_score": new_score,
                "existing_score": existing_score,
                "message": f"库中已存在相同且更高清的表情包（得分 {existing_score} vs {new_score}），无需重复保存"
            }

    def apply_cleanup(self, delete_paths: List[str]) -> Dict[str, int]:
        """
        4. 方法 apply_cleanup(delete_paths)：
        - 传入用户勾选需要清理的旧图路径列表，安全删除本地图片文件并清除 SQLite 记录。
        """
        stats = {"success": 0, "failed": 0}

        for path in delete_paths:
            rel_filename = os.path.basename(path)
            try:
                # 1. 删除物理文件
                if os.path.exists(path):
                    os.remove(path)

                # 2. 清除数据库特征记录
                self.db.delete_feature(rel_filename)
                self.db.delete_feature(path)

                stats["success"] += 1
                logger.info(f"[DeduplicationPipeline] 成功清理重复图片: {path}")
            except Exception as e:
                stats["failed"] += 1
                logger.error(f"[DeduplicationPipeline] 清理图片失败 ({path}): {e}")

        return stats

    def apply_deduplication_actions(self, delete_paths: List[str]) -> Dict[str, int]:
        """兼容别名方法"""
        return self.apply_cleanup(delete_paths)


if __name__ == '__main__':
    print("=== DeduplicationPipeline 业务总门面功能测试 ===")
    pipeline = DeduplicationPipeline()

    print("1. 正在运行一键全库去重与画质评估流程...")
    reports = pipeline.run_full_deduplication(threshold=5)

    print(f"\n共检测到 {len(reports)} 组重复表情包报告:\n")
    for r in reports:
        print(f"=== [重复分组 {r['group_id']}] ===")
        print(f"  推荐保留: {os.path.basename(r['recommended_keep'])}")
        print(f"  建议删除: {[os.path.basename(p) for p in r['candidates_to_delete']]}")
        print("  明细:")
        for d in r["details"]:
            print(f"    - {d['rel_path']} | 分数: {d['score']} | 分辨率: {d['resolution']} | 原因: {d['reason']}")
        print()
