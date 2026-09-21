"""调用通道的运行支持：提交与轮询、重试判定、带凭据作用域的请求、产物下载落盘、供应商响应留痕
与供应商任务 id 落库。

视频、图像、音频三类调用通道与自定义供应商的 backend 共用本模块。视频契约位于
:mod:`lib.backends.video_backend_contract`，图像与音频契约位于各自通道包的 ``base``。图像与音频
通道经本模块不会加载视频通道包：视频请求只作类型标注出现，视频能力异常只在视频专属函数里取。
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
from lib.infra.logging_utils import redact_diagnostic_text, sanitize_diagnostic_payload
from lib.infra.retry import (
    BASE_RETRYABLE_ERRORS,
    AsyncClock,
    NonRetryableError,
    SystemClock,
    _should_retry,
    with_retry_async,
)

if TYPE_CHECKING:
    from lib.backends.video_backend_contract import ProviderResponseStage, VideoGenerationRequest

# `_should_retry` 默认会做字符串模式兜底（"timeout"/"503" 等），
# 而 persist 重试要严格"DB 瞬态错误"语义——业务异常（如
# `ValueError("Connection timed out: rate")`）不该被字符串子串吞掉。
# 显式传 `retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS)` 关掉兜底。

logger = logging.getLogger(__name__)

VIDEO_POLL_INTERVAL_SECONDS = 5.0
VIDEO_POLL_MAX_CONSECUTIVE_FAILURES = 10
VIDEO_POLL_MAX_BACKOFF_SECONDS = 60.0


# DB 瞬态错误集合：sqlite "database is locked"、pg "could not connect" / 连接已关闭。
# 故意不收 DBAPIError 父类——会兜住 IntegrityError/DataError/ProgrammingError 等非瞬态
# 错误（SQL 语法 / 约束违反），重试无意义且拖延 fail-fast。
_PERSIST_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,
    InterfaceError,
    ConnectionError,
    TimeoutError,
)
_PERSIST_BACKOFF_SECONDS: tuple[int, ...] = (1, 2, 4)


class ProviderJobIdStore(Protocol):
    """供应商任务 id 的落库入口：与 job_id 同一次写入落下协议标识与实际请求域名。"""

    async def persist_provider_job_id(
        self, task_id: str, job_id: str, *, endpoint: str | None = None, base_url: str | None = None
    ) -> None: ...


_provider_job_id_store: Callable[[], ProviderJobIdStore] | None = None


def install_provider_job_id_store(get_store: Callable[[], ProviderJobIdStore]) -> None:
    """登记落库入口的取用方式；由生成队列在其模块加载时登记。

    落库入口由上层注入而非在此导入：生成队列经配置解析够得到声明式运行时，运行支持直接导入它，
    所有用到运行支持的调用通道（包括 ComfyUI 子包）都会被这条链拖过去。每次落库时调一次
    ``get_store``，不缓存它返回的实例。
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
    """Submit 之后立即调：把 job_id 持久化到 DB 让重启可接续。

    Caller 显式传 task_id；``endpoint`` 是协议标识（协议维度，只有自定义供应商有，记录本笔供应商
    任务按哪套协议提交），``base_url`` 是请求实际发往的域名（连接维度，两类供应商通用，续跑据此
    回放原域名轮询）。两者与 job_id 同一次写入落地。DB 瞬态错误最多重试 3 次，业务异常立即抛。
    重试用尽抛异常，由 worker finally 兜底 mark_failed（fail-fast）。
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
    """提交-轮询型 video backend 的 provider_job_id 持久化收口点。

    各 backend 在 ``generate()`` 内 submit 拿到 job_id 后调 ``self._persist_provider_job_id``
    统一写回；持久化时机（submit 后、poll 前）、None 跳过（非 worker 路径）、fail-fast 语义集中
    于此单一调用点。提交-轮询型 backend 继承本 mixin 即得能力，无需自己处理持久化条件。

    ``provider`` 仍由 backend 显式传 PROVIDER_* 常量而非从 ``self.name`` 推：gemini 的
    ``name`` 是 ``gemini-aistudio`` / ``gemini-vertex``，与计费/日志归因用的 ``PROVIDER_GEMINI``
    （``gemini``）不同，自动取 name 会改写持久化日志的 provider 字段。
    """

    async def _persist_provider_job_id(
        self,
        request: VideoGenerationRequest,
        job_id: str,
        *,
        provider: str,
        endpoint: str | None = None,
    ) -> None:
        """submit 成功后立即调：worker 路径写回 job_id，非 worker 路径（task_id=None）跳过。

        同时按维度分列写回该笔提交所用的端点信息：协议标识取 ``request.execution_endpoint``（由
        自定义供应商的包装层在转发前注入，内置供应商无此维度、恒 None），实际请求域名取参数
        ``endpoint``（由提交域名随用户配置变化的 backend 传入，只有 dashscope 协议这一条线）。
        两类供应商共用同一套写法，域名一律落 ``submitted_base_url``。持久化失败抛出（DB 瞬态错误
        已在 ``persist_provider_job_id`` 内重试 3 次），由 worker finally 兜底 mark_failed ——
        保持现有 fail-fast 语义（ADR 0007）。
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
    """HTTP 状态码 → 是否可重试。

    瞬态错误恒重试：408 Request Timeout / 425 Too Early / 429 Too Many Requests / 5xx。
    404 默认快速失败（确定性"不存在"，如端点拼错）；轮询/下载场景传 retry_not_found=True，
    按"任务提交后短暂未就绪 / 资源未传播"重试。其余 4xx（400/401/403/422 等）确定性客户端
    错误一律快速失败——重试只会拖到 max_wait 超时，白占 worker 槽。
    """
    if status_code in (408, 425, 429):
        return True
    if 500 <= status_code <= 599:
        return True
    if status_code == 404:
        return retry_not_found
    return False


