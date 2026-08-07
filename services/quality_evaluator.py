import os
import math
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image, ImageFilter


def calculate_sharpness(pil_img: Image.Image) -> float:
    """
    计算图片的边缘锐度/清晰度（评估包浆度/模糊度）:
    1. 转灰度图
    2. 使用 ImageFilter.FIND_EDGES 提取边缘
    3. 计算边缘图像像素值的方差 (Variance)
    方差越大说明边缘越清晰锐利，方差越小说明越模糊/包浆严重。
    """
    try:
        gray = pil_img.convert('L')
        # 限制计算尺寸，提升大图评测速度
        if gray.width > 512 or gray.height > 512:
            gray.thumbnail((512, 512), Image.Resampling.LANCZOS)

        edges = gray.filter(ImageFilter.FIND_EDGES)
        pixels = list(edges.getdata())

        if not pixels:
            return 0.0

        n = len(pixels)
        mean = sum(pixels) / n
        var = sum((x - mean) ** 2 for x in pixels) / n
        return var
    except Exception as e:
        print(f"[ERROR] [QualityEvaluator] calculate_sharpness 失败: {e}")
        return 0.0


def detect_watermark_penalty(pil_img: Image.Image) -> Tuple[float, bool]:
    """
    检测图片底部水印噪声扣分:
    1. 截取图片底部 15% 区域 (绝大多数水印 Logo 所在地)
    2. 计算该区域高对比度边缘分布密度
    :return: (扣分值, 是否检测到强烈水印)
    """
    try:
        width, height = pil_img.size
        if height < 20:
            return 0.0, False

        # 截取底部 15% 区域
        bottom_h = max(1, int(height * 0.15))
        bottom_crop = pil_img.crop((0, height - bottom_h, width, height))

        gray = bottom_crop.convert('L')
        edges = gray.filter(ImageFilter.FIND_EDGES)
        pixels = list(edges.getdata())

        if not pixels:
            return 0.0, False

        # 统计边缘像素中高于门限 (如 50) 的比例
        threshold = 50
        high_edge_count = sum(1 for p in pixels if p > threshold)
        edge_density = high_edge_count / len(pixels)

        # 如果底部边缘密度过高 (>12%)，说明很有可能包含文字/平台 Logo 水印
        if edge_density > 0.12:
            penalty = min(25.0, edge_density * 100.0)
            return penalty, True
        elif edge_density > 0.06:
            penalty = min(15.0, edge_density * 50.0)
            return penalty, False

        return 0.0, False
    except Exception as e:
        print(f"[ERROR] [QualityEvaluator] detect_watermark_penalty 失败: {e}")
        return 0.0, False


def evaluate_quality(image_path: str) -> Optional[Dict[str, Any]]:
    """
    单图综合品质打分 (0 ~ 100 分)
    考虑指标:
    - 分辨率 (分辨率越大，得分越高)
    - 图像格式 (PNG > WebP > GIF/JPG)
    - 锐度/清晰度 (方差越强得分越高)
    - 水印扣分 (底部检测到密集 Logo 水印)
    - 文件大小 (过小往往压缩严重)
    """
    if not os.path.exists(image_path):
        return None

    try:
        file_size = os.path.getsize(image_path)
        ext = os.path.splitext(image_path)[1].lower()

        with Image.open(image_path) as img:
            width, height = img.size
            sharpness = calculate_sharpness(img)
            watermark_penalty, has_watermark = detect_watermark_penalty(img)

        # 1. 分辨率得分 (权重 40%, 基准 1080x1080 = 40分)
        pixels_count = width * height
        # 对数平滑评分，避免特大图分值爆炸
        res_score = min(40.0, (math.log10(max(pixels_count, 1)) / math.log10(1080 * 1080)) * 40.0)

        # 2. 锐度/清晰度得分 (权重 30%, 方差 500 以上归一为满分 30分)
        sharp_score = min(30.0, (sharpness / 500.0) * 30.0)

        # 3. 格式与无损得分 (权重 20%)
        format_scores = {
            ".png": 20.0,
            ".webp": 18.0,
            ".gif": 15.0,
            ".jpg": 12.0,
            ".jpeg": 12.0,
            ".bmp": 10.0
        }
        fmt_score = format_scores.get(ext, 10.0)

        # 4. 文件体积保真得分 (权重 10%, 10KB~2MB)
        size_score = min(10.0, max(0.0, (file_size / (500 * 1024)) * 10.0))

        # 综合原始得分 (0~100)
        raw_score = res_score + sharp_score + fmt_score + size_score

        # 减去水印扣分
        final_score = max(0.0, min(100.0, raw_score - watermark_penalty))

        fmt_name = ext.replace('.', '').upper()

        return {
            "score": round(final_score, 1),
            "resolution": (width, height),
            "sharpness": round(sharpness, 2),
            "has_watermark": has_watermark,
            "format": fmt_name,
            "file_size": file_size
        }

    except Exception as e:
        print(f"[ERROR] [QualityEvaluator] evaluate_quality 失败 ({image_path}): {e}")
        return None


