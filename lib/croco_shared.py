"""Croco GPU 共享工具模块（统一任务协议客户端）。

供 image_backends / video_backends / audio_backends 复用。Croco GPU 是自建"GPU 视频调度中枢"，
用单一 `/v1` base + Bearer 单 token 鉴权，按模型合同（model_id + operation + contract_version）
统一提交任务、轮询状态、下载产物（见 Croco 中枢 API 文档 0.4.0）。

- CROCO_BASE_URL — 默认 base（含 https://8.137.116.27:5888）
- resolve_croco_token — Token 解析（缺失即 raise，不走 env fallback）
- croco_base_url — 归一化 base（去末尾斜杠，容忍 host-only）
- croco_headers — Bearer 鉴权头
- CrocoClient — 统一任务客户端（素材上传 / 提交 / 轮询 / 产物 / 下载）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async

logger = logging.getLogger(__name__)


def _should_retry(exc: Exception) -> bool:
    """Croco 统一任务重试判断：5xx/429 或网络错误重试，4xx 快速失败。

    Croco 提交带 Idempotency-Key 幂等，重复提交返回原 job_id（replayed=true）不会重复建任务，
    故不需要 submit_post 的歧义传输保护，直接用状态码分流即可。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    if isinstance(exc, httpx.TransportError):
        return True
    return False


# 默认 base；用户可经配置覆盖 base_url 指向自建中转。
CROCO_BASE_URL = "https://8.137.116.27:5888"

_JOBS_ENDPOINT = "/api/v2/jobs"
_ASSETS_ENDPOINT_TMPL = "/api/v2/assets/{kind}"

# 素材上传允许的 MIME（按扩展名映射，见 Croco 中枢 API 文档 4.1）。
_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_AUDIO_MIME_BY_SUFFIX = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}

# 统一任务终态：blocked / succeeded / failed / canceled。
_TERMINAL_STATUSES = frozenset({"blocked", "succeeded", "failed", "canceled"})

# 轮询参数：前 2 分钟每 2 秒，之后每 5 秒（中枢文档推荐）。
_FAST_POLL_SECONDS = 2.0
_FAST_POLL_WINDOW_SECONDS = 120.0
_SLOW_POLL_SECONDS = 5.0
_CANCEL_RETRY_SECONDS = 1.0
_CANCEL_MAX_ATTEMPTS = 10

JobUpdateCallback = Callable[[dict, dict | None], Awaitable[None]]


def resolve_croco_token(token: str | None = None) -> str:
    """Bearer Token 解析：缺失即 raise，不走 env fallback。"""
    if token is None or not token.strip():
        raise ValueError("请到系统配置页填写 Croco GPU Token")
    return token.strip()


def croco_base_url(configured: str | None = None) -> str:
    """归一化 base：去末尾斜杠，容忍用户填 host 或带 scheme。"""
    base = ((configured or "").strip() or CROCO_BASE_URL).rstrip("/")
    return base