# httpx 传输错误中「请求确定未送达」的子集：连接建立阶段失败 / 从未取得连接 / 代理握手失败。
# 重试安全——服务端不可能已建任务 / 已计费。读/写阶段及之后的传输错误（ReadTimeout、
# WriteError、连接中途断开、RemoteProtocolError 等）请求可能已抵达服务端，归歧义态，不在此列。
_NOT_SENT_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,  # 代理连接/握手失败：请求从未离开客户端→代理段，未抵达目标供应商
)

# 请求字节发出前就确定失败的本地/协议错误：URL scheme 不受支持（UnsupportedProtocol）
# 或本地请求构造违反协议（LocalProtocolError）。二者既无重复计费风险，重试到 max_wait
# 也不会变好——poll 路径快速失败、submit 路径原样抛出（非歧义态，不套「请求可能已送达」）。
# 注意 RemoteProtocolError 是其同级 ProtocolError 子类，但属「服务端中途断开」，不在此列。
_NON_RETRYABLE_LOCAL_ERRORS: tuple[type[Exception], ...] = (
    httpx.LocalProtocolError,
    httpx.UnsupportedProtocol,
)


def should_retry_submit(exc: Exception) -> bool:
    """创建/提交阶段（非幂等「创建 + 计费」POST）重试谓词。

    与 ``should_retry_poll`` 的关键区别：传输错误只重试「请求确定未送达」的子集
    （``_NOT_SENT_TRANSPORT_ERRORS``：连接建立失败 / 从未取得连接 / 代理握手失败），重试不会重复建
    任务、不会重复计费。歧义态（ReadTimeout 等「请求可能已被服务端处理」）由
    ``submit_post`` 包成 ``AmbiguousSubmitError`` 终态失败，本谓词对其（及一切业务异常）
    返回 False。HTTPStatusError 按 status_code 显式闸门：5xx/408/425/429 重试（服务端
    明示创建失败），404 与确定性 4xx 快速失败。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=False)
    return isinstance(exc, _NOT_SENT_TRANSPORT_ERRORS)


def should_retry_poll(exc: Exception) -> bool:
    """轮询/下载阶段（幂等 GET）重试谓词。

    幂等查询重试无副作用，故传输/网络错误（RequestError）与基础瞬态错误一律重试；唯本地/
    协议错误（``_NON_RETRYABLE_LOCAL_ERRORS``：UnsupportedProtocol / LocalProtocolError）在
    请求发出前就确定失败，重试到 max_wait 也不会变好，快速失败。HTTPStatusError 按
    status_code 闸门，404 视为"任务提交后短暂未就绪 / 资源未传播"重试。HTTPStatusError 消息
    含 URL/task_id，其中 "500"/"503" 子串会被字符串兜底误判，故走显式 status_code 判定绕开；
    ResumeExpiredError 等业务异常一律快速失败。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=True)
    if isinstance(exc, _NON_RETRYABLE_LOCAL_ERRORS):
        return False
    return isinstance(exc, (httpx.RequestError, *BASE_RETRYABLE_ERRORS))


