"""KlingBackendBase: shared scaffolding for Kling image/video backends.

Centralize duplicated assembly: authentication headers, ``__init__`` credential dispatch/base_url
normalization/common fields, submit/poll HTTP retry scaffolding. Subclasses retain per-medium paths,
payloads, capability projections, image ``api_model_name`` decoupling, and video subpaths/resume/download
through overridable methods/constructor parameters.

JWT primitives live in ``lib.backends.kling_shared``: ``KlingJWTManager`` / ``kling_bearer_headers`` /
``resolve_kling_*`` / ``KLING_BASE_URL``. This base assembles them only; submit/poll helpers reuse
``lib.backends.backend_runtime`` ``submit_post`` / ``poll_with_retry`` and retry predicates.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar

import httpx

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.backend_runtime import (
    VIDEO_POLL_INTERVAL_SECONDS,
    poll_with_retry,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.kling_shared import (
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
from lib.backends.providers import PROVIDER_KLING
from lib.backends.transport_layer import TransportLayer
from lib.config.url_utils import normalize_base_url
from lib.infra.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    with_retry_async,
)

logger = logging.getLogger(__name__)


class KlingBackendBase:
    """Shared Kling backend base: JWT / Bearer authentication with asynchronous submit/poll scaffolding.

    Two modes:
    - ``auth_mode="jwt"`` built-ins take access_key + secret_key through ``KlingJWTManager``, checking
      every HTTP call and resigning under 60 seconds before expiry; async renders can outlive tokens.
    - ``auth_mode="bearer"`` custom endpoints take static api_key + base_url, bypassing JWT management.

    Subclasses pass ``model`` already defaulted to their ``DEFAULT_MODEL`` through ``super().__init__``,
    then finish per-medium assembly: image ``api_model_name`` / static capabilities, video capability lookup.
    """

    # Subclass-defined image/video media nouns used only for operator progress logs, without behavioral effects.
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
        transport_layer: TransportLayer | None = None,
    ) -> None:
        self._auth_mode = auth_mode
        self._model = model
        self._base_url = (normalize_base_url(base_url) or KLING_BASE_URL).rstrip("/")
        self._http_timeout = http_timeout
        self._transport_layer = transport_layer

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
        """Authentication headers: jwt checks expiry/resigns each call as needed; bearer uses static keys."""
        if self._jwt is not None:
            return self._jwt.auth_headers()
        assert self._static_api_key is not None
        return kling_bearer_headers(self._static_api_key)

    # Response interpretation, overridden by subclasses for their API design standards.
    # Defaults to legacy ``model_name`` envelopes: ``data.task_id``, ``data.task_status``, terminal ``succeed``.

    def _task_id_from(self, submit_payload: dict) -> str:
        return extract_kling_task_id(submit_payload)

    def _task_status(self, payload: dict) -> str | None:
        return kling_task_status(payload)

    def _is_terminal(self, payload: dict) -> bool:
        return is_kling_task_terminal(payload)

    def _failure_reason(self, payload: dict) -> str | None:
        return kling_task_failure_reason(payload)

    # HTTP submit / poll scaffolding.

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _submit_task(
        self,
        client: httpx.AsyncClient,
        endpoint_path: str,
        payload: dict,
        request: VideoGenerationRequest | None = None,
    ) -> str:
        # Nonidempotent billed task-creation POST: submit_post terminally fails ambiguous transport as AmbiguousSubmitError,
        # preventing duplicate tasks/charges. >=400 raises HTTPStatusError for should_retry_submit status routing.
        # Video channels alone supply ``request``; image channels retain no provider-response snapshots.
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}/{endpoint_path}",
                json=payload,
                headers=self._headers(),
            ),
            provider=PROVIDER_KLING,
            request=request,
        )
        return self._task_id_from(resp.json())

    async def _poll_query(self, client: httpx.AsyncClient, endpoint_path: str, *, base_url: str | None = None) -> dict:
        """Query task states; default ``base_url`` uses current configured domains, overridden by callers replaying submission domains."""
        resp = await client.get(
            f"{(base_url or self._base_url).rstrip('/')}/{endpoint_path}",
            headers=self._headers(),
        )
        raise_for_status_redacted(resp)
        return resp.json()

    async def _poll_until_terminal(
        self,
        poll_fn: Callable[[], Awaitable[dict]],
        *,
        max_wait: float,
        poll_interval: float = VIDEO_POLL_INTERVAL_SECONDS,
    ) -> dict:
        """Poll to succeed/failed via base.poll_with_retry, injecting Kling terminal/failure/retry predicates."""
        return await poll_with_retry(
            poll_fn=poll_fn,
            is_done=self._is_terminal,
            is_failed=self._failure_reason,
            poll_interval=poll_interval,
            max_wait=max_wait,
            retry_if=should_retry_poll,
            label="Kling",
            on_progress=lambda v, elapsed: logger.info(
                "Kling %s生成中... status=%s elapsed=%ds",
                self._media_label,
                self._task_status(v),
                int(elapsed),
            ),
        )
