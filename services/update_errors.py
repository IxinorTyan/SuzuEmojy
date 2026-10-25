"""Structured updater diagnostics, formatted in the UI's current language."""
import errno
import re
import zipfile

import requests


REASONS = {
    "timeout": "连接或读取超时。",
    "dns": "域名解析失败，无法找到服务器。",
    "proxy": "无法连接代理服务器，请检查代理或 VPN 设置。",
    "tls": "TLS 证书验证失败，请检查系统时间和代理证书。",
    "connection": "连接中断或服务器无法访问。",
    "http": "服务器返回 HTTP {status}。",
    "not_found": "服务器找不到更新资源（HTTP 404），发布包可能已被删除或镜像尚未同步。",
    "forbidden": "服务器拒绝访问（HTTP {status}），可能受到访问限制。",
    "server": "服务器暂时异常（HTTP {status}），请稍后重试。",
    "rate_limit": "请求过于频繁或 GitHub API 配额已用尽，请稍后重试。",
    "metadata": "服务器返回的更新信息无效，可能是拦截页或镜像暂未同步。",
    "content": "服务器返回了网页，未收到 ZIP 更新包。",
    "redirect": "服务器将请求重定向到了不安全的 HTTP 地址。",
    "size": "下载大小不符：预期 {expected} 字节，实际 {actual} 字节。",
    "hash": "下载文件的 SHA-256 校验不匹配，文件可能损坏或镜像尚未同步。",
    "permission": "无法写入文件，可能缺少权限或文件正被占用。",
    "space": "磁盘空间不足，请清理软件所在磁盘后重试。",
    "installation": "当前安装缺少有效的更新清单或更新器，请从发布页下载完整安装包。",
    "version": "无法读取当前版本，请检查 version.json 或重新下载正式发布包。",
    "package": "更新包不完整、不兼容或校验失败，请重新下载或使用正式发布包。",
    "release": "发布信息不完整或格式不受支持，请查看发布页。",
    "installer": "更新器未能就绪，软件仍保持运行，请查看下方退出代码或日志。",
    "local": "本地文件操作失败。",
    "unknown": "发生未预期的错误。",
}


def safe_detail(error):
    # Request exceptions can contain proxy credentials or signed redirect URLs.
    # The separate source URL is sufficient for locating a failed request.
    return re.sub(r'https?://[^\s\'"<>]+', '[URL]', str(error))[:2000]


class UpdateError(ValueError):
    def __init__(self, code, detail="", **values):
        self.code = code
        self.detail = safe_detail(detail)
        self.values = values
        super().__init__(f"{code}: {self.detail}")


class UpdateNetworkError(UpdateError):
    def __init__(self, phase, attempts):
        self.phase = phase
        self.attempts = list(attempts)
        super().__init__("network", "\n".join(
            f'{source}: {error}' for source, url, error in self.attempts
        ))


def request_error(error):
    if isinstance(error, UpdateError):
        return error
    code, values = "connection", {}
    if isinstance(error, requests.exceptions.ProxyError):
        code = "proxy"
    elif isinstance(error, requests.exceptions.SSLError):
        code = "tls"
    elif isinstance(error, requests.exceptions.Timeout):
        code = "timeout"
    elif isinstance(error, requests.exceptions.HTTPError):
        response = error.response
        status = response.status_code if response is not None else "?"
        values["status"] = status
        code = "http"
        if status == 429 or (status == 403 and response.headers.get("X-RateLimit-Remaining") == "0"):
            code = "rate_limit"
        elif status == 404:
            code = "not_found"
        elif status in (401, 403):
            code = "forbidden"
        elif isinstance(status, int) and status >= 500:
            code = "server"
        detail = f"HTTP {status}"
        if response is not None and response.headers.get("Retry-After"):
            detail += "; Retry-After: " + response.headers["Retry-After"]
        return UpdateError(code, detail, **values)
    elif isinstance(error, ValueError):
        code = "metadata"
    elif "read timed out" in str(error).lower():
        # urllib3 wraps streaming read timeouts as ConnectionError.
        code = "timeout"
    elif any(token in str(error).lower() for token in (
        "getaddrinfo", "name resolution", "name or service not known", "nodename",
    )):
        code = "dns"
    return UpdateError(code, safe_detail(error), **values)


def local_error(error):
    if isinstance(error, UpdateError):
        return error
    detail = str(error)
    if isinstance(error, PermissionError):
        code = "permission"
    elif getattr(error, "errno", None) == errno.ENOSPC or "Insufficient" in detail:
        code = "space"
    elif isinstance(error, zipfile.BadZipFile):
        code = "package"
    elif isinstance(error, OSError):
        code = "local"
    elif detail.startswith(("Release", "Invalid stable version", "No stable release", "Unexpected release", "Invalid release")):
        code = "release"
    elif isinstance(error, (ValueError, KeyError, TypeError)):
        code = "package"
    else:
        code = "unknown"
    return UpdateError(code, detail)


def format_update_error(error, phase="check"):
    # Keep Qt/i18n out of the network and standalone installer modules.
    from services.i18n import t

    stages = {"check": "检查更新", "download": "下载更新", "install": "准备安装"}

    def describe(problem):
        reason = t(REASONS.get(problem.code, REASONS["unknown"])).format(**problem.values)
        if problem.detail:
            reason += "\n" + t("详细信息") + "：" + problem.detail
        return reason

    if isinstance(error, UpdateNetworkError):
        stage = t(stages[error.phase])
        lines = [t("{stage}失败，所有来源均未成功。").format(stage=stage)]
        for source, url, problem in error.attempts:
            lines.append(f"[{source}]\n" + t("请求地址") + "：" + url + "\n" + describe(problem))
    else:
        stage = t(stages.get(phase, "检查更新"))
        lines = [t("{stage}失败。").format(stage=stage), describe(local_error(error))]
    lines.append(t("可重试，或打开发布页手动下载。"))
    return "\n\n".join(lines)
