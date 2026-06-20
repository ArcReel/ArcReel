"""KlingBackendBase — 可灵 Kling 图像/视频后端共享脚手架基类。

收口两个媒体后端类层逐字重复的装配：鉴权头、``__init__`` 凭证分派 + base_url 归一化 + 通用字段
赋值、submit/poll 的 HTTP retry 骨架。per-medium 差异（端点路径、payload 构建、capability 投影、
image 的 ``api_model_name`` 解耦、video 的子路径/resume/download）以可重写方法 / 构造参数留给子类。

JWT 加密原语在 ``lib.kling_shared`` 共享（``KlingJWTManager`` / ``kling_bearer_headers`` /
``resolve_kling_*`` / ``KLING_BASE_URL``），本基类只把它们装配成后端脚手架；submit/poll helpers
复用 ``lib.video_backends.base``（``submit_post`` / ``poll_with_retry`` 及重试谓词）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar

import httpx

from lib.config.url_utils import normalize_base_url
from lib.kling_shared import (
    KLING_BASE_URL,
    KlingJWTManager,
    extract_kling_task_id,
    is_kling_task_terminal,
    kling_bearer_headers,
    kling_task_failure_reason,
    kling_task_status,
    resolve_kling_api_key,
    resolve_kling_jwt_credentials,
)
from lib.providers import PROVIDER_KLING
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    poll_with_retry,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)


class KlingBackendBase:
    """可灵 Kling 后端共享基类（JWT / Bearer 双模式鉴权，异步提交-轮询骨架）。

    双模式：
    - ``auth_mode="jwt"``（内置 provider）：接 access_key + secret_key，走 ``KlingJWTManager``，
      每次 HTTP 调用前检查过期、距过期 <60s 按需重签——异步渲染可能超单 token 寿命。
    - ``auth_mode="bearer"``（自定义 endpoint）：接静态 api_key + base_url，旁路 JWT 管理器。

    子类经 ``super().__init__`` 传入已回落各自 ``DEFAULT_MODEL`` 的 ``model``，再做 per-medium 尾部
    装配（image 的 ``api_model_name`` 与静态 capability 集、video 的能力位查表）。
    """

    # 进度日志的媒体名词（"图像" / "视频"），由子类声明；仅用于 operator 日志，不影响行为。
    _media_label: ClassVar[str] = ""

    def __init__(
        self,
        *,
        auth_mode: str,
        access_key: str | None,
        secret_key: str | None,
        api_key: str | None,
        model: str,
        base_url: str | None,
        http_timeout: float,
    ) -> None:
        self._auth_mode = auth_mode
        self._model = model
        self._base_url = (normalize_base_url(base_url) or KLING_BASE_URL).rstrip("/")
        self._http_timeout = http_timeout

        if auth_mode == "jwt":
            ak, sk = resolve_kling_jwt_credentials(access_key, secret_key)
            self._jwt: KlingJWTManager | None = KlingJWTManager(ak, sk)
            self._static_api_key: str | None = None
        elif auth_mode == "bearer":
            self._jwt = None
            self._static_api_key = resolve_kling_api_key(api_key)
        else:
            raise ValueError(f"未知 Kling auth_mode: {auth_mode}")

    @property
    def name(self) -> str:
        return PROVIDER_KLING

    @property
    def model(self) -> str:
        return self._model

    # ── auth ────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """鉴权头：jwt 模式每次调用触发过期检查 + 按需重签；bearer 模式用静态 key。"""
        if self._jwt is not None:
            return self._jwt.auth_headers()
        assert self._static_api_key is not None
        return kling_bearer_headers(self._static_api_key)

    # ── HTTP submit / poll 骨架 ─────────────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _submit_task(self, client: httpx.AsyncClient, endpoint_path: str, payload: dict) -> str:
        # 非幂等「建任务 + 计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败，
        # 避免重试重复建任务 + 重复计费；>=400 抛 HTTPStatusError 交 should_retry_submit 按状态码分流。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}/{endpoint_path}",
                json=payload,
                headers=self._headers(),
            ),
            provider=PROVIDER_KLING,
        )
        return extract_kling_task_id(resp.json())

    async def _poll_query(self, client: httpx.AsyncClient, endpoint_path: str) -> dict:
        resp = await client.get(
            f"{self._base_url}/{endpoint_path}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_until_terminal(
        self,
        poll_fn: Callable[[], Awaitable[dict]],
        *,
        poll_interval: float,
        max_wait: float,
    ) -> dict:
        """轮询至终态（succeed/failed）：复用 base.poll_with_retry，注入 Kling 终态/失败/重试谓词。"""
        return await poll_with_retry(
            poll_fn=poll_fn,
            is_done=is_kling_task_terminal,
            is_failed=kling_task_failure_reason,
            poll_interval=poll_interval,
            max_wait=max_wait,
            retry_if=should_retry_poll,
            label="Kling",
            on_progress=lambda v, elapsed: logger.info(
                "Kling %s生成中... status=%s elapsed=%ds",
                self._media_label,
                kling_task_status(v),
                int(elapsed),
            ),
        )