def should_retry_signed_download(exc: Exception) -> bool:
    """预签发 URL 下载重试谓词：4xx 一律确定性失败。

    签名 URL 在签发那一刻即完整可用，403/404 只可能是签名错误或对象不存在，重试到 max
    也不会变好。只重试 5xx/408/425/429 与传输/网络错误（幂等 GET 重试无副作用）。
    HTTPStatusError 按 status_code 显式闸门，绕开字符串兜底对结果 URL 中 "503"/"timeout"
    等子串的误判。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=False)
    if isinstance(exc, _NON_RETRYABLE_LOCAL_ERRORS):
        return False
    return isinstance(exc, (httpx.RequestError, *BASE_RETRYABLE_ERRORS))


def should_retry_download(exc: Exception) -> bool:
    """视频产物下载重试谓词（幂等 GET 取 provider 任务成功后签发的结果 URL）。

    在 :func:`should_retry_signed_download` 之上额外重试 403/404：终态后产物尚未就绪、
    CDN 未同步是抽样中的真实形态（ark ``video_not_ready``），URL 本身写错由端点测试在保存
    前兜住。
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
    """create/提交阶段（非幂等 POST）统一包装：按「请求是否确定送达」给失败分流。

    - 连接/代理建立失败（ConnectError/ConnectTimeout/PoolTimeout/ProxyError）：请求确定未送达
      → 原样抛出，交 ``should_retry_submit`` 重试。
    - 本地/协议错误（UnsupportedProtocol/LocalProtocolError）：请求发出前就确定失败、无计费
      风险 → 原样抛出，由 ``should_retry_submit`` 快速失败（非歧义态，不套「请求可能已送达」）。
    - 其余传输错误（ReadTimeout/WriteError/RemoteProtocolError 等）：请求可能已被服务端
      处理 → 抛 ``AmbiguousSubmitError`` 终态失败，不重试，避免重复建任务 + 重复计费。
    - 收到 >=400 响应：先落 body 日志（诊断 413 等），再 ``raise_for_status`` 抛
      HTTPStatusError，交 ``should_retry_submit`` 按 status_code 分流。

    与 ``with_retry_async(retry_if=should_retry_submit)`` 配套使用：装饰器负责重试，
    本包装负责把歧义态在重试前转成不可重试的终态异常。

    传了 ``request`` 就把收到的响应体留痕。建任务失败发生在轮询开始之前，不在这里记就永远
    记不到——而那正是最需要供应商原文的一类失败。
    """
    try:
        resp = await post_fn()
    except httpx.RequestError as exc:
        # 请求确定未送达——连接建立失败（瞬态、可重试）或本地/协议错误（确定性、快速失败）——
        # 均无重复建任务 / 重复计费风险，原样抛出交 should_retry_submit 分流；不套歧义态。
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
    """按 path 逐层走 dict key / list 下标（int 段表 list 下标），任一层缺失返回 None。"""
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
    """按优先级逐个试取第一个非空字符串值（int 容忍并 str 化）。

    各家回包结构不一致时，用一张按优先级排序的路径表容错取值，而不是为每种形状写一条分支。
    """
    for path in paths:
        val = _dig(payload, path)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, int) and not isinstance(val, bool):
            return str(val)
    return None


