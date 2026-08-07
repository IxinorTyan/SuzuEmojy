import os
import math
import hashlib
from typing import Optional, Dict, Any
from PIL import Image

# 预计算 32x32 DCT 的余弦矩阵表，加速 pHash 运算
_COS_MATRIX = [
    [math.cos((2 * x + 1) * u * math.pi / 64.0) for x in range(32)]
    for u in range(8)
]


def crop_center(pil_img: Image.Image, crop_ratio: float = 0.8) -> Image.Image:
    """
    裁切掉图片四周的边缘，仅保留中心 crop_ratio（默认 80%）的图像区域。
    有效防止边缘水印（如抖音/小红书/微博 Logo）干扰相似度计算。
    """
    if crop_ratio >= 1.0 or crop_ratio <= 0.0:
        return pil_img

    width, height = pil_img.size
    crop_w = max(1, int(width * crop_ratio))
    crop_h = max(1, int(height * crop_ratio))

    left = (width - crop_w) // 2
    top = (height - crop_h) // 2
    right = left + crop_w
    bottom = top + crop_h

    return pil_img.crop((left, top, right, bottom))


def compute_dhash(pil_img: Image.Image) -> str:
    """
    计算图片的 dHash (差异哈希):
    1. 中心裁剪去水印
    2. 转灰度图，Resize 为 9x8 像素
    3. 比较相邻像素亮度，生成 64 位二进制 Hash 字符串
    """
    cropped = crop_center(pil_img, crop_ratio=0.8)
    # 转灰度并缩放为 9x8 (9 列, 8 行)
    resized = cropped.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(resized.getdata())

    bit_list = []
    for row in range(8):
        for col in range(8):
            left_pixel = pixels[row * 9 + col]
            right_pixel = pixels[row * 9 + col + 1]
            bit_list.append('1' if left_pixel > right_pixel else '0')

    return "".join(bit_list)


def compute_phash(pil_img: Image.Image) -> str:
    """
    计算图片的 pHash (感知哈希):
    1. 中心裁剪去水印
    2. 转灰度图，Resize 为 32x32
    3. 计算 32x32 的离散余弦变换 (DCT)，提取左上角 8x8 低频特征
    4. 比较 low-frequency 像素值与均值，生成 64 位二进制 Hash 字符串
    """
    cropped = crop_center(pil_img, crop_ratio=0.8)
    resized = cropped.convert('L').resize((32, 32), Image.Resampling.LANCZOS)
    pixels = list(resized.getdata())

    # 提取 32x32 像素点数据 f(x, y)
    grid = [pixels[i * 32:(i + 1) * 32] for i in range(32)]

    # 计算 8x8 低频 DCT 矩阵
    dct_low = []
    for u in range(8):
        for v in range(8):
            # 2D DCT 变换公式
            sum_val = 0.0
            cos_u = _COS_MATRIX[u]
            cos_v = _COS_MATRIX[v]
            for x in range(32):
                row_pixels = grid[x]
                cos_u_x = cos_u[x]
                for y in range(32):
                    sum_val += row_pixels[y] * cos_u_x * cos_v[y]
            
            c_u = 1.0 / math.sqrt(2) if u == 0 else 1.0
            c_v = 1.0 / math.sqrt(2) if v == 0 else 1.0
            dct_low.append(0.25 * c_u * c_v * sum_val)

    # 计算除 DC (0,0) 分量外的均值，或包含 DC 分量的均值
    # 此处取 8x8 所有 64 个低频系数的均值
    avg = sum(dct_low) / 64.0

    # 生成 64 位二进制字符串
    bit_list = ['1' if val > avg else '0' for val in dct_low]
    return "".join(bit_list)


def calculate_hamming_distance(hash1: str, hash2: str) -> int:
    """
    计算两个 64 位二进制 Hash 字符串之间的汉明距离（不同字符的数量）
    返回值范围: 0 到 64
    """
    if len(hash1) != len(hash2):
        raise ValueError(f"Hash 长度不匹配: {len(hash1)} vs {len(hash2)}")

    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def is_similar(hash1: str, hash2: str, threshold: int = 5) -> bool:
    """
    判断两个 Hash 的汉明距离是否小于等于阈值 threshold (默认 5)
    """
    try:
        dist = calculate_hamming_distance(hash1, hash2)
        return dist <= threshold
    except Exception:
        return False


def extract_all_hashes(image_path: str) -> Optional[Dict[str, str]]:
    """
    主入口函数：读取图片路径，提取 MD5、dHash 与 pHash。
    如图片损坏或不存在，捕获异常并返回 None。
    """
    if not os.path.exists(image_path):
        return None

    try:
        # 1. 计算文件 MD5
        with open(image_path, 'rb') as f:
            file_bytes = f.read()
            md5_val = hashlib.md5(file_bytes).hexdigest()

        # 2. 打开图片计算 dHash 与 pHash
        with Image.open(image_path) as img:
            dhash_val = compute_dhash(img)
            phash_val = compute_phash(img)

        return {
            "md5": md5_val,
            "dhash": dhash_val,
            "phash": phash_val
        }

    except Exception as e:
        print(f"[ERROR] [Hasher] 提取图片特征失败 ({image_path}): {e}")
        return None


if __name__ == '__main__':
    import sys

    print("=== Hasher 图像感知哈希算法模块测试 ===")
    
    # 找一张已有图片做测试
    test_img_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "images")
    if os.path.exists(test_img_dir):
        files = os.listdir(test_img_dir)
        if files:
            sample_path = os.path.join(test_img_dir, files[0])
            print(f"正在测试图片: {sample_path}")
            res = extract_all_hashes(sample_path)
            if res:
                print(f"  MD5:   {res['md5']}")
                print(f"  dHash: {res['dhash']} (长度: {len(res['dhash'])})")
                print(f"  pHash: {res['phash']} (长度: {len(res['phash'])})")

                # 自我比较校验
                d_dist = calculate_hamming_distance(res['dhash'], res['dhash'])
                p_dist = calculate_hamming_distance(res['phash'], res['phash'])
                print(f"  dHash 自比汉明距离: {d_dist}")
                print(f"  pHash 自比汉明距离: {p_dist}")
                print(f"  相似度判定 (threshold=5): {is_similar(res['phash'], res['phash'])}")
            else:
                print("提取失败")
        else:
            print("data/images 目录下无测试图片")
    else:
        print(f"未找到测试图片目录: {test_img_dir}")
