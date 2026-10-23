"""万万工作台（coolhs）的共享词汇：默认地址、请求头与素材 MIME 推断。

单独成文件是为了让供应商注册表（``lib.config.registry``）只依赖这两个常量，不必把
视频后端的 HTTP 与轮询依赖一并拉进 import 图。
"""

from __future__ import annotations

from pathlib import Path

#: 平台根地址；用户可在供应商配置里覆盖（``base_url`` 为同名 optional key）。
COOLHS_BASE_URL = "https://wan.coolhs.com"

#: 平台公网域名前置 Cloudflare：不带浏览器标识的 UA 会被 1010 拒绝，Python/httpx 的默认
#: 标识一律不放行。这个头是硬要求。
COOLHS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: 素材扩展名 → MIME。平台按素材类型拦规格（图片 JPEG/PNG/BMP/WebP、视频 mp4/mov、音频
#: mp3/wav），MIME 猜错会在预签名阶段就被拒，故按扩展名显式给。
_MIME_BY_SUFFIX: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}

#: 扩展名不认识时的兜底（按素材类型给最常见的那个）。
_MIME_BY_KIND: dict[str, str] = {
    "image": "image/png",
    "video": "video/mp4",
    "audio": "audio/mpeg",
}


def coolhs_mime(kind: str, path: Path) -> str:
    """素材的 Content-Type：先认扩展名，再按素材类型兜底。"""
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), _MIME_BY_KIND.get(kind, "application/octet-stream"))


def coolhs_headers(api_key: str) -> dict[str, str]:
    """平台要求的请求头：卡密 + 浏览器 UA。

    卡密走 ``X-Wan-Card``（官方文档明确不要塞进 URL 或查询参数）；``Accept`` 显式要 JSON，
    免得错误分支回一段 HTML、让解析报一个看不懂的错。
    """
    return {
        "X-Wan-Card": api_key,
        "User-Agent": COOLHS_USER_AGENT,
        "Accept": "application/json",
    }