def first_mapping_by_paths(payload: object, paths: tuple[tuple[str | int, ...], ...]) -> dict | None:
    """按优先级逐个试取第一个 dict 值；取不到返回 None。

    同 ``first_str_by_paths``，用于回包里成组的子结构（如 metadata）——形状随部署变化时
    与状态、视频地址走同一张优先级表，不各写一套形状分支。
    """
    for path in paths:
        val = _dig(payload, path)
        if isinstance(val, dict):
            return val
    return None


# 错误描述的常见落点：扁平 error 与包装体内的 data.error。
_ERROR_PATHS: tuple[tuple[str | int, ...], ...] = (("error",), ("data", "error"))


def extract_provider_error_message(state: object) -> str:
    """从回包里尽力取供应商错误描述（dict 取 message/name，或直接是字符串）；取不到返回 unknown。"""
    for path in _ERROR_PATHS:
        err = _dig(state, path)
        if isinstance(err, dict):
            # 两个字段各自判定：message 为空白或非字符串时仍要落到 name，别把回退一并跳过。
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
    """通用异步轮询辅助函数，带瞬态错误重试和超时控制。

    连续可重试错误（其间无一次成功响应）满 `VIDEO_POLL_MAX_CONSECUTIVE_FAILURES` 次即抛
    RuntimeError 终态失败，任一成功响应清零。重试等待按 `poll_interval × 2^k` 退避、封顶
    `VIDEO_POLL_MAX_BACKOFF_SECONDS`；响应带整数秒且不超过该封顶的 `Retry-After` 时优先采用。
    失败预算管「供应商不可达」，`max_wait` 管「供应商可达但慢」。任何一次等待都截到 `max_wait`
    的截止时刻，故最后一次轮询发出时必定仍在预算内。

    失败预算对全部消费方生效，视频与图片两条通道同此一份：图片侧的 `lib/backends/image_backends/vidu.py`
    与 `lib/backends/kling_backend_base.py` 同样在连续失败满额时终止，不会用满各自的 `max_wait` 窗口。

    Args:
        poll_fn: 每次轮询调用的异步函数，返回最新状态。
        is_done: 判断轮询结果是否表示任务完成。
        is_failed: 判断轮询结果是否表示任务失败，返回错误信息或 None。
        max_wait: 最大等待时间（秒），超时抛出 TimeoutError。
        poll_interval: 成功响应后的轮询间隔，同时是失败退避的基数；视频调用通道统一用默认 5 秒。
        retryable_errors: 可重试的异常类型元组（未指定 retry_if 时生效）。
        retry_if: 自定义重试谓词，指定时替代默认的 `_should_retry`，让调用方精确控制
            哪些异常应当重试（如按 HTTP status_code 区分确定性 4xx 与瞬态 5xx）。
        label: 日志前缀（如 "Ark"、"Gemini"）。
        on_progress: 可选的进度回调，每次非终态轮询后调用。
        clock: 单调计时与异步等待 seam；生产默认使用系统时钟。
    """
    active_clock = clock if clock is not None else SystemClock()
    start = active_clock.monotonic()
    prefix = f"{label} " if label else ""
    predicate = retry_if if retry_if is not None else (lambda e: _should_retry(e, retryable_errors))
    consecutive_failures = 0

    # 先查询再等待：已完成/缓存命中的任务立刻返回，不被 poll_interval 白等一轮。
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
        # 等待不越过剩余预算：下一次轮询必定发在 max_wait 截止时刻或之前。
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
    """(scheme, host, port) 三元组；端口按 scheme 补默认值，供同源判定使用。"""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None)
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


#: 手动跟随重定向时的跳数上限，与 httpx 的缺省一致。
_MAX_REDIRECTS = 20


def _redirect_location(response: httpx.Response) -> str | None:
    return response.headers.get("location") if response.is_redirect else None


