"""Higgsfield 共享工具模块。

供 image_backends / video_backends / providers router 复用。包含：
- HIGGSFIELD_BASE_URL — Higgsfield API 基础 URL（``https://api.higgsfield.ai``）
- resolve_higgsfield_credentials — Key ID + Key Secret 解析（缺失即 raise）
- create_higgsfield_client — 带 ``Authorization: Key <id>:<secret>`` 头、经出站目的地校验的 httpx 客户端
- upload_image — 本地图片经预签名地址上传，换回供应商接受的公开 URL（Higgsfield 的素材字段只收 URL）
- fetch_request_status / is_request_done / request_failure_reason / extract_* — 请求状态轮询工具
- HIGGSFIELD_RETRYABLE_ERRORS — 瞬态错误集合（仅 NetworkError / TimeoutException）
- safe_body_for_log — 日志输出白名单视图
- test_higgsfield_connection — 连接测试（供 providers router 调用）

官方文档索引见 ``docs/api-docs/providers/higgsfield.md``。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from pathlib import Path

import httpx

from lib.backends.artifact_download_guard import artifact_http_client, check_destination
from lib.backends.data_uri import image_mime_from_bytes
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.transport_layer import TransportLayer
from lib.infra.retry import BASE_RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

HIGGSFIELD_BASE_URL = "https://api.higgsfield.ai"

# 仅重试瞬态网络错误：401/404 等业务错误重试到超时只会白占配额。
HIGGSFIELD_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    *BASE_RETRYABLE_ERRORS,
    httpx.NetworkError,
    httpx.TimeoutException,
)

# 文件上传接口接受的图片 MIME（docs/concepts/file-uploads）。
_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_UPLOAD_URL_PATH = "/files/generate-upload-url"
_STATUS_PATH = "/requests/{request_id}/status"

# 终态状态（docs/concepts/requests）：completed 成功；failed / nsfw 不计费；canceled 只在排队期可达。
_DONE_STATUS = "completed"
_FAILED_STATUSES = frozenset({"failed", "nsfw", "canceled"})


def resolve_higgsfield_credentials(access_key: str | None, secret_key: str | None) -> tuple[str, str]:
    """Key ID 与 Key Secret 都要有，缺一即 raise；两者在控制台 ``console.higgsfield.ai`` 成对签发。"""
    if not access_key or not access_key.strip() or not secret_key or not secret_key.strip():
        raise ValueError("请到系统配置页填写 Higgsfield 的 Key ID 与 Key Secret")
    return access_key.strip(), secret_key.strip()


def higgsfield_auth_headers(access_key: str, secret_key: str) -> dict[str, str]:
    return {"Authorization": f"Key {access_key}:{secret_key}"}


def create_higgsfield_client(
    *,
    access_key: str | None = None,
    secret_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
    transport_layer: TransportLayer | None = None,
) -> httpx.AsyncClient:
    """创建带鉴权头、经出站目的地校验的 ``httpx.AsyncClient``。"""
    key_id, key_secret = resolve_higgsfield_credentials(access_key, secret_key)
    return artifact_http_client(
        base_url=(base_url or HIGGSFIELD_BASE_URL).rstrip("/"),
        headers={**higgsfield_auth_headers(key_id, key_secret), "Content-Type": "application/json"},
        timeout=timeout,
        transport_layer=transport_layer,
    )


def new_idempotency_key() -> str:
    """每次提交一把幂等键：连接建立失败重发同一请求时复用，供应商据此不重复建任务（docs/concepts/errors）。"""
    return str(uuid.uuid4())


def image_content_type(path: Path, head: bytes) -> str:
    """上传声明的 MIME：先按文件头识别，认不出再按扩展名；都认不出按 PNG 处理。"""
    return image_mime_from_bytes(head) or _IMAGE_MIME_TYPES.get(path.suffix.lower(), "image/png")


async def upload_image(client: httpx.AsyncClient, path: Path, *, timeout: float = 120.0) -> str:  # noqa: ASYNC109 -- 转交 httpx 的 I/O 超时配置，非 async 取消 deadline
    """把本地图片送上 Higgsfield 的文件存储，返回可作为模型输入的公开 URL。

    两步：``POST /files/generate-upload-url`` 取预签名地址，再 ``PUT`` 文件本体。上传走不带鉴权头的
    独立客户端——官方明确要求不要把 API 凭证送到预签名存储地址。读文件是阻塞 I/O，卸载到线程。
    """
    content = await asyncio.to_thread(path.read_bytes)
    content_type = image_content_type(path, content[:16])
    resp = await client.post(_UPLOAD_URL_PATH, json={"content_type": content_type})
    raise_for_status_redacted(resp)
    ticket = resp.json()
    upload_url = ticket.get("upload_url")
    public_url = ticket.get("public_url")
    if not upload_url or not public_url:
        raise RuntimeError(f"Higgsfield 上传票据缺少 upload_url / public_url: {sorted(ticket)}")
    headers = {str(k): str(v) for k, v in (ticket.get("upload_headers") or {}).items()}
    headers.setdefault("Content-Type", content_type)
    async with artifact_http_client(timeout=timeout) as storage:
        put = await storage.put(upload_url, content=content, headers=headers)
        raise_for_status_redacted(put)
    return str(public_url)


async def fetch_request_status(client: httpx.AsyncClient, request_id: str) -> dict:
    """查询请求状态（``GET /requests/{id}/status``）。"""
    resp = await client.get(_STATUS_PATH.format(request_id=request_id))
    raise_for_status_redacted(resp)
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Higgsfield 状态响应不是对象: {type(data).__name__}")
    return data


def is_request_done(payload: Mapping[str, object]) -> bool:
    return payload.get("status") == _DONE_STATUS


def request_failure_reason(payload: Mapping[str, object]) -> str | None:
    status = payload.get("status")
    if status in _FAILED_STATUSES:
        error = payload.get("error") or "unknown"
        return f"Higgsfield 请求终态 {status}: {error}"
    return None


def extract_request_id(payload: Mapping[str, object]) -> str:
    request_id = payload.get("request_id")
    if not request_id:
        raise RuntimeError(f"Higgsfield 提交响应缺少 request_id: {sorted(payload)}")
    return str(request_id)


def extract_image_urls(payload: Mapping[str, object]) -> list[str]:
    """completed 响应里的 ``images[].url``；为空由调用方按「成功却无产物」处理。"""
    images = payload.get("images") or []
    if not isinstance(images, list):
        return []
    return [str(item["url"]) for item in images if isinstance(item, Mapping) and item.get("url")]


def extract_video_url(payload: Mapping[str, object]) -> str:
    video = payload.get("video")
    url = video.get("url") if isinstance(video, Mapping) else None
    if not url:
        raise RuntimeError(f"Higgsfield 请求 {payload.get('request_id')} 已完成但没有 video.url")
    return str(url)


# 日志白名单：素材 URL 是预签名地址、prompt 可能很长，只保留结构性参数。
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {
        "aspect_ratio",
        "resolution",
        "duration",
        "mode",
        "sound",
        "generate_audio",
        "seed",
        "batch_size",
        "enhance_prompt",
        "prompt_extend",
        "enable_thinking",
        "bitrate_mode",
        "multi_shots",
    }
)
_URL_LIST_KEYS = ("image_urls", "video_urls", "audio_urls")
_URL_KEYS = ("image_url", "last_image_url", "end_image_url", "first_frame_url", "last_frame_url")


def safe_body_for_log(body: Mapping[str, object]) -> dict:
    """仅保留白名单字段 + prompt 截断 + 素材只计数。"""
    view: dict = {k: body[k] for k in _SAFE_LOG_KEYS if k in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt"] = prompt[:120] + ("…" if len(prompt) > 120 else "")
    for key in _URL_LIST_KEYS:
        items = body.get(key)
        if isinstance(items, list) and items:
            view[key] = f"<{len(items)} url>"
    for key in _URL_KEYS:
        if body.get(key):
            view[key] = "<url>"
    return view


def test_higgsfield_connection(config: Mapping[str, str | None]) -> None:
    """用一个随机的 request_id 查状态来验证凭证：凭证有效时 404（请求不存在），401/403 为凭证无效。

    其他状态码（含 5xx）一律视为不可判定，抛错避免误判成功。``base_url`` 是管理员可配的地址，与调用通道
    同走一道出站目的地校验；本函数在路由的工作线程里同步执行，没有运行中的事件循环，可直接 ``asyncio.run``。
    """
    key_id, key_secret = resolve_higgsfield_credentials(config.get("access_key"), config.get("secret_key"))
    base_url = (config.get("base_url") or HIGGSFIELD_BASE_URL).rstrip("/")
    url = f"{base_url}{_STATUS_PATH.format(request_id=uuid.uuid4())}"
    asyncio.run(check_destination(httpx.URL(url)))
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url, headers=higgsfield_auth_headers(key_id, key_secret))
    if resp.status_code == 404:
        return
    if resp.status_code in (401, 403):
        raise RuntimeError(f"HTTP {resp.status_code}: 凭证无效")
    raise RuntimeError(f"HTTP {resp.status_code}: 连接测试无法判定")
