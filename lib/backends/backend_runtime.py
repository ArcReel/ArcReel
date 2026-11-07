"""Channel runtime support: submission/polling, retry predicates, credential-scoped requests, artifact persistence, provider response evidence,
and provider task-id persistence.

Shared by video/image/audio channels and custom-provider backends. Video contracts live in
:mod:`arcreel_market_core.video_backend_contract`; image/audio contracts in their channel ``base`` modules. Image/audio
channels do not load video packages through this module: video requests appear only in type annotations, video-capability exceptions only inside video-specific functions.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy.exc import InterfaceError, OperationalError

from lib.backends.artifact_download_guard import (
    VIDEO_ARTIFACT_MAX_BYTES,
    artifact_http_client,
    buffered_error_response,
    stream_body_to_file,
)
from lib.backends.data_uri import file_to_data_uri
from lib.backends.http_status_errors import (
    AmbiguousSubmitError,
    ArtifactDownloadError,
    provider_rejected_error,
    redacted_status_error,
)
from lib.infra.async_thread import run_noninterruptible_async
from lib.infra.logging_utils import redact_diagnostic_text, sanitize_diagnostic_payload
from lib.infra.mp4_faststart import FASTSTART_SUFFIXES, ensure_faststart
from lib.infra.retry import (
    BASE_RETRYABLE_ERRORS,
    AsyncClock,
    NonRetryableError,
    resolve_clock,
    should_retry,
    with_retry_async,
)

if TYPE_CHECKING:
    from arcreel_market_core.video_backend_contract import ProviderResponseStage, VideoGenerationRequest

# `should_retry` defaults to substring fallback such as "timeout"/"503",
# but persistence retries strictly mean DB transients: business exceptions like
# `ValueError("Connection timed out: rate")` must not be swallowed by matching substrings.
# Explicit `retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS)` disables fallback.

logger = logging.getLogger(__name__)

VIDEO_POLL_INTERVAL_SECONDS = 5.0
VIDEO_POLL_MAX_CONSECUTIVE_FAILURES = 10
VIDEO_POLL_MAX_BACKOFF_SECONDS = 60.0


# DB transients: sqlite "database is locked", pg "could not connect" / closed connections.
# Deliberately exclude DBAPIError parents, which catch nontransient IntegrityError/DataError/ProgrammingError
# such as SQL syntax/constraint errors: retries are pointless and delay fail-fast.
_PERSIST_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,
    InterfaceError,
    ConnectionError,
    TimeoutError,
)
_PERSIST_BACKOFF_SECONDS: tuple[int, ...] = (1, 2, 4)


class ProviderJobIdStore(Protocol):
    """Provider task-id persistence entry: write protocol identifiers/actual request hosts atomically with job_id."""

    async def persist_provider_job_id(
        self, task_id: str, job_id: str, *, endpoint: str | None = None, base_url: str | None = None
    ) -> None: ...


_provider_job_id_store: Callable[[], ProviderJobIdStore] | None = None


def install_provider_job_id_store(get_store: Callable[[], ProviderJobIdStore]) -> None:
    """Register store access, installed by generation queues during module loading.

    Upper layers inject stores instead of imports here: queues reach declarative runtimes through configuration resolution;
    direct runtime-support imports would drag every channel, including ComfyUI packages, through that dependency chain. Call
    ``get_store`` for every write without caching returned instances.
    """
    global _provider_job_id_store
    _provider_job_id_store = get_store


@with_retry_async(
    max_attempts=3,
    backoff_seconds=_PERSIST_BACKOFF_SECONDS,
    retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS),
)
async def _persist_with_retry(task_id: str, job_id: str, endpoint: str | None, base_url: str | None) -> None:
    if _provider_job_id_store is None:
        raise RuntimeError("provider_job_id store not installed: lib.generation.generation_queue was never imported")
    await _provider_job_id_store().persist_provider_job_id(task_id, job_id, endpoint=endpoint, base_url=base_url)


async def persist_provider_job_id(
    task_id: str,
    job_id: str,
    *,
    provider: str,
    endpoint: str | None = None,
    base_url: str | None = None,
) -> None:
    """Call immediately after submission: persist job_id to DB for restart resume.

    Callers explicitly pass task_id. ``endpoint`` identifies protocols (custom providers only, recording the protocol used
    for this vendor task); ``base_url`` is the actual request host (connection dimension shared by both provider types, replayed
    on resume). Persist both with job_id in one write. Retry DB transients at most 3 times; raise business exceptions immediately.
    Exhaustion raises, letting worker finally mark_failed as fail-fast fallback.
    """
    try:
        await _persist_with_retry(task_id, job_id, endpoint, base_url)
        logger.info("provider_job_id 已持久化 task_id=%s provider=%s job_id=%s", task_id, provider, job_id)
    except Exception as exc:
        logger.error(
            "provider_job_id_persist_failed task_id=%s provider=%s job_id=%s error=%s",
            task_id,
            provider,
            job_id,
            exc,
        )
        raise


class ProviderJobIdPersistenceMixin:
    """Central provider_job_id persistence for submit-poll video backends.

    Backends call ``self._persist_provider_job_id`` after submission obtains job_id in ``generate()``.
    Centralize timing (after submit/before poll), None skips for nonworker paths, and fail-fast semantics
    here. Submit-poll backends inherit this mixin without handling persistence conditions themselves.

    Backends still explicitly pass PROVIDER_* constants as ``provider``, never infer from ``self.name``: gemini
    ``name`` is ``gemini-aistudio`` / ``gemini-vertex``, unlike billing/log attribution ``PROVIDER_GEMINI``
    (``gemini``); automatic name inference would change persisted-log provider fields.
    """

    async def _persist_provider_job_id(
        self,
        request: VideoGenerationRequest,
        job_id: str,
        *,
        provider: str,
        endpoint: str | None = None,
    ) -> None:
        """Call immediately after successful submission: persist worker job_id, skip nonworkers with task_id=None.

        Persist separate endpoint dimensions: protocol identifiers from ``request.execution_endpoint`` injected by
        custom-provider wrappers before forwarding (always None for built-ins), actual hosts from
        ``endpoint`` passed by backends whose hosts vary with configuration, replayed for resume polling.
        Both provider types share this mechanism; hosts always enter ``submitted_base_url``. Persistence failures raise
        after DB transients already retry 3 times in ``persist_provider_job_id``; worker finally mark_failed
        preserves fail-fast semantics (ADR 0007).
        """
        if request.task_id is not None:
            await persist_provider_job_id(
                request.task_id,
                job_id,
                provider=provider,
                endpoint=request.execution_endpoint,
                base_url=endpoint,
            )
        if request.on_provider_resubmit_unsafe is not None:
            request.on_provider_resubmit_unsafe()


def is_retryable_http_status(status_code: int, *, retry_not_found: bool = False) -> bool:
    """HTTP status → retryability.

    Always retry transient 408 Request Timeout / 425 Too Early / 429 Too Many Requests / 5xx.
    Default 404 fails fast as deterministic absence, such as incorrect endpoints; polling/download retry_not_found=True
    treats it as temporary postsubmission unavailability/resource propagation. Other 4xx (400/401/403/422, etc.)
    deterministically fail fast: retries merely reach max_wait while occupying workers.
    """
    if status_code in (408, 425, 429):
        return True
    if 500 <= status_code <= 599:
        return True
    if status_code == 404:
        return retry_not_found
    return False


# Definitely-unsent httpx transports: connection establishment/acquisition/proxy-handshake failures.
# Safe retries: servers cannot have created/billed tasks. Read/write and later transport errors (ReadTimeout,
# WriteError, midconnection disconnects, RemoteProtocolError) may have reached servers and are ambiguous, excluded here.
_NOT_SENT_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,  # Proxy connection/handshake failures: requests never left client→proxy segments or reached destination providers.
)

# Local/protocol failures before request bytes send: UnsupportedProtocol URL schemes
# or LocalProtocolError malformed local construction. Neither risks duplicate billing nor improves through max_wait:
# polling fails fast, submission propagates unchanged, never labeling ambiguous delivery.
# RemoteProtocolError is a sibling ProtocolError but means server disconnects midway, excluded here.
_NON_RETRYABLE_LOCAL_ERRORS: tuple[type[Exception], ...] = (
    httpx.LocalProtocolError,
    httpx.UnsupportedProtocol,
)


def should_retry_submit(exc: Exception) -> bool:
    """Retry predicates for nonidempotent creation + billing POSTs.

    Unlike ``should_retry_poll``, transport retries cover only definitely-unsent requests
    (``_NOT_SENT_TRANSPORT_ERRORS``: connection establishment/acquisition/proxy-handshake failures), avoiding duplicate
    tasks/billing. Ambiguous failures such as ReadTimeout, potentially processed by servers, become
    terminal ``AmbiguousSubmitError`` through ``submit_post``; this predicate returns False for them
    and all business exceptions. Explicit HTTPStatusError status_code gates retry 5xx/408/425/429 (server-reported
    creation failures), failing fast on 404/deterministic 4xx.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=False)
    return isinstance(exc, _NOT_SENT_TRANSPORT_ERRORS)


