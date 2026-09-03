import os
import re
import json
import time
import shutil
import hashlib
import tempfile
import configparser
from pathlib import Path
from PIL import Image, ImageSequence

from services.marketface_handler import (
    get_recovered_gif_path,
    is_marketface_candidate,
    recover_marketface_data,
)


class QQExtractor:
    FILE_SIGNATURES = {
        'jpg': (b'\xff\xd8\xff', b'\xff\xd8\xff\xe0', b'\xff\xd8\xff\xe1'),
        'png': (b'\x89PNG\r\n\x1a\n',),
        'gif': (b'GIF87a', b'GIF89a'),
        'bmp': (b'BM',),
        'tiff': (b'II*\x00', b'MM\x00*'),
        'webp': (b'RIFF', b'WEBP'),
        'ico': (b'\x00\x00\x01\x00', b'\x00\x00\x02\x00'),
        'psd': (b'8BPS',),
        'svg': (b'<?xml', b'<svg'),
        'heic': (b'ftypheic', b'ftypheix', b'ftyphevc', b'ftyphevx'),
        'avif': (b'ftypavif', b'ftypavis'),
    }

    @staticmethod
    def is_apng_file(file_path):
        """
        通过解析 PNG Chunk 极速判断是否为 APNG（带有 acTL 块）
        """
        if not file_path or not os.path.exists(file_path):
            return False
        try:
            with open(file_path, 'rb') as f:
                header = f.read(8)
                if header != b'\x89PNG\r\n\x1a\n':
                    return False
                while True:
                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        break
                    length = int.from_bytes(length_bytes, 'big')
                    chunk_type = f.read(4)
                    if chunk_type == b'acTL':
                        return True
                    if chunk_type in (b'IDAT', b'IEND'):
                        break
                    # 跳过数据块以及 4 字节 CRC
                    f.seek(length + 4, 1)
        except Exception:
            pass
        return False

    @staticmethod
    def get_temp_gif_cache_dir():
        """获取 APNG 预览转码临时缓存目录"""
        temp_dir = os.path.join(tempfile.gettempdir(), "SuzuEmojy_QQ_GIF_Cache")
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    @staticmethod
    def convert_apng_to_gif(apng_path, output_gif_path=None):
        """
        将 APNG 转换为带透明度的标准 GIF 动图。
        如果未指定 output_gif_path，则在系统临时目录生成一个以文件哈希命名的缓存文件。
        返回生成的 gif 路径，若转换失败则返回 None。
        """
        if not os.path.exists(apng_path):
            return None

        if output_gif_path is None:
            try:
                with open(apng_path, 'rb') as f:
                    content = f.read()
                file_hash = hashlib.md5(content).hexdigest()
            except Exception:
                file_hash = hashlib.md5(apng_path.encode('utf-8')).hexdigest()
            output_gif_path = os.path.join(QQExtractor.get_temp_gif_cache_dir(), f"{file_hash}.gif")

        # 如果临时缓存已存在且非空，直接返回
        if os.path.exists(output_gif_path) and os.path.getsize(output_gif_path) > 0:
            return output_gif_path

        try:
            im = Image.open(apng_path)
            frames = []
            durations = []
            
            # 逐帧提取
            for frame in ImageSequence.Iterator(im):
                # 保持 RGBA 色彩与透明通道
                rgba_frame = frame.convert('RGBA')
                frames.append(rgba_frame)
                # 获取帧间隔时间，默认为 40ms (25fps)
                dur = frame.info.get('duration', 40)
                if dur <= 0:
                    dur = 40
                durations.append(dur)

            if not frames:
                return None

            # 确保目标目录存在
            os.makedirs(os.path.dirname(os.path.abspath(output_gif_path)), exist_ok=True)

            # 保存为 GIF (disposal=2 防止帧残留叠影)
            frames[0].save(
                output_gif_path,
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=0,
                disposal=2
            )
            return output_gif_path
        except Exception as e:
            print(f"APNG 转 GIF 失败 ({apng_path}): {e}")
            return None

    @staticmethod
    def get_actual_extension(file_path):
        if not file_path or not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'rb') as f:
                header = f.read(16)
            for ext, signatures in QQExtractor.FILE_SIGNATURES.items():
                for sig in signatures:
                    if header.startswith(sig):
                        return ext
        except Exception:
            pass
        return None

    @staticmethod
    def _calculate_emoji_score(file_path_str, actual_ext):
        """
        计算候选表情文件的综合质量得分：
        1. APNG/GIF 动图优先 (+1000分)
        2. 主文件优先于 _0, _1 切片 (+200分)
        3. 优先目录权重 (apng > ori/raw > png > thumb/preview)
        4. 文件体积评分 (+0~50分)
        """
        score = 0
        file_base = os.path.splitext(os.path.basename(file_path_str))[0].lower()
        lowered_path = file_path_str.lower().replace('\\', '/')

        # 1. 动图优先
        if actual_ext == 'png' and QQExtractor.is_apng_file(file_path_str):
            score += 1000
        elif actual_ext == 'gif':
            score += 1000

        # 2. 主图优先（非切片）
        if not re.search(r'_\d+$', file_base):
            score += 200

        # 3. 目录层级权重
        if '/apng/' in lowered_path or lowered_path.endswith('/apng'):
            score += 100
        elif '/ori/' in lowered_path or '/raw/' in lowered_path:
            score += 80
        elif '/png/' in lowered_path:
            score += 40
        elif '/thumb/' in lowered_path or '/preview/' in lowered_path:
            score -= 100

        # 4. 文件体积加分
        try:
            size_kb = os.path.getsize(file_path_str) / 1024
            score += min(size_kb, 50.0)
        except Exception:
            pass

        return score

    @staticmethod
    def _get_emoji_group_key(file_path_str, emoji_root_path):
        """
        根据相对路径和文件名生成归一化的表情分组键 (Group Key)。
        使得同一个表情的 apng、png、切片 (370_0, 370_1) 归入同一个 Group Key 进行打分竞争。
        """
        try:
            rel_path = os.path.relpath(file_path_str, emoji_root_path)
        except Exception:
            rel_path = os.path.basename(file_path_str)

        rel_dir = os.path.dirname(rel_path)
        file_name = os.path.basename(file_path_str)
        file_base = os.path.splitext(file_name)[0].lower()

        # 去除切片序号 (如 370_0 -> 370)
        clean_name = re.sub(r'_\d+$', '', file_base)

        # 过滤掉通用的子目录名称 (如 apng, png, ori, thumb 等)
        normalized_dir = re.sub(r'[\\/](apng|png|ori|raw|thumb|preview)$', '', rel_dir, flags=re.IGNORECASE)
        if normalized_dir in ['.', 'apng', 'png', 'ori', 'raw', 'thumb', 'preview']:
            normalized_dir = ''

        # 组合分组键
        if normalized_dir:
            group_key = f"{normalized_dir}/{clean_name}".replace('\\', '/').lower().strip('/')
        else:
            group_key = clean_name.lower().strip('/')

        return group_key

    @staticmethod
    def get_nickname_cache_path():
        appdata_path = os.getenv('LOCALAPPDATA')
        if not appdata_path:
            appdata_path = os.path.join(os.getenv('USERPROFILE', ''), 'AppData', 'LocalLow')
        cache_dir = os.path.join(appdata_path, 'SuzuEmojy_QQ_Cache')
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, 'user_nicknames.json')

    @staticmethod
    def load_nickname_cache():
        cache_path = QQExtractor.get_nickname_cache_path()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    @staticmethod
    def save_nickname_cache(cache_data):
        cache_path = QQExtractor.get_nickname_cache_path()
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def get_user_nickname(qq_number):
        cache = QQExtractor.load_nickname_cache()
        now = int(time.time())
        
        # 检查缓存中是否有未过期的数据
        if str(qq_number) in cache and \
           'username_expire_time' in cache[str(qq_number)] and \
           cache[str(qq_number)]['username_expire_time'] > now:
            return cache[str(qq_number)].get('name', '')
        
        # 从API获取新数据
        try:
            import urllib.request
            url = f"https://uapis.cn/api/v1/social/qq/userinfo?qq={qq_number}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    if data and data.get("nickname"):
                        cache[str(qq_number)] = {
                            'name': data['nickname'],
                            'username_expire_time': now + 86400  # 缓存 1 天
                        }
                        QQExtractor.save_nickname_cache(cache)
                        return data['nickname']
        except Exception:
            pass
        
        return ''

    @staticmethod
    def get_display_name(qq_number):
        nickname = QQExtractor.get_user_nickname(qq_number)
        if nickname:
            return f"{nickname}（{qq_number}）"
        return str(qq_number)

    @staticmethod
    def detect_tencent_files_path():
        """自动检测系统文档中是否存在 Tencent Files 目录"""
        def get_windows_documents_path():
            try:
                import winreg
                sub_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                    personal_path, _ = winreg.QueryValueEx(key, "Personal")
                    return personal_path
            except Exception:
                return None

        candidate_roots = []
        doc_path = get_windows_documents_path()
        if doc_path:
            candidate_roots.append(Path(doc_path))
        user_home = Path(os.path.expanduser('~'))
        candidate_roots.append(user_home / "Documents")
        candidate_roots.append(user_home)
        candidate_roots.append(Path(r"C:\Users\Public\Documents"))

        seen = set()
        unique_roots = []
        for root in candidate_roots:
            try:
                resolved = root.resolve()
                if resolved not in seen and resolved.exists():
                    seen.add(resolved)
                    unique_roots.append(resolved)
            except Exception:
                continue

        for root in unique_roots:
            tencent_dir = root / "Tencent Files"
            if tencent_dir.exists() and tencent_dir.is_dir():
                try:
                    for sub in os.listdir(tencent_dir):
                        sub_path = tencent_dir / sub
                        if sub.isdigit() and sub_path.is_dir():
                            if (sub_path / "nt_qq").exists():
                                return str(tencent_dir)
                except Exception:
                    continue
        return None

    @staticmethod
    def is_content_valid(content, min_chinese=1):
        chinese_chars = sum('\u4e00' <= char <= '\u9fff' for char in content)
        return chinese_chars >= min_chinese

    @staticmethod
    def read_file_with_correct_encoding(file_path, target_string):
        try:
            with open(file_path, 'rb') as f:
                data = f.read()
        except Exception:
            return None

        # 尝试常用编码，避免第三方依赖
        encodings = ['gb18030', 'utf-8', 'utf-16', 'gbk', 'big5', 'utf-16-le', 'latin-1']
        
        try:
            import chardet
            detected = chardet.detect(data)
            if detected['encoding']:
                encodings.insert(0, detected['encoding'])
        except Exception:
            pass

        seen = set()
        ordered_encodings = []
        for enc in encodings:
            enc_lower = enc.lower()
            if enc_lower not in seen:
                seen.add(enc_lower)
                ordered_encodings.append(enc)

        for enc in ordered_encodings:
            try:
                content = data.decode(enc, errors='strict')
            except Exception:
                continue
            if target_string in content and (QQExtractor.is_content_valid(content) or not target_string.isascii()):
                return enc
        return None

    @staticmethod
    def get_userdata_save_path(ini_file_path, cache_path=None):
        if cache_path and os.path.exists(cache_path):
            return cache_path

        config = configparser.ConfigParser()
        target_string = '[UserDataSet]'
        userdata_save_path = None

        if ini_file_path and os.path.exists(ini_file_path):
            try:
                encode = QQExtractor.read_file_with_correct_encoding(ini_file_path, target_string)
                if encode:
                    config.read(ini_file_path, encoding=encode)
                    if 'UserDataSet' in config:
                        userdata_save_path = config.get('UserDataSet', 'UserDataSavePath', fallback=None)
            except Exception:
                pass

        if not userdata_save_path or not os.path.exists(userdata_save_path):
            detected_path = QQExtractor.detect_tencent_files_path()
            if detected_path:
                userdata_save_path = detected_path

        return userdata_save_path

    @staticmethod
    def get_numeric_subdirectories(parent_dir):
        if not parent_dir or not os.path.exists(parent_dir):
            return []
        try:
            subdirs = [name for name in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, name))]
            return [name for name in subdirs if name.isdigit()]
        except Exception:
            return []

    @staticmethod
    def read_marketface_data(file_path):
        """读取并恢复 marketface 文件，返回可用的 GIF 二进制数据。"""
        recovered = recover_marketface_data(file_path)
        return recovered[0] if recovered is not None else None

    @staticmethod
    def get_marketface_info(file_path):
        """读取并恢复 marketface，返回 (GIF 数据, 帧数)。"""
        return recover_marketface_data(file_path)

    @staticmethod
    def get_marketface_gif_path(file_path):
        """获取恢复后的 marketface 临时 GIF 文件路径。"""
        return get_recovered_gif_path(file_path)

    @staticmethod
    def scan_marketface(emoji_root_path):
        """扫描 marketface 原图，只返回成功恢复并通过 GIF 校验的文件。"""
        emoji_path = Path(emoji_root_path) if emoji_root_path else None
        if not emoji_path or not emoji_path.exists():
            return []

        recovered_files = []
        try:
            for root, _, files in os.walk(str(emoji_path)):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    if not is_marketface_candidate(file_path):
                        continue
                    if recover_marketface_data(file_path) is not None:
                        recovered_files.append(file_path)
        except Exception:
            return []

        return sorted(recovered_files)

    @staticmethod
    def scan_emojis(emoji_root_path, selected_folder=None):
        """
        针对不同 QQNT 表情分类，全量安全扫描并利用智能评分算法筛选出最优质的表情图片文件列表。
        自动剔除冗余子帧/切片，动图自动优选，且保证不会遗漏任何有效表情。
        """
        if selected_folder and selected_folder.lower() == "marketface":
            return QQExtractor.scan_marketface(emoji_root_path)

        emoji_path = Path(emoji_root_path) if emoji_root_path else None
        if not emoji_path or not emoji_path.exists():
            return []

        # 1. 全量安全深度遍历，确保任何层级的文件都不会遗漏
        raw_files = []
        try:
            for root, _, files in os.walk(str(emoji_path)):
                for f in files:
                    raw_files.append(os.path.join(root, f))
        except Exception:
            return []

        # 2. 真实图片校验与分组智能竞争
        # groups: { group_key: (best_file_path, best_score) }
        groups = {}

        for file_path_str in raw_files:
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            if not actual_ext:
                continue

            group_key = QQExtractor._get_emoji_group_key(file_path_str, emoji_path)
            score = QQExtractor._calculate_emoji_score(file_path_str, actual_ext)

            if group_key not in groups:
                groups[group_key] = (file_path_str, score)
            else:
                _, existing_score = groups[group_key]
                if score > existing_score:
                    groups[group_key] = (file_path_str, score)

        # 3. 提取每个分组的最佳表情文件
        best_files = [val[0] for val in groups.values()]
        best_files.sort()
        return best_files
