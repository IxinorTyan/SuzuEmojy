#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Sticker Downloader (Python Module)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
用于解析、批量并发下载 Telegram 贴纸包/表情包的服务模块。
支持静态 WebP、动画 TGS、视频 WebM 贴纸，支持格式转换为 PNG/GIF 或保存原始文件，
支持多线程并发下载、进度回调及一键 ZIP 打包。

网络策略（智能直连优先）：
1. 优先直接连接 Telegram 官方服务器 (https://api.telegram.org)
2. 当直连网络不可达或超时被阻断时，自动无缝降级切换至 Cloudflare Worker / 备用代理
3. 亦支持传入自定义本地代理 (如 SOCKS5 / HTTP 代理) 与自定义 Bot Token
"""

import os
import re
import io
import sys
import logging
import gzip
import json
import time
import shutil
import zipfile
import tempfile
from typing import Optional, List, Dict, Any, Callable, Union, Set
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs, quote

import requests
from PIL import Image

HAS_CV2 = False

logger = logging.getLogger(__name__)

try:
    from services.webm_converter import (
        convert_video_to_gif,
        is_ffmpeg_available,
        webm_to_png_frame_ffmpeg,
    )
    HAS_FFMPEG_CONVERTER = is_ffmpeg_available()
except Exception:
    HAS_FFMPEG_CONVERTER = False


# ===== 默认内置常量 =====
TG_DIRECT_API = "https://api.telegram.org"
DEF_CF_PROXY = "http://suzu-tg-proxy.1491503128lgz.workers.dev/"
DEF_PROXY = DEF_CF_PROXY
DEF_TOKEN = "8536050631:AAGIiZUSgJ8pQobJZ2UtHLrJUP6Au3fiMnE"


@dataclass
class StickerItem:
    """单个贴纸项元数据"""
    index: int
    file_id: str
    file_unique_id: str
    emoji: str = ""
    width: int = 512
    height: int = 512
    is_animated: bool = False
    is_video: bool = False
    file_size: Optional[int] = None
    thumbnail_file_id: Optional[str] = None
    custom_emoji_id: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StickerPackInfo:
    """贴纸包整体元数据"""
    name: str
    title: str
    sticker_type: str  # 'regular', 'mask', 'custom_emoji'
    is_animated: bool
    is_video: bool
    stickers: List[StickerItem]
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_count(self) -> int:
        return len(self.stickers)

    @property
    def type_desc(self) -> str:
        if self.is_animated:
            return "动画贴纸 (TGS)"
        elif self.is_video:
            return "视频贴纸 (WebM)"
        else:
            return "静态贴纸 (WebP)"


class TGStickerDownloader:
    """Telegram 贴纸下载器核心类"""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        cf_proxy: Optional[str] = None,
        network_proxy: Optional[Union[str, Dict[str, str]]] = None,
        timeout: int = 15,
        retries: int = 2,
        prefer_direct: bool = True,
    ):
        """
        初始化下载器

        :param bot_token: Telegram Bot Token，留空则使用默认内置 Token
        :param cf_proxy: Cloudflare Worker 或反向代理基础 URL，留空使用内置备用代理
        :param network_proxy: 标准网络代理（如 "http://127.0.0.1:7890" 或 "socks5://127.0.0.1:1080"）
        :param timeout: 单次 HTTP 请求超时时间（秒）
        :param retries: 请求重试次数
        :param prefer_direct: 是否优先尝试官方直连 (默认 True，直连失败自动回退到反代)
        """
        self.bot_token = (bot_token or DEF_TOKEN).strip()
        self.cf_proxy = (cf_proxy or DEF_CF_PROXY).strip().rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.prefer_direct = prefer_direct
        self._path_cache: Dict[str, str] = {}
        self._active_base_url: Optional[str] = None

        self.session = requests.Session()
        # 优化连接池容量以提高多线程并发效率
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        if network_proxy:
            if isinstance(network_proxy, str):
                self.session.proxies = {"http": network_proxy, "https": network_proxy}
            elif isinstance(network_proxy, dict):
                self.session.proxies = network_proxy

    @property
    def candidate_bases(self) -> List[str]:
        """获取候选 API 基础 URL 列表 (直连优先或反代优先)"""
        if self._active_base_url:
            # 若已确定有效 base_url，优先使用，备选为另一方
            alt = self.cf_proxy if self._active_base_url == TG_DIRECT_API else TG_DIRECT_API
            return [self._active_base_url, alt]
        if self.prefer_direct:
            return [TG_DIRECT_API, self.cf_proxy]
        else:
            return [self.cf_proxy, TG_DIRECT_API]

    @staticmethod
    def parse_sticker_input(input_str: str) -> Optional[Dict[str, str]]:
        """
        解析输入的贴纸链接或包名
        支持格式:
        - https://t.me/addstickers/PackName
        - https://t.me/addemoji/EmojiPackName
        - tg://resolve?domain=addstickers&set=PackName
        - PackName (纯包名)

        :return: {'name': '包名', 'route': 'addstickers'/'addemoji', 'source': 'url'/'deeplink'/'name'} 或 None
        """
        s = input_str.strip()
        if not s:
            return None

        # 匹配标准 t.me 链接
        m = re.match(
            r"^(?:https?:\/\/)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)\/(addstickers|addemoji)\/([A-Za-z0-9_]+)(?:[/?#].*)?$",
            s,
            re.IGNORECASE,
        )
        if m:
            return {"name": m.group(2), "route": m.group(1).lower(), "source": "url"}

        # 匹配 tg:// 协议链接
        if s.lower().startswith("tg://"):
            try:
                parsed = urlparse(s)
                query = parse_qs(parsed.query)
                domain = (query.get("domain", [""])[0]).lower()
                name = query.get("set", [""])[0] or query.get("start", [""])[0]
                if domain in ("addstickers", "addemoji") and re.match(r"^[A-Za-z0-9_]+$", name):
                    return {"name": name, "route": domain, "source": "deeplink"}
            except Exception:
                pass

        # 匹配纯包名
        if re.match(r"^[A-Za-z0-9_]+$", s):
            return {"name": s, "route": "", "source": "name"}

        return None

    def _api_url_for_base(self, base_url: str, method: str) -> str:
        """根据 base_url 构建 Telegram Bot API URL"""
        return f"{base_url.rstrip('/')}/bot{self.bot_token}/{method.lstrip('/')}"

    def _file_url_for_base(self, base_url: str, file_path: str) -> str:
        """根据 base_url 构建 Telegram 文件下载 URL"""
        return f"{base_url.rstrip('/')}/file/bot{self.bot_token}/{file_path.lstrip('/')}"

    def _request_api(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """带直连优先与自动反代回退重试的 API 请求封装"""
        bases = self.candidate_bases
        last_err = None

        for base in bases:
            url = self._api_url_for_base(base, method)
            for attempt in range(self.retries):
                try:
                    resp = self.session.get(url, params=params, timeout=self.timeout)
                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 2))
                        time.sleep(max(retry_after, 2))
                        continue
                    data = resp.json()
                    if not data.get("ok"):
                        err_desc = data.get("description", "Unknown Telegram API error")
                        raise RuntimeError(err_desc)
                    # 请求成功，记录当前有效线路
                    self._active_base_url = base
                    return data["result"]
                except Exception as e:
                    last_err = e
                    if attempt < self.retries - 1:
                        time.sleep(0.5 + attempt * 0.5)

        raise RuntimeError(f"请求 Telegram API [{method}] 失败: {last_err}")

    def test_connection(self) -> Dict[str, Any]:
        """
        测试网络连通性 (自动检测官方直连与代理通道)

        :return: {'direct_ok': bool, 'proxy_ok': bool, 'bot_ok': bool, 'channel': str, 'bot_username': str, 'error': str}
        """
        result = {
            "direct_ok": False,
            "proxy_ok": False,
            "bot_ok": False,
            "channel": "未知",
            "bot_username": "",
            "error": "",
        }

        # 1. 测试官方直连
        try:
            r = self.session.get(self._api_url_for_base(TG_DIRECT_API, "getMe"), timeout=6)
            if r.status_code == 200 and r.json().get("ok"):
                result["direct_ok"] = True
                result["bot_ok"] = True
                result["channel"] = "Telegram 官方直连 (api.telegram.org)"
                result["bot_username"] = r.json()["result"].get("username", "")
                self._active_base_url = TG_DIRECT_API
                return result
        except Exception as e:
            result["error"] = f"直连失败 ({e}); "

        # 2. 测试备用反代
        try:
            r = self.session.get(self._api_url_for_base(self.cf_proxy, "getMe"), timeout=8)
            if r.status_code == 200 and r.json().get("ok"):
                result["proxy_ok"] = True
                result["bot_ok"] = True
                result["channel"] = f"备用反向代理通道 ({self.cf_proxy})"
                result["bot_username"] = r.json()["result"].get("username", "")
                self._active_base_url = self.cf_proxy
                return result
            else:
                result["error"] += f"反代返回异常: {r.text}"
        except Exception as e:
            result["error"] += f"反代失败 ({e})"

        return result

    def get_sticker_set(self, name_or_url: str) -> StickerPackInfo:
        """
        根据链接或包名获取贴纸包的完整信息

        :param name_or_url: 贴纸链接 (t.me/addstickers/xxx) 或纯包名
        :return: StickerPackInfo 对象
        """
        parsed = self.parse_sticker_input(name_or_url)
        if not parsed:
            raise ValueError(f"无法解析有效的贴纸包名或链接: {name_or_url}")

        pack_name = parsed["name"]
        data = self._request_api("getStickerSet", {"name": pack_name})

        stickers = []
        for idx, item in enumerate(data.get("stickers", [])):
            thumb_id = None
            if "thumbnail" in item:
                thumb_id = item["thumbnail"].get("file_id")
            elif "thumb" in item:
                thumb_id = item["thumb"].get("file_id")

            stickers.append(
                StickerItem(
                    index=idx,
                    file_id=item["file_id"],
                    file_unique_id=item.get("file_unique_id", ""),
                    emoji=item.get("emoji", ""),
                    width=item.get("width", 512),
                    height=item.get("height", 512),
                    is_animated=item.get("is_animated", data.get("is_animated", False)),
                    is_video=item.get("is_video", data.get("is_video", False)),
                    file_size=item.get("file_size"),
                    thumbnail_file_id=thumb_id,
                    custom_emoji_id=item.get("custom_emoji_id"),
                    raw_data=item,
                )
            )

        return StickerPackInfo(
            name=data.get("name", pack_name),
            title=data.get("title", pack_name),
            sticker_type=data.get("sticker_type", "regular"),
            is_animated=data.get("is_animated", False),
            is_video=data.get("is_video", False),
            stickers=stickers,
            raw_data=data,
        )

    def get_file_path(self, file_id: str) -> str:
        """获取文件的相对路径 (带缓存)"""
        if file_id in self._path_cache:
            return self._path_cache[file_id]

        file_info = self._request_api("getFile", {"file_id": file_id})
        fp = file_info.get("file_path", "")
        if not fp:
            raise RuntimeError(f"未能获取到 file_id [{file_id}] 的 file_path")
        self._path_cache[file_id] = fp
        return fp

    def download_file_bytes(self, file_path_or_id: str) -> bytes:
        """
        下载指定文件的原始二进制数据 (带线路自动切换)

        :param file_path_or_id: 文件路径 (如 'stickers/file_0.webp') 或 Telegram file_id
        :return: bytes
        """
        if "/" not in file_path_or_id and "." not in file_path_or_id:
            file_path = self.get_file_path(file_path_or_id)
        else:
            file_path = file_path_or_id

        bases = self.candidate_bases
        last_err = None

        for base in bases:
            url = self._file_url_for_base(base, file_path)
            for attempt in range(self.retries):
                try:
                    resp = self.session.get(url, timeout=self.timeout)
                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 2))
                        time.sleep(max(retry_after, 2))
                        continue
                    resp.raise_for_status()
                    self._active_base_url = base
                    return resp.content
                except Exception as e:
                    last_err = e
                    if attempt < self.retries - 1:
                        time.sleep(0.5 + attempt * 0.5)

        raise RuntimeError(f"下载文件 [{file_path}] 失败: {last_err}")

    # ===== 格式转换工具 =====

    @staticmethod
    def webp_to_png(webp_bytes: bytes) -> bytes:
        """将静态 WebP 图像转为 PNG bytes"""
        with Image.open(io.BytesIO(webp_bytes)) as img:
            out_io = io.BytesIO()
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.save(out_io, format="PNG", optimize=True)
            return out_io.getvalue()

    @staticmethod
    def webm_to_png_frame(webm_bytes: bytes) -> bytes:
        """提取 WebM 视频第一帧为 PNG bytes，优先使用 FFmpeg。"""
        tmp_in = None
        tmp_out = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tf_in:
                tf_in.write(webm_bytes)
                tmp_in = tf_in.name
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf_out:
                tmp_out = tf_out.name

            webm_to_png_frame_ffmpeg(tmp_in, tmp_out)
            with open(tmp_out, "rb") as f:
                return f.read()
        except Exception as e:
            logger.warning("FFmpeg 首帧提取失败，回退 cv2: %s", e)
        finally:
            for path in (tmp_in, tmp_out):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        try:
            with Image.open(io.BytesIO(webm_bytes)) as img:
                out_io = io.BytesIO()
                img.save(out_io, format="PNG")
                return out_io.getvalue()
        except Exception:
            return webm_bytes

    @staticmethod
    def webm_to_gif(webm_bytes: bytes, fps: int = 15, max_frames: int = 120, max_size: int = 512) -> bytes:
        """将 WebM 视频贴纸转换为动态 GIF bytes (优先使用高性能 FFmpeg)"""
        # 1. 优先使用 FFmpeg 两步调色板高质量且极速转码
        if HAS_FFMPEG_CONVERTER:
            tmp_in = None
            tmp_out = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tf_in:
                    tf_in.write(webm_bytes)
                    tmp_in = tf_in.name

                with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as tf_out:
                    tmp_out = tf_out.name

                if convert_video_to_gif(tmp_in, tmp_out, fps=fps, max_size=max_size):
                    with open(tmp_out, "rb") as f:
                        return f.read()
            except Exception as e:
                logger.warning("FFmpeg GIF 转换失败，回退 cv2: %s", e)
            finally:
                if tmp_in and os.path.exists(tmp_in):
                    try:
                        os.remove(tmp_in)
                    except Exception:
                        pass
                if tmp_out and os.path.exists(tmp_out):
                    try:
                        os.remove(tmp_out)
                    except Exception:
                        pass

        # FFmpeg 不可用或转换失败时返回原始 WebM，避免引入体积很大的 OpenCV。
        return webm_bytes


    @staticmethod
    def decompress_tgs_json(tgs_bytes: bytes) -> str:
        """解压 TGS (gzip 格式) 贴纸得到原始 Lottie JSON 字符串"""
        raw_json = gzip.decompress(tgs_bytes)
        return raw_json.decode("utf-8")

    def process_sticker_data(
        self,
        raw_bytes: bytes,
        ext: str,
        target_format: str,
    ) -> tuple[bytes, str]:
        """
        根据目标格式处理/转换贴纸数据

        :param raw_bytes: 原始贴纸文件 bytes
        :param ext: 原始文件扩展名 ('webp', 'tgs', 'webm')
        :param target_format: 'original' | 'png' | 'gif'
        :return: (处理后的 bytes, 对应的扩展名)
        """
        ext = ext.lower().lstrip(".")
        target_format = target_format.lower()

        if target_format == "original":
            return raw_bytes, ext

        if target_format == "png":
            if ext == "webp":
                return self.webp_to_png(raw_bytes), "png"
            elif ext == "webm":
                png_bytes = self.webm_to_png_frame(raw_bytes)
                return png_bytes, "png"
            elif ext == "tgs":
                return raw_bytes, "tgs"
            return raw_bytes, ext

        if target_format == "gif":
            if ext == "webm":
                gif_bytes = self.webm_to_gif(raw_bytes)
                return gif_bytes, "gif"
            elif ext == "tgs":
                return raw_bytes, "tgs"
            elif ext == "webp":
                return self.webp_to_png(raw_bytes), "png"
            return raw_bytes, ext

        return raw_bytes, ext

    def download_sticker(
        self,
        sticker: StickerItem,
        target_format: str = "original",
    ) -> tuple[bytes, str]:
        """
        下载单张贴纸并按需格式转换

        :return: (文件 bytes, 文件扩展名)
        """
        file_path = self.get_file_path(sticker.file_id)
        raw_ext = file_path.split(".")[-1] if "." in file_path else "webp"
        raw_bytes = self.download_file_bytes(file_path)
        return self.process_sticker_data(raw_bytes, raw_ext, target_format)

    def download_pack(
        self,
        name_or_url: str,
        output_dir: Optional[str] = None,
        format: str = "png",
        to_zip: bool = False,
        zip_path: Optional[str] = None,
        selected_indices: Optional[Union[List[int], Set[int]]] = None,
        max_workers: int = 5,
        progress_callback: Optional[Callable[[int, int, str, Optional[Dict[str, Any]]], None]] = None,
    ) -> Dict[str, Any]:
        """
        并发下载整个贴纸包

        :param name_or_url: 贴纸链接或包名
        :param output_dir: 保存目录（留空则默认保存在 ./downloads/{pack_name}）
        :param format: 目标格式 ('png' | 'gif' | 'original' | 'auto')
        :param to_zip: 是否在下载后打包为 zip 压缩包
        :param zip_path: 指定 zip 保存路径（若为 None 则保存在 output_dir 的父级或同名 zip）
        :param selected_indices: 指定下载的贴纸下标列表（从 0 开始），None 表示下载全部
        :param max_workers: 并发下载线程数 (默认 5)
        :param progress_callback: 进度回调函数，签名: callback(done_count, total_count, status_message, extra_dict)
        :return: 结果字典，包含成功列表、失败列表、保存路径、zip 路径等
        """
        def _notify(done: int, total: int, msg: str, extra: Optional[Dict[str, Any]] = None):
            if progress_callback:
                try:
                    progress_callback(done, total, msg, extra)
                except Exception:
                    pass

        # 1. 获取贴纸包信息
        _notify(0, 0, "正在获取贴纸包信息...")
        pack = self.get_sticker_set(name_or_url)
        all_stickers = pack.stickers

        # 筛选需要下载的贴纸
        if selected_indices is not None:
            target_indices = set(selected_indices)
            target_stickers = [s for s in all_stickers if s.index in target_indices]
        else:
            target_stickers = all_stickers

        total_count = len(target_stickers)
        if total_count == 0:
            return {
                "pack_name": pack.name,
                "pack_title": pack.title,
                "total": 0,
                "success_count": 0,
                "fail_count": 0,
                "files": [],
                "output_dir": None,
                "zip_path": None,
                "errors": ["未选择任何贴纸"],
            }

        # 2. 准备输出目录
        if not output_dir:
            output_dir = os.path.join(".", "downloads", pack.name)
        os.makedirs(output_dir, exist_ok=True)

        # 3. 智能选择默认格式
        if format == "auto":
            if pack.is_animated or pack.is_video:
                format = "gif"
            else:
                format = "png"

        # 4. 并发下载与转换任务
        downloaded_files: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        done_count = 0

        _notify(0, total_count, f"开始下载 [{pack.title}] 共 {total_count} 个贴纸...")

        def _worker(stk: StickerItem):
            try:
                # 获取路径与原始扩展名
                file_path = self.get_file_path(stk.file_id)
                raw_ext = file_path.split(".")[-1] if "." in file_path else "webp"
                raw_bytes = self.download_file_bytes(file_path)
                final_bytes, final_ext = self.process_sticker_data(raw_bytes, raw_ext, format)

                # 文件命名：001_emoji.ext
                safe_emoji = re.sub(r'[\\/:*?"<>|]', "", stk.emoji or "")
                fname = f"{stk.index + 1:03d}" + (f"_{safe_emoji}" if safe_emoji else "") + f".{final_ext}"
                save_path = os.path.join(output_dir, fname)

                with open(save_path, "wb") as f:
                    f.write(final_bytes)

                return {
                    "ok": True,
                    "index": stk.index,
                    "file_name": fname,
                    "file_path": save_path,
                    "size": len(final_bytes),
                    "emoji": stk.emoji,
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "index": stk.index,
                    "error": str(exc),
                    "emoji": stk.emoji,
                }

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_item = {executor.submit(_worker, stk): stk for stk in target_stickers}
            for future in as_completed(future_to_item):
                res = future.result()
                done_count += 1
                if res["ok"]:
                    downloaded_files.append(res)
                    msg = f"已下载 ({done_count}/{total_count}): {res['file_name']}"
                else:
                    errors.append(res)
                    msg = f"下载失败 ({done_count}/{total_count}): 贴纸 #{res['index'] + 1} - {res['error']}"

                _notify(done_count, total_count, msg, res)

        # 按贴纸原始顺序排序
        downloaded_files.sort(key=lambda x: x["index"])

        # 5. ZIP 打包支持
        final_zip_path = None
        if to_zip and downloaded_files:
            _notify(done_count, total_count, "正在打包为 ZIP 文件...")
            if not zip_path:
                zip_path = os.path.join(os.path.dirname(output_dir), f"{pack.name}.zip")

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in downloaded_files:
                    zf.write(item["file_path"], arcname=os.path.join(pack.name, item["file_name"]))
            final_zip_path = os.path.abspath(zip_path)
            _notify(done_count, total_count, f"ZIP 打包完成: {final_zip_path}")

        return {
            "pack_name": pack.name,
            "pack_title": pack.title,
            "pack_type": pack.type_desc,
            "total": total_count,
            "success_count": len(downloaded_files),
            "fail_count": len(errors),
            "output_dir": os.path.abspath(output_dir),
            "zip_path": final_zip_path,
            "files": downloaded_files,
            "errors": errors,
        }
