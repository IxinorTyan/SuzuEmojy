import os
import shutil
import configparser
from pathlib import Path

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
    def get_actual_extension(file_path):
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
        
        # 尝试动态导入 chardet (如果存在)
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
    def scan_emojis(emoji_root_path, selected_folder):
        """扫描并过滤有效表情包文件"""
        if not emoji_root_path or not os.path.exists(emoji_root_path):
            return []
        
        target_scan_path = emoji_root_path
        if selected_folder in ['emoji-recv', 'personal_emoji', 'marketface']:
            try:
                for d in os.listdir(emoji_root_path):
                    if d.lower() == 'ori' and os.path.isdir(emoji_root_path / d):
                        target_scan_path = emoji_root_path / d
                        break
            except Exception:
                pass

        raw_files = []
        for root, _, filenames in os.walk(str(target_scan_path)):
            for filename in filenames:
                raw_files.append(os.path.join(root, filename))

        # 快速通过魔数筛选与路径/格式优先级去重
        unique_emojis = {}
        for file_path_str in raw_files:
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            if actual_ext:
                base_name = os.path.splitext(os.path.basename(file_path_str))[0].lower()
                if base_name not in unique_emojis:
                    unique_emojis[base_name] = (file_path_str, actual_ext)
                else:
                    existing_path, existing_ext = unique_emojis[base_name]
                    is_new_gif = (actual_ext.lower() == 'gif')
                    is_old_gif = (existing_ext.lower() == 'gif')
                    
                    if is_new_gif and not is_old_gif:
                        unique_emojis[base_name] = (file_path_str, actual_ext)
                    elif not is_new_gif and is_old_gif:
                        pass
                    else:
                        # 如果都是gif或都不是gif，则优先选择ori原图目录下的文件
                        new_is_ori = ('/ori/' in file_path_str.replace('\\', '/'))
                        old_is_ori = ('/ori/' in existing_path.replace('\\', '/'))
                        if new_is_ori and not old_is_ori:
                            unique_emojis[base_name] = (file_path_str, actual_ext)

        return [val[0] for val in unique_emojis.values()]
