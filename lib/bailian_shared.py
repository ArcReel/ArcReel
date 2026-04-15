"""百炼供应商共享工具 — 文件上传、重试策略等。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

# 百炼可重试错误码
BAILIAN_RETRYABLE_ERRORS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)

# 默认 DashScope 端点
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"


async def get_upload_policy(api_key: str, model_name: str, base_url: str | None = None) -> dict[str, Any]:
    """获取文件上传凭证。

    Args:
        api_key: DashScope API Key
        model_name: 模型名称（用于获取对应的上传策略）
        base_url: 可选的自定义 base URL

    Returns:
        包含上传凭证的字典
    """
    url = f"{base_url or DEFAULT_DASHSCOPE_BASE_URL}/api/v1/uploads"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    params = {"action": "getPolicy", "model": model_name}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        return data["data"]


@with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8), retryable_errors=BAILIAN_RETRYABLE_ERRORS)
async def upload_file_to_oss(policy_data: dict[str, Any], file_path: str | Path) -> str:
    """将本地文件上传到百炼临时 OSS 存储。

    Args:
        policy_data: 从 get_upload_policy 获取的上传凭证
        file_path: 本地文件路径

    Returns:
        oss:// 格式的临时 URL
    """
    file_path = Path(file_path)
    file_name = file_path.name
    key = f"{policy_data['upload_dir']}/{file_name}"

    with open(file_path, "rb") as f:
        files = {
            "OSSAccessKeyId": (None, policy_data["oss_access_key_id"]),
            "Signature": (None, policy_data["signature"]),
            "policy": (None, policy_data["policy"]),
            "x-oss-object-acl": (None, policy_data["x_oss_object_acl"]),
            "x-oss-forbid-overwrite": (None, policy_data["x_oss_forbid_overwrite"]),
            "key": (None, key),
            "success_action_status": (None, "200"),
            "file": (file_name, f),
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(policy_data["upload_host"], files=files, timeout=60.0)
            response.raise_for_status()

    return f"oss://{key}"


async def upload_file_and_get_url(
    api_key: str, model_name: str, file_path: str | Path, base_url: str | None = None
) -> str:
    """上传本地文件并返回 oss:// URL（完整流程）。

    Args:
        api_key: DashScope API Key
        model_name: 模型名称
        file_path: 本地文件路径
        base_url: 可选的自定义 base URL

    Returns:
        oss:// 格式的临时 URL
    """
    policy_data = await get_upload_policy(api_key, model_name, base_url)
    oss_url = await upload_file_to_oss(policy_data, file_path)
    logger.info(f"文件上传成功: {file_path} -> {oss_url}")
    return oss_url
