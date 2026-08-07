import os
import sys
from typing import List, Dict, Any, Optional, Callable

from services.feature_db import FeatureDB
from services.hasher import extract_all_hashes, calculate_hamming_distance, is_similar


class UnionFind:
    """并查集数据结构，用于高效合并相似图片分组"""

    def __init__(self):
        self.parent = {}

    def find(self, item: str) -> str:
        if item not in self.parent:
            self.parent[item] = item
            return item
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])  # 路径压缩
        return self.parent[item]

    def union(self, item1: str, item2: str):
        root1 = self.find(item1)
        root2 = self.find(item2)
        if root1 != root2:
            self.parent[root1] = root2

    def get_groups(self) -> List[List[str]]:
        """获取包含 2 个及以上元素的聚类分组"""
        groups = {}
        for item in list(self.parent.keys()):
            root = self.find(item)
            if root not in groups:
                groups[root] = []
            groups[root].append(item)
        
        # 仅返回包含重复/相似图片（元素个数 >= 2）的分组
        return [members for members in groups.values() if len(members) >= 2]


class Deduplicator:
    """表情包去重核心服务：关联数据库与感知哈希提取器，提供索引构建与分组比对功能"""

    SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')

    def __init__(self, feature_db: Optional[FeatureDB] = None):
        if feature_db is None:
            self.db = FeatureDB()
        else:
            self.db = feature_db

    def scan_and_index_images(
        self,
        image_dir: str = "data/images",
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Dict[str, int]:
        """
        全库/增量图片索引建立:
        遍历 image_dir 下所有图片，若数据库内缺失或不完整则计算哈希并保存。
        :param image_dir: 图片存储目录
        :param progress_callback: 进度回调函数 progress_callback(current, total)
        :return: {"total": 总图数, "new_indexed": 新建立索引数, "failed": 失败数}
        """
        if not os.path.isabs(image_dir):
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
            abs_image_dir = os.path.join(base_dir, image_dir)
        else:
            abs_image_dir = image_dir

        stats = {"total": 0, "new_indexed": 0, "failed": 0}

        if not os.path.exists(abs_image_dir):
            return stats

        image_files = [
            f for f in os.listdir(abs_image_dir)
            if f.lower().endswith(self.SUPPORTED_EXTENSIONS)
        ]
        total_files = len(image_files)
        stats["total"] = total_files

        for idx, filename in enumerate(image_files, 1):
            if progress_callback:
                try:
                    progress_callback(idx, total_files)
                except Exception:
                    pass

            file_path = os.path.join(abs_image_dir, filename)
            # 使用相对文件名作为数据库记录的主键 (保持与项目原有规范一致)
            rel_path = filename

            # 1. 查询数据库记录
            existing = self.db.get_feature(rel_path)
            # 若记录存在且 pHash / dHash 均不为空，跳过（增量扫描）
            if existing and existing.get("phash") and existing.get("dhash"):
                continue

            # 2. 计算缺失的哈希特征
            hashes = extract_all_hashes(file_path)
            if hashes:
                success = self.db.save_feature(
                    image_path=rel_path,
                    md5=hashes["md5"],
                    dhash=hashes["dhash"],
                    phash=hashes["phash"]
                )
                if success:
                    stats["new_indexed"] += 1
                else:
                    stats["failed"] += 1
            else:
                stats["failed"] += 1

        return stats

    def find_duplicate_groups(self, threshold: int = 5, hash_type: str = "phash") -> List[List[str]]:
        """
        全库重复图片分组查找 (使用并查集算法进行图聚类):
        :param threshold: 汉明距离阈值 (默认 <= 5 视为相似)
        :param hash_type: 比较使用的哈希类型 ("phash" 或 "dhash")
        :return: 二维列表，每个子列表代表一组互为重复/相似的图片路径
        """
        all_features = self.db.get_all_features()
        valid_items = []

        # 筛选有效哈希项
        for feat in all_features:
            h_val = feat.get(hash_type)
            if h_val and len(h_val) == 64:
                valid_items.append((feat["image_path"], h_val))

        uf = UnionFind()
        n = len(valid_items)

        # 两两计算汉明距离
        for i in range(n):
            path1, hash1 = valid_items[i]
            for j in range(i + 1, n):
                path2, hash2 = valid_items[j]
                try:
                    dist = calculate_hamming_distance(hash1, hash2)
                    if dist <= threshold:
                        uf.union(path1, path2)
                except Exception:
                    continue

        return uf.get_groups()

    def find_similar_for_image(self, image_path: str, threshold: int = 5) -> List[str]:
        """
        单图相似检索（新图保存时拦截）:
        计算给定图片特征，与数据库已存在数据比对，返回相似图片路径列表。
        :param image_path: 新图片文件绝对路径或相对路径
        :param threshold: 汉明距离阈值 (默认 <= 5)
        :return: 数据库中匹配到的相似表情包路径列表
        """
        hashes = extract_all_hashes(image_path)
        if not hashes or not hashes.get("phash"):
            return []

        target_phash = hashes["phash"]
        target_md5 = hashes["md5"]
        all_features = self.db.get_all_features()
        similar_paths = []

        for feat in all_features:
            db_img_path = feat["image_path"]
            
            # 如果 MD5 相同，直接判定精准重复
            if feat.get("md5") == target_md5:
                similar_paths.append(db_img_path)
                continue

            db_phash = feat.get("phash")
            if db_phash and len(db_phash) == 64:
                try:
                    dist = calculate_hamming_distance(target_phash, db_phash)
                    if dist <= threshold:
                        similar_paths.append(db_img_path)
                except Exception:
                    continue

        return similar_paths


if __name__ == '__main__':
    print("=== Deduplicator 去重核心服务测试 ===")
    dedup = Deduplicator()

    # 1. 增量建立索引
    print("正在扫描并建立特征索引...")
    scan_res = dedup.scan_and_index_images("data/images")
    print(f"索引扫描结果: {scan_res}")

    # 2. 查找全库重复表情包组
    print("\n正在查找库内重复/相似表情包分组 (threshold=5)...")
    groups = dedup.find_duplicate_groups(threshold=5, hash_type="phash")
    print(f"共发现 {len(groups)} 组重复表情包:")
    for idx, group in enumerate(groups, 1):
        print(f"  分组 {idx}: {group}")