def _rewrites_to_get(status_code: int, method: str) -> bool:
    """跟随重定向时该不该把方法改写成 GET 并丢掉请求体（与 httpx / RFC 9110 同规则）。

    303 对除 HEAD 外的一切方法改写；301 / 302 只改写 POST——PUT 等方法在这两档上保留方法
    与请求体，改写它们会让端点收到一个语义完全不同的请求。307 / 308 一律原样重发。
    """
    if status_code == 303:
        return method != "HEAD"
    if status_code in (301, 302):
        return method == "POST"
    return False


def _without_query(url: str) -> str:
    """去掉查询串与 userinfo 的 URL：按 query 传的凭证、签名与 Basic 认证的口令都在那里，
    不该进错误消息与日志。"""
    return str(httpx.URL(url).copy_with(query=None, userinfo=b""))


#: 拒因摘要的字符上限。上游错误体可以长达数千字符，而摘要要落进任务失败信封、随任务列表
#: 回传给用户，必须有界；超限部分以 :data:`_PROVIDER_REASON_ELLIPSIS` 收尾标记截断。
PROVIDER_REASON_MAX_CHARS = 300

_PROVIDER_REASON_ELLIPSIS = "…"

#: 错误体里承载「为什么被拒」的字段名，按顺序取第一个非空字符串。
_REASON_MESSAGE_KEYS = ("message", "msg", "error_msg", "detail", "description", "reason")

#: 错误对象常见的外层容器：拒因往往裹在这些键下面。
_REASON_NESTED_KEYS = ("error", "output", "data", "detail", "result")

#: 与拒因同层的机器码。它比自然语言更能指向被触发的那条拒绝规则，取到就前置进摘要。
_REASON_CODE_KEYS = ("code", "error_code", "type")

#: 认证类状态码：401 未认证、407 代理未认证。这类响应体不产出拒因摘要。
_CREDENTIAL_STATUS_CODES = frozenset({401, 407})

#: 容器下钻深度上限，防畸形错误体上的深递归。
_REASON_MAX_DEPTH = 3

#: URL 或任何带查询串的 URL-like token。供应商偶尔省略 scheme；query 仍可能是凭证或签名参数。
_REASON_URL_RE = re.compile(r"https?://[^\s\"'<>]+|[^\s\"'<>?]+\?[^\s\"'<>]+", re.IGNORECASE)