def should_retry_poll(exc: Exception) -> bool:
    """Retry predicates for idempotent polling/download GETs.

    Idempotent query retries have no side effects, so transport/network RequestError and basic transients retry; only local/
    protocol ``_NON_RETRYABLE_LOCAL_ERRORS`` (UnsupportedProtocol / LocalProtocolError) fail before requests,
    never improving through max_wait, and fail fast. HTTPStatusError status_code gates treat 404
    as temporary task/resource propagation. Messages include URLs/task_ids whose "500"/"503" substrings
    mislead string fallbacks, so explicit status checks bypass them; business exceptions such as
    ResumeExpiredError always fail fast.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=True)
    if isinstance(exc, _NON_RETRYABLE_LOCAL_ERRORS):
        return False
    return isinstance(exc, (httpx.RequestError, *BASE_RETRYABLE_ERRORS))


def should_retry_signed_download(exc: Exception) -> bool:
    """Presigned URL download predicates: every 4xx is deterministic failure.

    Signed URLs are fully available when issued; 403/404 means signature errors/nonexistent objects and will not improve
    through max retries. Retry only 5xx/408/425/429 and transport/network failures, safe for idempotent GETs.
    Explicit HTTPStatusError status_code gates bypass misleading "503"/"timeout" substrings
    in result URLs.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=False)
    if isinstance(exc, _NON_RETRYABLE_LOCAL_ERRORS):
        return False
    return isinstance(exc, (httpx.RequestError, *BASE_RETRYABLE_ERRORS))


