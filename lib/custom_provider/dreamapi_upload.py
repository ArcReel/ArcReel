"""DreamAPI 的按需素材上传：调用官方策略接口，将本地素材换成短期公网 URL。"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

_UPLOAD_POLICY_PATH = "/api/file/v1/get_policy"
_UPLOAD_FINISH_PATH = "/api/file/v1/policy_upload_finish"
_ALLOWED_STORAGE_SUFFIX = ".aliyuncs.com"
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


class DreamAPIUploadError(RuntimeError):
    """DreamAPI Storage 未能返回可用于模型请求的素材 URL。"""


async def upload_asset(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    file_path: Path,
) -> str:
    """按官方策略 → OSS multipart → 完成回调流程上传单个素材并返回临时 URL。

    凭证只发往配置的 DreamAPI API 主机；OSS multipart 请求只携带短时策略字段，不携带 API Key。
    """
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    file_bytes = await asyncio.to_thread(file_path.read_bytes)
    mime_type = _MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")

    policy_response = await client.post(
        f"{base}{_UPLOAD_POLICY_PATH}",
        headers=headers,
        json={"scene": "Dream-CN"},
        follow_redirects=False,
    )
    policy_data = _response_data(policy_response, "upload policy")
    host = _required_text(policy_data, "host", "upload policy")
    try:
        parsed_host = urlsplit(host)
        port = parsed_host.port
    except ValueError as exc:
        raise DreamAPIUploadError("DreamAPI storage host is not an allowed HTTPS OSS host") from exc
    hostname = (parsed_host.hostname or "").lower()
    if (
        parsed_host.scheme.lower() != "https"
        or not hostname.endswith(_ALLOWED_STORAGE_SUFFIX)
        or parsed_host.username is not None
        or parsed_host.password is not None
        or port not in {None, 443}
        or parsed_host.path not in {"", "/"}
        or parsed_host.query
        or parsed_host.fragment
    ):
        raise DreamAPIUploadError("DreamAPI storage host is not an allowed HTTPS OSS host")

    form_fields = [
        ("policy", _required_text(policy_data, "policy", "upload policy")),
        ("OSSAccessKeyId", _required_text(policy_data, "OSSAccessKeyId", "upload policy")),
        ("success_action_status", "200"),
        ("signature", _required_text(policy_data, "signature", "upload policy")),
        ("key", _required_text(policy_data, "key", "upload policy")),
        ("callback", _required_text(policy_data, "callback", "upload policy")),
    ]
    boundary = f"----ArcReelDreamAPI{uuid.uuid4().hex}"
    multipart_parts = [_multipart_field(boundary, name, value) for name, value in form_fields]
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", file_path.name) or "asset"
    multipart_parts.extend(
        [
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("ascii"),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ]
    )
    upload_response = await client.post(
        host,
        content=b"".join(multipart_parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        follow_redirects=False,
    )
    upload_data = _response_data(upload_response, "file upload")
    request_id = _required_text(upload_data, "reqId", "file upload")

    finish_response = await client.post(
        f"{base}{_UPLOAD_FINISH_PATH}",
        headers=headers,
        json={"reqId": request_id},
        follow_redirects=False,
    )
    finish_data = _response_data(finish_response, "upload completion")
    result_url = _required_text(finish_data, "url", "upload completion")
    result = urlsplit(result_url)
    if result.scheme != "https" or not result.hostname:
        raise DreamAPIUploadError("DreamAPI returned an invalid uploaded asset URL")
    return result_url


def _response_data(response: httpx.Response, phase: str) -> dict[str, object]:
    if response.is_error or response.is_redirect:
        raise DreamAPIUploadError(f"DreamAPI {phase} request failed with HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise DreamAPIUploadError(f"DreamAPI {phase} response was not valid JSON") from exc
    if not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("data"), dict):
        raise DreamAPIUploadError(f"DreamAPI {phase} response did not contain a successful data object")
    return body["data"]


def _required_text(data: dict[str, object], key: str, phase: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise DreamAPIUploadError(f"DreamAPI {phase} response omitted {key}")
    return value


def _multipart_field(boundary: str, name: str, value: str) -> bytes:
    return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