def _reason_code(body: Mapping[str, object]) -> str | None:
    """取与拒因同层的机器码；``bool`` 不算码（JSON 里 ``True`` 是标志位而非错误号）。"""
    for key in _REASON_CODE_KEYS:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _reason_text(payload: object, depth: int = 0) -> str | None:
    """从解析后的错误体里取「为什么被拒」那句话，取不到返回 ``None``。

    只认消息字段与少数外层容器，不整体序列化：错误体常把请求原样回显回来（含 prompt 全文），
    整体序列化会把它一并带进用户可见的摘要。
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
    """去掉文本里每个绝对 URL 的查询串、片段与 userinfo：预签名参数与按 query 传的凭证都在
    查询串里，而 ``https://TOKEN@host`` 这种把令牌塞进权限段的写法只有整段清掉才拦得住——
    随后的通用脱敏只认得出 ``user:password@`` 形态，遮不住没有冒号的裸令牌。"""

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
    """供应商原文的脱敏形态：先剥掉每个 URL 的查询串，再遮蔽凭证。

    两层缺一不可：``redact_diagnostic_text`` 只认得出常见的凭证参数名，预签名 URL 上
    的厂商私有参数要靠查询串整体剥离兜住；而查询串之外的凭证回显只有前者认得出。
    """
    if not isinstance(value, str):
        # 非字符串只出现在留痕的兜底分支：没有 URL 文本可剥，交通用遮蔽器统一 str 化
        return redact_diagnostic_text(value)
    return redact_diagnostic_text(_strip_url_queries(value))


def is_provider_rejection(status_code: int) -> bool:
    """该状态码是否算「提交被上游拒绝」：确定性 4xx，不含瞬态可重试的那几个。

    与摘要有无无关——拒绝这件事本身决定读侧的文案与后续动作（修改输入而非重试）。
    """
    return 400 <= status_code < 500 and not is_retryable_http_status(status_code, retry_not_found=False)


def provider_reason_summary(response: httpx.Response) -> str | None:
    """把上游确定性 4xx 的响应体提炼成脱敏、截断后的拒因摘要；无可用内容返回 ``None``。

    只对 4xx 产出：5xx 与传输错误说的是「上游此刻不可用」，其响应体对用户没有可行动的信息，
    并入错误信息只会把一次瞬态故障写成看似确定的拒绝。

    脱敏分两层：先去掉每个 URL 的查询串（预签名参数与按 query 传的凭证都在那里，与
    ``_without_query`` 同口径），再交 ``redact_diagnostic_text`` 遮蔽 Authorization / Bearer /
    引号包裹的 secret 等值，与日志侧共用一套规则。
    """
    if not is_provider_rejection(response.status_code):
        return None
    if response.status_code in _CREDENTIAL_STATUS_CODES:
        # 认证失败的响应体讲的就是凭证本身，上游常把整把密钥回显在句子里；这类拒因除了
        # 「凭证没被接受」没有别的可行动信息，而那一层已由本地化文案的状态码给出。
        return None
    try:
        body_text = response.text
    except httpx.ResponseNotRead:
        # 流式响应尚未 aread：错误路径上没有可读的体，不为此再发一次请求。
        return None
    try:
        payload: object = response.json()
    except ValueError:
        reason = body_text
    else:
        reason = _reason_text(payload)
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
    """一次请求的目标与随行凭证。跨源跳转时把凭证整个卸掉。"""

    url: str
    headers: Mapping[str, str] | None
    params: Mapping[str, str] | None = None
    #: 按 query 传的凭证。``Location`` 会整体替换查询串，同源续跳时要重新贴回去。
    auth_query: Mapping[str, str] | None = None

    def redirected_to(self, location: str, credential_origin: tuple[str, str, int | None]) -> _Hop:
        target = httpx.URL(self.url).join(location)
        if url_origin(str(target)) != credential_origin:
            # 跨源：请求头、查询凭证与原请求的 params 一并卸掉。
            return _Hop(str(target), None, None, None)
        # 同源：凭证仍在作用域内。原请求的 params 不重放（重定向目标自带查询串），但按 query
        # 传的凭证必须补回——否则一次 `/jobs` → `/jobs/` 的规范化跳转就会丢掉 api_key。
        # 合并进目标 URL 的查询串而不是走 params：后者会整串替换，把 Location 自带的参数冲掉。
        if self.auth_query:
            target = target.copy_merge_params(dict(self.auth_query))
        return _Hop(str(target), self.headers, None, self.auth_query)


@dataclass(frozen=True)
class _Body:
    """一次请求的请求体。跟随重定向改写成 GET 时整个丢掉，三个字段不会各丢一半。"""

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
    """发一次请求并自行逐跳跟随重定向，跳出本请求的源时卸掉 ``headers`` 里的凭证。

    httpx 的 ``follow_redirects`` 跨源只摘 ``Authorization``，自定义头名（``X-API-Key`` 之类）
    会原样送到重定向目标。凡是携带渲染出的 auth 节的请求都要走这里，而不是交给客户端自动跟随。

    凭证的作用域取 ``url`` 自己的源，而不是某个外部基准：调用方指定的地址就是凭证的去处，
    需要防的是服务端用 ``Location`` 把它引到别处。

    ``files`` / ``data`` 走 multipart 的请求体，与 ``json`` 三选一；续跳时原样重发，故 ``files``
    的内容要是字节而不是文件句柄——句柄读到结尾后第二跳会发出一个空体。
    """
    credential_origin = url_origin(url)
    # 首跳的 auth.query 已经拼在 url 上，params 不重复带；只在同源续跳时补回。
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
    raise RuntimeError(f"request exceeded {_MAX_REDIRECTS} redirects: {_without_query(url)}")


async def stream_to_file(
    client: httpx.AsyncClient,
    url: str,
    output_path: Path,
    *,
    max_bytes: int,
    timeout: int = 120,  # noqa: ASYNC109 -- 转交 httpx 的 I/O 超时配置，非 async 取消 deadline
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    credential_origin: tuple[str, str, int | None] | None = None,
    auth_query: Mapping[str, str] | None = None,
) -> None:
    """把 URL 内容流式写入本地文件，不含重试——重试由 :func:`with_artifact_retry` 统一承担。

    响应体超过 ``max_bytes`` 即中止并抛 :class:`ArtifactTooLargeError`，产物路径上不留残片；
    错误响应的响应体只读前 ``ERROR_BODY_MAX_BYTES`` 字节。出站目的地由调用方传入的 ``client``
    约束（见 :func:`lib.backends.artifact_download_guard.artifact_http_client`）。

    ``credential_origin`` 给出 ``headers`` 里的凭证只许发往哪个源。给了它就自行逐跳跟随
    重定向，跳到别的源时把 ``headers`` 整个丢掉：httpx 跨源只摘 ``Authorization``，而端点
    定义的 auth 节可以用 ``X-API-Key`` 之类的任意头名，交给 ``follow_redirects`` 会把这些
    凭证原样送到重定向目标（对象存储 / CDN）去。
    """

    async def _write(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            # 换成读完（且截断）响应体的副本，HTTPStatusError.response.text 才可用
            resp = await buffered_error_response(resp)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 产物地址的查询串可能是签名，也可能是按 query 传的凭证——都不该进日志与任务记录。
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
                # 3xx 没给 Location 就无处可跳：raise_for_status 放行 3xx，直接落盘会把跳转
                # 响应体当成产物存下来。抛错交给重试预算。
                if resp.is_redirect:
                    raise RuntimeError(f"redirect without a Location header: {_without_query(hop.url)}")
                await _write(resp)
                return
        hop = hop.redirected_to(location, credential_origin)
    raise RuntimeError(f"artifact download exceeded {_MAX_REDIRECTS} redirects: {_without_query(url)}")


#: 产物下载的墙钟上限。终止主要由失败预算负责（连续 10 次 + 退避封顶 60s，累计约 435s），
#: 本值只兜住「每次都连得上、只是慢」的情形。视频通道传 `poll_timeout_seconds` 覆盖它，
#: 图片 / 音频通道没有该维度，用本缺省。
ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS = 1800


async def with_artifact_retry[T](
    attempt: Callable[[], Awaitable[T]],
    *,
    label: str,
    retry_if: Callable[[Exception], bool] | None = should_retry_download,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    max_wait: float = ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
) -> T:
    """按与轮询共用的预算重试一次产物取件。

    产物下载与轮询同属「供应商任务已建成后的幂等取件」，故用同一套终止条件：连续失败满
    ``VIDEO_POLL_MAX_CONSECUTIVE_FAILURES`` 次即终态失败、指数退避封顶
    ``VIDEO_POLL_MAX_BACKOFF_SECONDS``、响应带 ``Retry-After`` 时优先采用。全部调用通道
    （内置视频 / 图片 / 音频与声明式运行时）共用本入口及同一份下载重试常量。

    HTTP 式通道用缺省的 ``should_retry_download`` 按 status_code 闸门；SDK 式通道的下载
    异常不是 ``HTTPStatusError``，传 ``retry_if=None`` 退到按 ``retryable_errors`` 判定。
    """

    # 单元素元组包住取件结果：`poll_with_retry` 按 is_done 判终态，而取件只要没抛异常就是
    # 成功，结果本身（None、空 bytes 等）不参与判定。
    async def once() -> tuple[T]:
        return (await attempt(),)

    # 墙钟上限套在整个重试之外：`poll_with_retry` 只在每次 poll_fn 返回后才比对 max_wait，
    # 而一次取件本身就可能长时间不返回（连得上、只是慢），那正是本上限要兜的情形。
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


async def download_video(
    url: str,
    output_path: Path,
    *,
    label: str = "",
    timeout: int = 120,  # noqa: ASYNC109 -- 转交 httpx 的 I/O 超时配置，非 async 取消 deadline
    retry_if: Callable[[Exception], bool] | None = should_retry_download,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    max_wait: float = ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
) -> None:
    """从 URL 流式下载视频到本地文件，重试走共用的产物下载预算。"""

    async def attempt() -> None:
        async with artifact_http_client(follow_redirects=True) as http_client:
            await stream_to_file(http_client, url, output_path, max_bytes=VIDEO_ARTIFACT_MAX_BYTES, timeout=timeout)

    await with_artifact_retry(
        attempt, label=label, retry_if=retry_if, retryable_errors=retryable_errors, max_wait=max_wait
    )


async def download_resumable_video(url: str, output_path: Path, *, label: str) -> None:
    """下载可续跑任务的成片；预算耗尽转成可重试下载的稳定失败。

    ``NonRetryableError``（目的地不合规、超出体积上限）原样抛出：重新取件结果不会变。
    """
    try:
        await download_video(url, output_path, label=label)
    except NonRetryableError:
        raise
    except Exception as exc:
        raise ArtifactDownloadError(detail=str(exc)) from exc


def reference_audio_to_data_uri(path: Path, *, model: str, mime_types: Mapping[str, str]) -> str:
    """参考音频 → base64 data URI；格式不受支持或文件不可读一律抛错。

    音频不能像参考图那样「缺失即跳过」：prompt 里的「音频N」按 content 数组中音频条目的
    出现顺序编号，跳过一段会让其后所有编号整体前移，把某个角色的音色安到另一个角色头上
    ——错得无声无息，且照常扣费。

    ``mime_types`` 由各 backend 传入：同一个扩展名各家接受的 MIME 写法不一致（mp3 有
    ``audio/mp3`` 与 ``audio/mpeg`` 两种口径），合表会让其中一家收到没验证过的 MIME。
    """
    # 只有视频调用通道会调到这里；延迟导入让图像、音频通道使用运行支持时无需加载视频契约。
    from lib.backends.video_backend_contract import VideoCapabilityError

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
    """把 HTTP 式调用通道的供应商响应及其阶段送到可选诊断回调。

    留痕是诊断数据，不参与业务解析：写入失败只记日志，不让一笔已被供应商受理（多半已计费）
    的生成因为诊断列写不进去而失败。

    响应原文在这里脱敏一次：留痕会落库并经 API 回读，而上游响应体可能带回签名 URL 或把
    凭证回显在字段里。收口在本函数而非各调用点，所有留痕点都共享同一套脱敏。逐字符串
    的口径与拒因摘要、失败日志同为 ``redact_provider_text``：通用遮蔽器只认得出常见的凭证
    参数名，厂商私有的查询参数要靠查询串整体剥离兜住。
    """
    if request.on_provider_response is None:
        return
    try:
        await request.on_provider_response(stage, sanitize_diagnostic_payload(body, redact_text=redact_provider_text))
    except Exception:
        logger.warning("供应商响应留痕写入失败 task_id=%s", request.task_id, exc_info=True)


def recording_poll[T](
    poll_fn: Callable[[], Awaitable[T]],
    request: VideoGenerationRequest,
    *,
    stage: ProviderResponseStage = "poll",
) -> Callable[[], Awaitable[T]]:
    """包一层轮询或二次取件：每收到一次供应商响应就留痕，早于状态与错误解读。

    终态失败由 ``is_failed`` 谓词在 :func:`poll_with_retry` 内部抛出、HTTP 错误响应则从
    ``poll_fn`` 自己抛出，两者都发生在 ``poll_with_retry`` 返回之前。留痕若放在轮询之后，
    恰好只在成功调用上留下，最需要诊断的失败调用反而为空。
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


def _response_body_or_text(response: httpx.Response) -> object:
    """响应体：能解析成 JSON 就存结构，否则存原文（截断由留痕边界统一负责）。"""
    try:
        return response.json()
    except ValueError:
        return response.text