def croco_headers(token: str) -> dict[str, str]:
    """Bearer 鉴权头。复用 resolve_croco_token 校验空 token。"""
    token = resolve_croco_token(token)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _extract_asset_id(payload: dict) -> str | None:
    """从素材上传响应取 asset_id（或等价的 id）。"""
    for key in ("asset_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class CrocoClient:
    """Croco 统一任务客户端。所有模型（H3 / Image / Music / FlashVSR）共用同一套提交-轮询-下载。"""

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._token = resolve_croco_token(token)
        self._base_url = croco_base_url(base_url)
        self._http_timeout = http_timeout

    async def upload_asset(self, kind: str, asset_path: Path) -> str:
        """上传本地素材（multipart file），返回 asset_id。kind 为 images 或 audio。"""

        def _build_file() -> tuple[str, bytes, str]:
            data = asset_path.read_bytes()
            suffix = asset_path.suffix.lower()
            if kind == "images":
                media_type = _IMAGE_MIME_BY_SUFFIX.get(suffix, "image/png")
            else:
                media_type = _AUDIO_MIME_BY_SUFFIX.get(suffix, "audio/mpeg")
            return asset_path.name, data, media_type

        filename, data, media_type = await asyncio.to_thread(_build_file)
        files = {"file": (filename, data, media_type)}
        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{self._base_url}{_ASSETS_ENDPOINT_TMPL.format(kind=kind)}"

        @with_retry_async(retry_if=_should_retry)
        async def _post() -> dict:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                resp = await client.post(url, headers=headers, files=files)
                resp.raise_for_status()
                return resp.json()

        data_json = await _post()
        asset_id = _extract_asset_id(data_json)
        if asset_id is None:
            raise RuntimeError("Croco 素材上传响应缺少 asset_id")
        return asset_id

    async def upload_image(self, image_path: Path) -> str:
        """上传本地图片，返回 asset_id。"""
        return await self.upload_asset("images", image_path)

    async def upload_audio(self, audio_path: Path) -> str:
        """上传本地音频，返回 asset_id。"""
        return await self.upload_asset("audio", audio_path)

    async def submit_job(
        self,
        *,
        model_id: str,
        operation: str,
        contract_version: str,
        parameters: dict,
        inputs: list[dict] | None = None,
        client_job_id: str | None = None,
    ) -> dict:
        """提交统一任务，返回任务投影（含 job_id）。"""
        cjid = client_job_id or uuid.uuid4().hex
        body = {
            "model_id": model_id,
            "operation": operation,
            "contract_version": contract_version,
            "client_job_id": cjid,
            "parameters": parameters,
            "inputs": inputs or [],
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Idempotency-Key": cjid,
        }
        url = f"{self._base_url}{_JOBS_ENDPOINT}"

        @with_retry_async(retry_if=_should_retry)
        async def _post() -> dict:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

        return await _post()

    async def get_job(self, job_id: str) -> dict:
        """查询统一任务状态。"""
        url = f"{self._base_url}{_JOBS_ENDPOINT}/{job_id}"
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {self._token}"})
            resp.raise_for_status()
            return resp.json()

    async def get_queue_position(self, job_id: str) -> dict | None:
        """Return the 1-based GPU queue position while the job is queued.

        A status transition can race this endpoint; 404/409 means the queue
        projection is no longer applicable and is therefore returned as None.
        """
        url = f"{self._base_url}{_JOBS_ENDPOINT}/{job_id}/queue-position"
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {self._token}"})
            if resp.status_code in {404, 409}:
                return None
            resp.raise_for_status()
            return resp.json()

    async def cancel_job(self, job_id: str) -> dict:
        """Request remote cancellation, tolerating short lifecycle transition races."""
        url = f"{self._base_url}{_JOBS_ENDPOINT}/{job_id}/cancel"
        headers = {"Authorization": f"Bearer {self._token}"}
        last_job: dict = {}
        for attempt in range(_CANCEL_MAX_ATTEMPTS):
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                resp = await client.post(url, headers=headers)
            if resp.status_code not in {409, 423}:
                resp.raise_for_status()
                return resp.json()
            last_job = await self.get_job(job_id)
            if last_job.get("status") in _TERMINAL_STATUSES:
                return last_job
            if attempt + 1 < _CANCEL_MAX_ATTEMPTS:
                await asyncio.sleep(_CANCEL_RETRY_SECONDS)
        raise RuntimeError(f"Croco 任务 {job_id} 暂时不可取消（最后状态 {last_job.get('status', 'unknown')}）")

    async def wait_until_terminal(
        self,
        job_id: str,
        *,
        max_wait_seconds: float = 1800.0,
        on_update: JobUpdateCallback | None = None,
    ) -> dict:
        """轮询直到终态，返回终态任务投影。超时抛 TimeoutError。"""
        import time

        started = time.monotonic()
        while True:
            job = await self.get_job(job_id)
            status = job.get("status")
            queue = None
            if status == "queued":
                try:
                    queue = await self.get_queue_position(job_id)
                except Exception:
                    # Queue position is display metadata; a transient failure must not
                    # abort a paid generation whose lifecycle endpoint is healthy.
                    logger.warning("Croco queue position unavailable job_id=%s", job_id, exc_info=True)
            if on_update is not None:
                await on_update(job, queue)
            if status in _TERMINAL_STATUSES:
                return job
            if time.monotonic() - started > max_wait_seconds:
                raise TimeoutError(f"Croco 任务 {job_id} 轮询超时（最后状态 {status}）")
            elapsed = time.monotonic() - started
            interval = _FAST_POLL_SECONDS if elapsed < _FAST_POLL_WINDOW_SECONDS else _SLOW_POLL_SECONDS
            await asyncio.sleep(interval)

    async def list_outputs(self, job_id: str) -> dict:
        """查询产物描述符列表。"""
        url = f"{self._base_url}{_JOBS_ENDPOINT}/{job_id}/outputs"
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {self._token}"})
            resp.raise_for_status()
            return resp.json()

    async def download_output(self, job_id: str, output_id: str, output_path: Path) -> None:
        """下载产物到本地文件（幂等 GET）。"""
        url = f"{self._base_url}{_JOBS_ENDPOINT}/{job_id}/outputs/{output_id}/content"

        @with_retry_async(
            max_attempts=DOWNLOAD_MAX_ATTEMPTS,
            backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
            retry_if=_should_retry,
        )
        async def _download() -> None:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {self._token}"})
                resp.raise_for_status()
                if not resp.content:
                    raise RuntimeError(f"Croco 产物下载返回空内容: {output_id}")

                def _save() -> None:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(resp.content)

                await asyncio.to_thread(_save)

        await _download()