def rank_duplicate_group(image_paths: List[str]) -> Dict[str, Any]:
    """
    重复组内自动择优判定:
    接受一组重复图片路径列表，评估画质并按得分降序排列。
    挑选出得分最高的图建议保留，其余图建议淘汰。
    """
    if not image_paths:
        return {
            "recommended_keep": "",
            "candidates_to_delete": [],
            "details": []
        }

    evaluated = []
    for path in image_paths:
        info = evaluate_quality(path)
        if info:
            info["path"] = path
            evaluated.append(info)
        else:
            # 读取失败打 0 分兜底
            evaluated.append({
                "path": path,
                "score": 0.0,
                "resolution": (0, 0),
                "sharpness": 0.0,
                "has_watermark": False,
                "format": "UNKNOWN",
                "file_size": 0
            })

    # 按综合品质得分降序排列
    evaluated.sort(key=lambda x: x["score"], reverse=True)

    recommended_keep = evaluated[0]["path"]
    candidates_to_delete = [item["path"] for item in evaluated[1:]]

    details = []
    for idx, item in enumerate(evaluated):
        w, h = item["resolution"]
        reasons = []

        if idx == 0:
            reasons.append(f"最高综合画质得分 ({item['score']}分)")
            reasons.append(f"分辨率 {w}x{h}")
            if item["has_watermark"]:
                reasons.append("提示: 含有疑似水印")
            else:
                reasons.append("无明显底部水印")
        else:
            diff_score = round(evaluated[0]["score"] - item["score"], 1)
            reasons.append(f"分值落后 {diff_score} 分")
            reasons.append(f"分辨率 {w}x{h}")
            if item["has_watermark"]:
                reasons.append("含有疑似水印扣分")
            if item["sharpness"] < evaluated[0]["sharpness"]:
                reasons.append("清晰度/锐度较低")

        details.append({
            "path": item["path"],
            "score": item["score"],
            "reason": "；".join(reasons),
            "resolution": item["resolution"],
            "format": item["format"]
        })

    return {
        "recommended_keep": recommended_keep,
        "candidates_to_delete": candidates_to_delete,
        "details": details
    }


if __name__ == '__main__':
    print("=== QualityEvaluator 图片品质打分与择优测试 ===")

    test_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "images")
    if os.path.exists(test_dir):
        files = [os.path.join(test_dir, f) for f in os.listdir(test_dir) if any(f.lower().endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.webp'))]
        if len(files) >= 2:
            test_group = files[:3]
            print(f"参与测试的图片: {test_group}\n")

            print("--- 单图评分 ---")
            for p in test_group:
                res = evaluate_quality(p)
                print(f"图片: {os.path.basename(p)} -> {res}")

            print("\n--- 组内择优判定 ---")
            rank_res = rank_duplicate_group(test_group)
            print(f"推荐保留: {rank_res['recommended_keep']}")
            print(f"建议淘汰: {rank_res['candidates_to_delete']}")
            print("明细分析:")
            for d in rank_res["details"]:
                print(f"  [{d['score']}分] {os.path.basename(d['path'])}: {d['reason']}")
        else:
            print(f"{test_dir} 路径下图片数量不足（需要至少 2 张图片测试）")
    else:
        print(f"测试路径不存在: {test_dir}")