def should_retry_download(exc: Exception) -> bool:
    """Video artifact download predicates for idempotent GETs of provider-issued successful-task URLs.

    Add 403/404 retries to :func:`should_retry_signed_download`: observed postterminal unready artifacts/CDN propagation
    include Ark ``video_not_ready``. Endpoint tests catch malformed URLs before saving.
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 404):
        return True
    return should_retry_signed_download(exc)


async def submit_post(
    post_fn: Callable[[], Awaitable[httpx.Response]],
    *,
    provider: str,
    request: VideoGenerationRequest | None = None,
    include_provider_reason: bool = True,
) -> httpx.Response:
    """Unified nonidempotent create/submission POST wrapper, routing failures by delivery certainty.

    - Connection/proxy establishment failures (ConnectError/ConnectTimeout/PoolTimeout/ProxyError): definitely unsent
      → propagate unchanged for ``should_retry_submit`` retries.
    - Local/protocol errors (UnsupportedProtocol/LocalProtocolError): definitely failed before sending, without billing
      risk → propagate unchanged for ``should_retry_submit`` fast failure, never label ambiguous delivery.
    - Other transport failures (ReadTimeout/WriteError/RemoteProtocolError, etc.): servers may have processed
      requests → terminal ``AmbiguousSubmitError``, no retries, preventing duplicate tasks/billing.
    - >=400 responses: log bodies first for diagnostics such as 413, then ``raise_for_status`` raises
      HTTPStatusError for ``should_retry_submit`` status_code routing.

    Pair with ``with_retry_async(retry_if=should_retry_submit)``: decorators own retries;
    this wrapper converts ambiguous states into nonretryable terminal exceptions beforehand.

    Supplied ``request`` enables response-body evidence. Task-creation failures precede polling and would never
    be captured elsewhere, despite especially needing original provider responses.
    """
    try:
        resp = await post_fn()
    except httpx.RequestError as exc:
        # Definitely-unsent connection failures (transient/retryable) and local/protocol errors (deterministic/fast failure)
        # never risk duplicate tasks/billing; propagate unchanged for should_retry_submit routing, without ambiguity wrappers.
        if isinstance(exc, (*_NOT_SENT_TRANSPORT_ERRORS, *_NON_RETRYABLE_LOCAL_ERRORS)):
            raise
        raise AmbiguousSubmitError(provider=provider) from exc
    if request is not None:
        await notify_provider_response(request, "submit", _response_body_or_text(resp))
    if resp.status_code >= 400:
        logger.warning("%s create 返回 %s: %s", provider, resp.status_code, redact_provider_text(resp.text)[:500])
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if include_provider_reason and is_provider_rejection(resp.status_code):
                raise provider_rejected_error(exc, provider_reason=provider_reason_summary(resp)) from None
            raise redacted_status_error(exc) from None
    return resp


def _dig(payload: object, path: tuple[str | int, ...]) -> object | None:
    """Traverse dict keys/list indices by path (int means list index); None on missing levels."""
    cur: object = payload
    for seg in path:
        if isinstance(seg, int):
            if not isinstance(cur, list) or seg >= len(cur):
                return None
            cur = cur[seg]
        else:
            if not isinstance(cur, dict) or seg not in cur:
                return None
            cur = cur[seg]
    return cur


def first_str_by_paths(payload: object, paths: tuple[tuple[str | int, ...], ...]) -> str | None:
    """Try paths in priority order for the first nonempty string, tolerating ints converted to str.

    Provider responses differ; ordered path tables tolerate varying shapes without separate branches per shape.
    """
    for path in paths:
        val = _dig(payload, path)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, int) and not isinstance(val, bool):
            return str(val)
    return None


def first_mapping_by_paths(payload: object, paths: tuple[tuple[str | int, ...], ...]) -> dict | None:
    """Try paths in priority order for the first dict; None without matches.

    Like ``first_str_by_paths``, handle grouped substructures such as metadata using the same priority-table approach
    as statuses/video addresses, avoiding separate shape branches across deployments.
    """
    for path in paths:
        val = _dig(payload, path)
        if isinstance(val, dict):
            return val
    return None


# Common error locations: flat error or wrapped data.error.
_ERROR_PATHS: tuple[tuple[str | int, ...], ...] = (("error",), ("data", "error"))


def extract_provider_error_message(state: object) -> str:
    """Best-effort provider error descriptions from dict message/name or direct strings; unknown if unavailable."""
    for path in _ERROR_PATHS:
        err = _dig(state, path)
        if isinstance(err, dict):
            # Check fields independently: blank/nonstring message still falls back to name.
            for value in (err.get("message"), err.get("name")):
                if isinstance(value, str) and value.strip():
                    return value.strip()
        elif isinstance(err, str) and err.strip():
            return err.strip()
    return "unknown"


async def poll_with_retry[T](
    *,
    poll_fn: Callable[[], Awaitable[T]],
    is_done: Callable[[T], bool],
    is_failed: Callable[[T], str | None],
    max_wait: float,
    poll_interval: float = VIDEO_POLL_INTERVAL_SECONDS,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    retry_if: Callable[[Exception], bool] | None = None,
    label: str = "",
    on_progress: Callable[[T, float], None] | None = None,
    clock: AsyncClock | None = None,
) -> T:
    """Shared async polling with transient retries and timeout controls.

    `VIDEO_POLL_MAX_CONSECUTIVE_FAILURES` retryable errors without successful responses raise terminal
    RuntimeError; any success resets the count. Back off by `poll_interval × 2^k`, capped at
    `VIDEO_POLL_MAX_BACKOFF_SECONDS`; prefer integer-second `Retry-After` within that cap.
    Failure budgets bound unreachable providers; `max_wait` bounds reachable but slow ones. Every wait clamps to `max_wait`
    deadlines, ensuring final queries still start within budget.

    Failure budgets apply to all consumers, including image channels: `lib/backends/image_backends/vidu.py`
    and `lib/backends/kling_backend_base.py` also terminate at consecutive limits without consuming entire `max_wait` windows.

    Args:
        poll_fn: Async query returning latest states.
        is_done: Whether results indicate completion.
        is_failed: Whether results indicate failure; return reasons or None.
        max_wait: Maximum seconds, raising TimeoutError at expiry.
        poll_interval: Interval after success and base failure backoff; video channels uniformly default to 5s.
        retryable_errors: Retryable exception tuple, used without retry_if.
        retry_if: Custom predicates replacing default `should_retry`, precisely controlling retries,
            such as deterministic 4xx versus transient 5xx by HTTP status_code.
        label: Log prefix, such as "Ark" / "Gemini".
        on_progress: Optional callback after every nonterminal query.
        clock: Monotonic timing/async waiting seam; absent uses context-bound ``resolve_clock``,
            defaulting to system clocks in production.
    """
    active_clock = resolve_clock(clock)
    start = active_clock.monotonic()
    prefix = f"{label} " if label else ""
    predicate = retry_if if retry_if is not None else (lambda e: should_retry(e, retryable_errors))
    consecutive_failures = 0

    # Query before waiting: completed/cached tasks return immediately without wasting poll_interval.
    while True:
        try:
            result = await poll_fn()
        except Exception as e:
            if not predicate(e):
                raise
            consecutive_failures += 1
            logger.warning("%s轮询异常（将重试）: %s - %s", prefix, type(e).__name__, str(e)[:200])
            if consecutive_failures >= VIDEO_POLL_MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(
                    f"{prefix}连续轮询失败 {VIDEO_POLL_MAX_CONSECUTIVE_FAILURES} 次，最后错误: {e}"
                ) from e
            retry_after = _retry_after_seconds(e)
            wait_time = (
                retry_after
                if retry_after is not None
                else min(
                    poll_interval * 2 ** (consecutive_failures - 1),
                    VIDEO_POLL_MAX_BACKOFF_SECONDS,
                )
            )
        else:
            consecutive_failures = 0
            error_msg = is_failed(result)
            if error_msg is not None:
                raise RuntimeError(error_msg)
            if is_done(result):
                return result
            if on_progress is not None:
                on_progress(result, active_clock.monotonic() - start)
            wait_time = poll_interval

        remaining = max_wait - (active_clock.monotonic() - start)
        if remaining <= 0:
            raise TimeoutError(f"{prefix}任务超时（{max_wait:.0f}秒）")
        # Wait no longer than remaining budgets; next query always starts at or before max_wait deadlines.
        await active_clock.sleep(min(wait_time, remaining))


def _retry_after_seconds(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if not isinstance(raw, str) or not raw.isdigit():
        return None
    seconds = int(raw)
    return seconds if 0 <= seconds <= VIDEO_POLL_MAX_BACKOFF_SECONDS else None


def url_origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) triple, with scheme-default ports for same-origin checks."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None)
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


#: Manual redirect hop cap, matching httpx defaults.
_MAX_REDIRECTS = 20


def _redirect_location(response: httpx.Response) -> str | None:
    return response.headers.get("location") if response.is_redirect else None


def _rewrites_to_get(status_code: int, method: str) -> bool:
    """Whether redirects rewrite methods to GET and discard bodies, matching httpx / RFC 9110.

    303 rewrites all except HEAD; 301 / 302 rewrite POST only, preserving PUT/other methods and bodies
    to avoid semantically different endpoint requests. 307 / 308 always replay unchanged.

    Decide method changes only; :func:`request_with_scoped_credentials` separately removes cross-origin redirect bodies.
    """
    if status_code == 303:
        return method != "HEAD"
    if status_code in (301, 302):
        return method == "POST"
    return False


def _without_query(url: str) -> str:
    """URLs stripped of query/userinfo, which may contain query credentials/signatures/Basic passwords
    unsuitable for error messages/logs."""
    return str(httpx.URL(url).copy_with(query=None, userinfo=b""))


#: Rejection-summary character limit: upstream bodies may have thousands of characters, but summaries enter task failure envelopes
#: and user task lists, requiring bounds. End truncated content with :data:`_PROVIDER_REASON_ELLIPSIS`.
PROVIDER_REASON_MAX_CHARS = 300

_PROVIDER_REASON_ELLIPSIS = "…"

#: Rejection-reason fields, using first nonempty string in order.
_REASON_MESSAGE_KEYS = ("message", "msg", "error_msg", "detail", "description", "reason")

#: Common outer error containers wrapping reasons.
_REASON_NESTED_KEYS = ("error", "output", "data", "detail", "result")

#: Machine codes at rejection levels identify triggered rules better than natural language; prepend when available.
_REASON_CODE_KEYS = ("code", "error_code", "type")

#: Authentication statuses: 401 unauthenticated, 407 proxy unauthenticated. Never summarize these bodies.
_CREDENTIAL_STATUS_CODES = frozenset({401, 407})

#: Container-depth cap preventing deep recursion on malformed errors.
_REASON_MAX_DEPTH = 3

#: URLs or URL-like tokens with queries. Vendors sometimes omit schemes; queries may still carry credentials/signatures.
_REASON_URL_RE = re.compile(r"https?://[^\s\"'<>]+|[^\s\"'<>?]+\?[^\s\"'<>]+", re.IGNORECASE)


def _reason_code(body: Mapping[str, object]) -> str | None:
    """Machine codes at the rejection-reason level; ``bool`` is not a code (JSON ``True`` is a flag, not an error number)."""
    for key in _REASON_CODE_KEYS:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _reason_text(payload: object, depth: int = 0) -> str | None:
    """Extract rejection-reason sentences from parsed error bodies; ``None`` if unavailable.

    Recognize message fields and limited outer containers only, never serialize entire objects: error bodies often echo
    complete requests including prompts, which whole serialization would expose in user-visible summaries.
    """
    if isinstance(payload, str):
        return payload.strip() or None
    if not isinstance(payload, dict) or depth >= _REASON_MAX_DEPTH:
        return None
    body = cast(dict[str, object], payload)
    text: str | None = None
    for key in _REASON_MESSAGE_KEYS:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            break
    if text is None:
        for key in _REASON_NESTED_KEYS:
            if key in body:
                text = _reason_text(body[key], depth + 1)
                if text:
                    break
    if not text:
        return None
    code = _reason_code(body)
    return f"{code}: {text}" if code and code not in text else text


def _strip_url_queries(text: str) -> str:
    """Strip query/fragment/userinfo from every absolute URL in text: presigned/query credentials live in query strings,
    while ``https://TOKEN@host`` embeds tokens in userinfo requiring complete removal; subsequent generic
    redaction recognizes only ``user:password@``, missing bare tokens without colons."""

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,;:!?)]}\"'")
        trailing = match.group(0)[len(raw) :]
        try:
            stripped = str(httpx.URL(raw).copy_with(query=None, fragment=None, userinfo=b""))
        except (httpx.InvalidURL, ValueError, UnicodeError):
            stripped = "<url>"
        return stripped + trailing

    return _REASON_URL_RE.sub(_replace, text)


def redact_provider_text(value: object) -> str:
    """Redact provider text by stripping all URL queries before masking credentials.

    Both layers are necessary: ``redact_diagnostic_text`` recognizes common credential names only, while private vendor
    presigned parameters require stripping whole queries; only the former recognizes credentials echoed outside queries.
    """
    if not isinstance(value, str):
        # Nonstrings occur only in evidence fallback: no URL text to strip; generic redaction handles str conversion.
        return redact_diagnostic_text(value)
    return redact_diagnostic_text(_strip_url_queries(value))


def is_provider_rejection(status_code: int) -> bool:
    """Whether statuses mean upstream submission rejection: deterministic 4xx excluding retryable transients.

    Independent of available summaries: rejection itself drives read-side wording/actions, modifying inputs rather than retrying.
    """
    return 400 <= status_code < 500 and not is_retryable_http_status(status_code, retry_not_found=False)


def provider_reason_summary(response: httpx.Response) -> str | None:
    """Extract redacted/truncated rejection summaries from deterministic upstream 4xx bodies; ``None`` if unavailable.

    Produce only for 4xx: 5xx/transport failures indicate temporary upstream unavailability without actionable body content;
    including it would misleadingly turn transient outages into apparently deterministic rejections.

    Two redaction layers: strip each URL query containing presigned/query credentials, matching
    ``_without_query``, then ``redact_diagnostic_text`` masks Authorization / Bearer /
    quoted secrets, sharing log rules.
    """
    if not is_provider_rejection(response.status_code):
        return None
    if response.status_code in _CREDENTIAL_STATUS_CODES:
        # Authentication-failure bodies discuss credentials themselves; upstream often echoes full keys. No actionable reason exists beyond
        # unaccepted credentials, already conveyed by localized status wording.
        return None
    try:
        body_text = response.text
    except httpx.ResponseNotRead:
        # Streaming responses not yet aread lack readable error bodies; never issue another request for them.
        return None
    try:
        payload: object = response.json()
    except ValueError:
        reason = body_text
    else:
        reason = _reason_text(payload)
    return summarize_provider_reason(reason)


def summarize_provider_reason(reason: object) -> str | None:
    """Redact provider rejection text, flatten whitespace, and truncate; ``None`` if unavailable.

    Shared by HTTP bodies (:func:`provider_reason_summary`) and gRPC status details.
    """
    if not reason:
        return None
    summary = " ".join(redact_provider_text(reason).split())
    if not summary:
        return None
    if len(summary) <= PROVIDER_REASON_MAX_CHARS:
        return summary
    return summary[: PROVIDER_REASON_MAX_CHARS - len(_PROVIDER_REASON_ELLIPSIS)] + _PROVIDER_REASON_ELLIPSIS


@dataclass(frozen=True)
class _Hop:
    """Request destinations and accompanying credentials; remove credentials entirely on cross-origin redirects."""

    url: str
    headers: Mapping[str, str] | None
    params: Mapping[str, str] | None = None
    #: Query credentials: ``Location`` entirely replaces queries, so reattach for same-origin redirects.
    auth_query: Mapping[str, str] | None = None

    def redirected_to(self, location: str, credential_origin: tuple[str, str, int | None]) -> _Hop:
        target = httpx.URL(self.url).join(location)
        if url_origin(str(target)) != credential_origin:
            # Cross-origin: remove headers, query credentials, and original params together.
            return _Hop(str(target), None, None, None)
        # Same-origin: credentials remain in scope. Never replay original params because redirect targets carry queries, but query
        # credentials must return or `/jobs` → `/jobs/` normalization loses api_key.
        # Merge target URL queries, not params, which would replace entire queries and erase Location parameters.
        if self.auth_query:
            target = target.copy_merge_params(dict(self.auth_query))
        return _Hop(str(target), self.headers, None, self.auth_query)


@dataclass(frozen=True)
class _Body:
    """Whole request body: discard entirely on GET rewrites or cross-origin redirects, never partially discard three fields."""

    json: object | None = None
    files: Mapping[str, Any] | None = None
    data: Mapping[str, Any] | None = None


async def request_with_scoped_credentials(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None,
    json: object | None,
    auth_query: Mapping[str, str] | None = None,
    files: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
) -> httpx.Response:
    """Send requests, following redirects manually and stripping credential ``headers`` outside this request's origin.

    httpx ``follow_redirects`` removes only ``Authorization`` across origins, forwarding custom headers such as ``X-API-Key``
    unchanged. All rendered-auth-section requests must use this wrapper, never client automatic redirects.

    Credential scope is ``url``'s own origin, not external baselines: caller-selected addresses are intended destinations;
    defend against server ``Location`` redirects elsewhere.

    Cross-origin redirects drop bodies too; 307 / 308 retain methods, but bodies remain credential-scoped.
    Same-origin redirects follow :func:`_rewrites_to_get`; 307 / 308 replay unchanged.

    Select exactly one of ``files``, ``data``, or ``json``; ``files`` / ``data`` use multipart bodies. Same-origin redirects replay unchanged, so
    ``files`` must contain bytes, not handles that would be exhausted and send empty bodies on subsequent hops.
    """
    credential_origin = url_origin(url)
    # First-hop auth.query is already in URLs, never duplicate via params; restore only for same-origin redirects.
    hop = _Hop(url, headers, None, auth_query)
    current_method = method.upper()
    body = _Body(json, files, data)
    for _ in range(_MAX_REDIRECTS + 1):
        response = await client.request(
            current_method,
            hop.url,
            headers=hop.headers,
            params=hop.params,
            json=body.json,
            files=body.files,
            data=body.data,
            follow_redirects=False,
        )
        location = _redirect_location(response)
        if location is None:
            return response
        if _rewrites_to_get(response.status_code, current_method):
            current_method = "GET"
            body = _Body()
        hop = hop.redirected_to(location, credential_origin)
        if url_origin(hop.url) != credential_origin:
            body = _Body()
    raise RuntimeError(f"request exceeded {_MAX_REDIRECTS} redirects: {_without_query(url)}")


async def stream_to_file(
    client: httpx.AsyncClient,
    url: str,
    output_path: Path,
    *,
    max_bytes: int,
    timeout: int = 120,  # noqa: ASYNC109 -- forwards httpx I/O timeout config, not async cancellation deadlines
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    credential_origin: tuple[str, str, int | None] | None = None,
    auth_query: Mapping[str, str] | None = None,
) -> None:
    """Stream URLs to local files without retries, centrally handled by :func:`with_artifact_retry`.

    Abort above ``max_bytes`` with :class:`ArtifactTooLargeError`, leaving no artifact fragments;
    read at most ``ERROR_BODY_MAX_BYTES`` from error bodies. Caller ``client`` constrains outbound destinations
    (:func:`lib.backends.artifact_download_guard.artifact_http_client`).

    ``credential_origin`` scopes ``headers`` credentials. When supplied, follow each redirect manually,
    dropping all ``headers`` across origins: httpx strips only ``Authorization``, while endpoint
    auth can use arbitrary headers such as ``X-API-Key``. Automatic ``follow_redirects`` would leak them
    to redirected object-storage/CDN destinations.

    Persist unchanged bytes only, without faststart: this function runs within :func:`with_artifact_retry` wall-clock budgets;
    callers remux after retry completion through :func:`faststart_video_artifact`.
    """

    async def _write(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            # Replace with read/truncated body copies so HTTPStatusError.response.text is available.
            resp = await buffered_error_response(resp)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Artifact URL queries may carry signatures/query credentials; exclude both from logs/task records.
            raise redacted_status_error(exc) from None
        await stream_body_to_file(resp, output_path, max_bytes=max_bytes)

    if credential_origin is None:
        async with client.stream("GET", url, timeout=timeout, headers=headers, params=params) as resp:
            await _write(resp)
        return

    hop = _Hop(url, headers, params, auth_query)
    for _ in range(_MAX_REDIRECTS + 1):
        async with client.stream(
            "GET",
            hop.url,
            timeout=timeout,
            headers=hop.headers,
            params=hop.params,
            follow_redirects=False,
        ) as resp:
            location = _redirect_location(resp)
            if location is None:
                # 3xx without Location cannot redirect. raise_for_status accepts 3xx, but direct persistence would save redirect
                # bodies as artifacts; raise for retry-budget handling.
                if resp.is_redirect:
                    raise RuntimeError(f"redirect without a Location header: {_without_query(hop.url)}")
                await _write(resp)
                return
        hop = hop.redirected_to(location, credential_origin)
    raise RuntimeError(f"artifact download exceeded {_MAX_REDIRECTS} redirects: {_without_query(url)}")


#: Artifact-download wall-clock cap. Failure budgets primarily terminate (10 consecutive, backoff capped at 60s, about 435s total),
#: while this bounds reachable-but-slow downloads. Video channels override via `poll_timeout_seconds`;
#: image/audio lack that dimension and use this default.
ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS = 1800


async def with_artifact_retry[T](
    attempt: Callable[[], Awaitable[T]],
    *,
    label: str,
    retry_if: Callable[[Exception], bool] | None = should_retry_download,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    max_wait: float = ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
) -> T:
    """Retry artifact retrieval under polling-shared budgets.

    Downloads/polling are idempotent retrieval after provider task creation, sharing termination rules: consecutive failures reach
    ``VIDEO_POLL_MAX_CONSECUTIVE_FAILURES`` → terminal failure; exponential backoff caps at
    ``VIDEO_POLL_MAX_BACKOFF_SECONDS``; prefer ``Retry-After`` when present. All built-in video/image/audio channels
    and declarative runtimes share this entry and download retry constants.

    HTTP channels default to ``should_retry_download`` status_code gates; SDK download exceptions
    are not ``HTTPStatusError``, so pass ``retry_if=None`` for ``retryable_errors`` classification.
    """

    # Wrap retrieval in single-element tuples: `poll_with_retry` uses is_done, and retrieval succeeds whenever it does not raise,
    # independently of results such as None/empty bytes.
    async def once() -> tuple[T]:
        return (await attempt(),)

    # Wall-clock limits wrap entire retries: `poll_with_retry` checks max_wait only after poll_fn returns,
    # but retrieval may itself stay reachable yet slow indefinitely, exactly what this cap bounds.
    async with asyncio.timeout(max_wait):
        fetched = await poll_with_retry(
            poll_fn=once,
            is_done=lambda _fetched: True,
            is_failed=lambda _fetched: None,
            max_wait=max_wait,
            retryable_errors=retryable_errors,
            retry_if=retry_if,
            label=label,
        )
    return fetched[0]


async def faststart_video_artifact(output_path: Path) -> None:
    """Best-effort faststart for persisted mp4 / mov / m4v (moov before mdat), enabling browser playback without
    full downloads. Preserve original bytes if bundled ffmpeg is unavailable, remux fails, or times out.

    Call after :func:`with_artifact_retry`, outside download wall-clock budgets: video budgets use
    ``poll_timeout_seconds``, as low as 60s; cancellation during remux would invalidate successfully downloaded artifacts
    and fail generation. Remux has separate deadlines and atomic temporary-file replacement, preserving originals on failure.
    """
    if output_path.suffix.lower() in FASTSTART_SUFFIXES:
        # Successfully downloaded videos must still commit; remux retains its own subprocess deadline.
        await run_noninterruptible_async(ensure_faststart(output_path))


async def download_video(
    url: str,
    output_path: Path,
    *,
    label: str = "",
    timeout: int = 120,  # noqa: ASYNC109 -- forwards httpx I/O timeout config, not async cancellation deadlines
    retry_if: Callable[[Exception], bool] | None = should_retry_download,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    max_wait: float = ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
) -> None:
    """Stream URL videos locally under shared artifact retry budgets; best-effort faststart afterward."""

    async def attempt() -> None:
        async with artifact_http_client(follow_redirects=True) as http_client:
            await stream_to_file(http_client, url, output_path, max_bytes=VIDEO_ARTIFACT_MAX_BYTES, timeout=timeout)

    await with_artifact_retry(
        attempt, label=label, retry_if=retry_if, retryable_errors=retryable_errors, max_wait=max_wait
    )
    await faststart_video_artifact(output_path)


async def download_resumable_video(url: str, output_path: Path, *, label: str) -> None:
    """Download resumable-task outputs; convert budget exhaustion into stable retryable-download failures.

    Propagate ``NonRetryableError`` unchanged for invalid destinations/oversized artifacts: retrieval retries cannot change outcomes.
    """
    try:
        await download_video(url, output_path, label=label)
    except NonRetryableError:
        raise
    except Exception as exc:
        raise ArtifactDownloadError(detail=str(exc)) from exc


def reference_audio_to_data_uri(path: Path, *, model: str, mime_types: Mapping[str, str]) -> str:
    """Audio references → base64 data URIs; reject unsupported formats/unreadable files.

    Unlike reference images, missing audio cannot be skipped: prompt “音频N” follows audio-item occurrence order
    in content arrays. Skipping clips shifts every later index and assigns character voices to other characters,
    silently incorrect while still billing.

    Backends supply ``mime_types``: accepted MIME differs for the same extension (mp3 uses
    ``audio/mp3`` or ``audio/mpeg``). Shared tables would send unverified MIME to a vendor.
    """
    # Only video channels reach here; deferred imports keep image/audio runtime support independent of video contracts.
    from arcreel_market_core.video_backend_contract import VideoCapabilityError

    mime = mime_types.get(path.suffix.lower())
    if mime is None:
        raise VideoCapabilityError(
            "video_reference_audio_format_unsupported",
            model=model,
            name=path.name,
            supported=", ".join(sorted(mime_types)),
        )
    try:
        return file_to_data_uri(path, mime)
    except OSError as exc:
        raise VideoCapabilityError("video_reference_audio_unreadable", model=model, names=path.name) from exc


async def notify_provider_response(request: VideoGenerationRequest, stage: ProviderResponseStage, body: object) -> None:
    """Send HTTP-channel provider responses/phases to optional diagnostic callbacks.

    Evidence is diagnostic, outside business parsing: persistence failures only log, never fail already-accepted,
    probably billed generation because diagnostic columns cannot be written.

    Redact original responses here because evidence persists and returns via APIs; upstream bodies may include signed URLs or
    echoed credentials. Centralize here, not call sites, sharing one policy across evidence points. Each string
    uses ``redact_provider_text``, matching rejection summaries/failure logs: generic masks know common credential
    names only; private vendor query parameters require whole-query stripping.
    """
    await notify_provider_response_to(request.on_provider_response, stage, body, label=request.task_id)


async def notify_provider_response_to(
    sink: Callable[[ProviderResponseStage, object], Awaitable[None]] | None,
    stage: ProviderResponseStage,
    body: object,
    *,
    label: str | None = None,
) -> None:
    """Same behavior, accepting callbacks only: image requests lack ``task_id``, so callers supply identifiers."""
    if sink is None:
        return
    try:
        await sink(stage, sanitize_diagnostic_payload(body, redact_text=redact_provider_text))
    except Exception:
        logger.warning("供应商响应留痕写入失败 task_id=%s", label, exc_info=True)


def recording_poll[T](
    poll_fn: Callable[[], Awaitable[T]],
    request: VideoGenerationRequest,
    *,
    stage: ProviderResponseStage = "poll",
) -> Callable[[], Awaitable[T]]:
    """Wrap polling/secondary retrieval, recording every provider response before status/error interpretation.

    ``is_failed`` raises terminal failures inside :func:`poll_with_retry`; ``poll_fn`` itself raises HTTP errors,
    both before ``poll_with_retry`` returns. Recording afterward would capture only successful calls,
    leaving the most diagnostically important failures empty.
    """

    async def once() -> T:
        try:
            body = await poll_fn()
        except httpx.HTTPStatusError as exc:
            await notify_provider_response(request, stage, _response_body_or_text(exc.response))
            raise
        await notify_provider_response(request, stage, body)
        return body

    return once


def resume_expiry_gate[T](
    poll_fn: Callable[[], Awaitable[T]],
    *,
    resume_job_id: str | None,
    provider: str,
) -> Callable[[], Awaitable[T]]:
    """Resume polling expiry gate: HTTP 404 querying job IDs becomes ``ResumeExpiredError``.

    :func:`should_retry_poll` retries 404 as newly submitted/unready queries. Resumed tasks were submitted earlier,
    so 404 means vendor-side absence; retries merely exhaust budgets into generic failures. ``resume_job_id`` =
    None means new submissions: return ``poll_fn`` unchanged and let retry predicates handle 404.

    Recording belongs inside this gate: pass :func:`recording_poll` outputs as ``poll_fn``; outside wrappers
    cannot see 404 after conversion to ResumeExpiredError.
    """
    if resume_job_id is None:
        return poll_fn
    from arcreel_market_core.video_backend_contract import ResumeExpiredError

    async def gated() -> T:
        try:
            return await poll_fn()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ResumeExpiredError(job_id=resume_job_id, provider=provider) from exc
            raise

    return gated


def _response_body_or_text(response: httpx.Response) -> object:
    """Response bodies: parsed JSON structures if possible, otherwise raw text; evidence boundaries centrally truncate."""
    try:
        return response.json()
    except ValueError:
        return response.text
