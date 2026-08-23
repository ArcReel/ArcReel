"""视频生成服务层核心接口定义与共享工具。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

import httpx
from sqlalchemy.exc import InterfaceError, OperationalError

from lib.data_uri import file_to_data_uri
from lib.retry import BASE_RETRYABLE_ERRORS, AsyncClock, SystemClock, _should_retry, with_retry_async

# `_should_retry` 默认会做字符串模式兜底（"timeout"/"503" 等），
# 而 persist 重试要严格"DB 瞬态错误"语义——业务异常（如
# `ValueError("Connection timed out: rate")`）不该被字符串子串吞掉。
# 显式传 `retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS)` 关掉兜底。

logger = logging.getLogger(__name__)


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


@with_retry_async(
    max_attempts=3,
    backoff_seconds=_PERSIST_BACKOFF_SECONDS,
    retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS),
)
async def _persist_with_retry(task_id: str, job_id: str, endpoint: str | None, base_url: str | None) -> None:
    from lib.generation_queue import get_generation_queue

    await get_generation_queue().persist_provider_job_id(task_id, job_id, endpoint=endpoint, base_url=base_url)


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
    统一写回，不再各自手写「``if request.task_id is not None`` → 调模块级 ``persist_provider_job_id``」
    那套形态；持久化时机（submit 后、poll 前）、None 跳过（非 worker 路径）、fail-fast 语义集中
    于此单一调用点。新增提交-轮询型 backend 继承本 mixin 即得能力，无需自己记得调持久化。

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


@with_retry_async(
    max_attempts=3,
    backoff_seconds=_PERSIST_BACKOFF_SECONDS,
    retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS),
)
async def _persist_api_call_id_with_retry(task_id: str, call_id: int) -> None:
    from lib.generation_queue import get_generation_queue

    await get_generation_queue().persist_api_call_id(task_id, call_id)


async def persist_api_call_id(task_id: str, call_id: int) -> None:
    """Start_call 拿到 call_id 后立即调：把 ApiCall.id 写入 task.payload。

    Resume 路径据此精准翻 pending ApiCall 行而不是按 segment_id+LIMIT 1 模糊匹配。
    与 ``persist_provider_job_id`` 同样走 DB 瞬态错误重试；重试用尽抛异常，由
    media_generator 的外层 try/except 走 finish_call(failed) 翻 pending ApiCall，
    并把异常冒泡给 worker finally 兜底 mark_failed（ADR 0007 fail-fast：未持久化
    的 submit 视为整笔失败——provider 端尚未提交，无需担心「幽灵任务」；若已提交
    则 resume 拿不到 api_call_id 锚定将永远留 pending 账目，必须 fail-fast 让记账
    在原地翻 failed 而不是延后到永远不会发生的 resume）。
    """
    try:
        await _persist_api_call_id_with_retry(task_id, call_id)
        logger.info("api_call_id 已持久化 task_id=%s call_id=%d", task_id, call_id)
    except Exception as exc:
        logger.error(
            "api_call_id_persist_failed task_id=%s call_id=%d error=%s",
            task_id,
            call_id,
            exc,
        )
        raise


class ResumeExpiredError(RuntimeError):
    """Provider 端 job 已过期或未找到——重启自愈无法接续，须走 mark_failed。

    Worker finally 据 ``isinstance(exc, ResumeExpiredError)`` 给 error_message
    加 ``[resume_expired]`` 前缀（agent-facing，i18n 豁免），运维分析可见。
    """

    def __init__(self, *, job_id: str, provider: str, message: str = "") -> None:
        self.job_id = job_id
        self.provider = provider
        super().__init__(message or f"resume job {job_id} expired or not found on provider {provider}")


class ResumeEndpointChangedError(RuntimeError):
    """提交本 job 时的 endpoint 与模型行当下的 endpoint 不同——续跑必须显式失败。

    endpoint 决定协议，换 endpoint 等于换 backend；拿新协议 backend 轮旧协议下创建的 job
    会误读响应，把仍在跑仍在计费的远端 job 标成失败。ADR 0054「换身份续跑必须显式报错」在
    endpoint 维度的落点：只拦已提交、持有 job_id 的续跑，排队未提交的任务照常按新 endpoint
    提交。仅自定义供应商有该维度。
    """

    def __init__(self, *, job_id: str, provider: str, submitted_endpoint: str, current_endpoint: str) -> None:
        self.job_id = job_id
        self.provider = provider
        self.submitted_endpoint = submitted_endpoint
        self.current_endpoint = current_endpoint
        super().__init__(
            f"resume job {job_id} was submitted via endpoint {submitted_endpoint} on provider {provider}, "
            f"but the model row now points to {current_endpoint}"
        )


# 图片后缀 → MIME 类型映射（多个后端共用）
IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


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


class AmbiguousSubmitError(RuntimeError):
    """create/submit（非幂等的「创建 + 计费」）阶段的歧义态失败。

    请求可能已抵达服务端并已落库 + 已计费，但响应在途丢失（ReadTimeout、写超时、
    连接中途断开、RemoteProtocolError 等）。此时自动重试会重复建任务 + 重复计费，故
    不重试、直接终态失败；error_message 带 ``[create_ambiguous]`` 前缀提示运维到供应商侧
    确认任务状态后再手动重试（与 ``[restart_lost]`` / ``[resume_unsupported]``
    「宁可手动重试、不可重复计费」先例一致，agent-facing 豁免 i18n）。
    """

    def __init__(self, *, provider: str, message: str = "") -> None:
        self.provider = provider
        super().__init__(
            message
            or f"[create_ambiguous] {provider} 创建请求可能已送达服务端但响应在途丢失"
            "（读超时/连接中途断开），为避免重复建任务与重复计费不自动重试；"
            "请到供应商侧确认任务状态后再手动重试"
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


def should_retry_download(exc: Exception) -> bool:
    """下载阶段（幂等 GET 取 provider 任务成功后签发的结果 URL）重试谓词。

    与 ``should_retry_poll`` 的唯一区别在 404：下载 URL 由 provider 直接签发，4xx（404 不存在 /
    403 过期 / 413 等）是确定性错误，重试到 max 也不会变好，故 ``retry_not_found=False`` 让 404
    随其余 4xx 一并 fail-fast（轮询的 404 才是"任务短暂未就绪"该重试）。5xx/408/425/429 与传输/
    网络错误（幂等 GET 重试无副作用）重试。HTTPStatusError 按 status_code 显式闸门，绕开字符串
    兜底对结果 URL 中 "503"/"timeout" 等子串的误判。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code, retry_not_found=False)
    if isinstance(exc, _NON_RETRYABLE_LOCAL_ERRORS):
        return False
    return isinstance(exc, (httpx.RequestError, *BASE_RETRYABLE_ERRORS))


async def submit_post(
    post_fn: Callable[[], Awaitable[httpx.Response]],
    *,
    provider: str,
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
    """
    try:
        resp = await post_fn()
    except httpx.RequestError as exc:
        # 请求确定未送达——连接建立失败（瞬态、可重试）或本地/协议错误（确定性、快速失败）——
        # 均无重复建任务 / 重复计费风险，原样抛出交 should_retry_submit 分流；不套歧义态。
        if isinstance(exc, (*_NOT_SENT_TRANSPORT_ERRORS, *_NON_RETRYABLE_LOCAL_ERRORS)):
            raise
        raise AmbiguousSubmitError(provider=provider) from exc
    if resp.status_code >= 400:
        logger.warning("%s create 返回 %s: %s", provider, resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp


class ProviderJobStatus(StrEnum):
    """供应商异步任务状态的 canonical 分档。

    ``EXPIRED`` 独立于 ``FAILED``：OpenAI / NewAPI 两条链路据其按 generate / resume 上下文
    分流抛 ``RuntimeError`` / ``ResumeExpiredError``，后者驱动 worker 的 ``[resume_expired]``
    前缀与「不再尝试重启自愈」判定。折进 failed 会静默吃掉这条分流。没有过期语义的端点
    （如流派 C ``/v2/video/generations``）在本分档之上自行折叠。
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL_PROVIDER_STATUSES: frozenset[ProviderJobStatus] = frozenset(
    {ProviderJobStatus.SUCCEEDED, ProviderJobStatus.FAILED, ProviderJobStatus.EXPIRED}
)

# 跨厂商状态同义词表（lowercase + strip 后查表）。OpenAI 兼容代理网关转发非原生型号时会把
# 底层厂商的状态串原样透传，各后端若只认自家文档里的字面量，已就绪的任务会被当成"仍在跑"
# 一路轮询到 max_wait —— 用户侧报超时失败，供应商侧成品已生成且已计费。
_PROVIDER_STATUS_SYNONYMS: dict[str, ProviderJobStatus] = {
    "completed": ProviderJobStatus.SUCCEEDED,
    "succeeded": ProviderJobStatus.SUCCEEDED,
    "succeed": ProviderJobStatus.SUCCEEDED,
    "success": ProviderJobStatus.SUCCEEDED,
    "failed": ProviderJobStatus.FAILED,
    "fail": ProviderJobStatus.FAILED,
    "error": ProviderJobStatus.FAILED,
    "canceled": ProviderJobStatus.FAILED,
    "cancelled": ProviderJobStatus.FAILED,
    "expired": ProviderJobStatus.EXPIRED,
    "generating": ProviderJobStatus.RUNNING,
    "in_progress": ProviderJobStatus.RUNNING,
    "running": ProviderJobStatus.RUNNING,
    "processing": ProviderJobStatus.RUNNING,
    "queued": ProviderJobStatus.QUEUED,
    "queueing": ProviderJobStatus.QUEUED,
    "preparing": ProviderJobStatus.QUEUED,
    "submitted": ProviderJobStatus.QUEUED,
    "pending": ProviderJobStatus.QUEUED,
    "created": ProviderJobStatus.QUEUED,
}


def normalize_provider_status(raw: object) -> ProviderJobStatus:
    """任意供应商状态值 → canonical 分档（大小写与首尾空白无关）。

    未登记的状态串一律当 ``RUNNING`` 继续轮询：把未知串判成终态，会让返回非标进行中状态
    （如 ``NOT_START``）的网关触发"下载未就绪任务"。非字符串（缺字段 / None）同理。
    """
    if not isinstance(raw, str):
        return ProviderJobStatus.RUNNING
    return _PROVIDER_STATUS_SYNONYMS.get(raw.strip().lower(), ProviderJobStatus.RUNNING)


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
    poll_interval: float,
    max_wait: float,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    retry_if: Callable[[Exception], bool] | None = None,
    label: str = "",
    on_progress: Callable[[T, float], None] | None = None,
    clock: AsyncClock | None = None,
) -> T:
    """通用异步轮询辅助函数，带瞬态错误重试和超时控制。

    Args:
        poll_fn: 每次轮询调用的异步函数，返回最新状态。
        is_done: 判断轮询结果是否表示任务完成。
        is_failed: 判断轮询结果是否表示任务失败，返回错误信息或 None。
        poll_interval: 两次轮询之间的间隔（秒）。
        max_wait: 最大等待时间（秒），超时抛出 TimeoutError。
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

    # 先查询再等待：已完成/缓存命中的任务立刻返回，不被 poll_interval 白等一轮。
    while True:
        try:
            result = await poll_fn()
        except Exception as e:
            if not predicate(e):
                raise
            logger.warning("%s轮询异常（将重试）: %s - %s", prefix, type(e).__name__, str(e)[:200])
        else:
            error_msg = is_failed(result)
            if error_msg is not None:
                raise RuntimeError(error_msg)
            if is_done(result):
                return result
            if on_progress is not None:
                on_progress(result, active_clock.monotonic() - start)

        if active_clock.monotonic() - start >= max_wait:
            raise TimeoutError(f"{prefix}任务超时（{max_wait:.0f}秒）")
        await active_clock.sleep(poll_interval)


@with_retry_async()
async def download_video(url: str, output_path: Path, *, timeout: int = 120) -> None:
    """从 URL 流式下载视频到本地文件（含瞬态错误重试）。"""
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    async with httpx.AsyncClient() as http_client:
        async with http_client.stream("GET", url, timeout=timeout) as resp:
            if resp.status_code >= 400:
                # 流式模式下需先读取响应体，否则 HTTPStatusError.response.text 不可用
                await resp.aread()
            resp.raise_for_status()
            # 异步流式读取所有 chunk，然后一次 to_thread 完成整段写入，
            # 避免对每个 64KB 分片调度一次线程池任务。
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                chunks.append(chunk)

            def _write_all() -> None:
                with open(output_path, "wb") as f:
                    for chunk in chunks:
                        f.write(chunk)

            await asyncio.to_thread(_write_all)


class VideoCapabilityError(RuntimeError):
    """视频后端能力不匹配（如 duration ↔ supported_durations）。

    与 ImageCapabilityError 对称：不携带本地化字符串，只带稳定 code + 上下文 params；
    路由层直接 _t(code, **params) 渲染，Worker 则按 code + params 落 task.error_message，
    文案留到读侧按 Accept-Language 渲染。
    """

    def __init__(self, code: str, **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


def reference_audio_to_data_uri(path: Path, *, model: str, mime_types: Mapping[str, str]) -> str:
    """参考音频 → base64 data URI；格式不受支持或文件不可读一律抛错。

    音频不能像参考图那样「缺失即跳过」：prompt 里的「音频N」按 content 数组中音频条目的
    出现顺序编号，跳过一段会让其后所有编号整体前移，把某个角色的音色安到另一个角色头上
    ——错得无声无息，且照常扣费。

    ``mime_types`` 由各 backend 传入：同一个扩展名各家接受的 MIME 写法不一致（mp3 有
    ``audio/mp3`` 与 ``audio/mpeg`` 两种口径），合表会让其中一家收到没验证过的 MIME。
    """
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


class ReferenceAudioMode(StrEnum):
    """后端接受参考音频的运输形态。

    ``DIRECT`` 表示随生成请求直传音频文件，模型据其复刻音色（Seedance 2.0 的
    ``role: reference_audio`` content 条目、Wan2.7 r2v 挂在参考素材项上的
    ``reference_voice``）。``NONE`` 表示该后端没有音色输入通道——带音频的请求在
    ``gate_video_request`` 处硬失败，不静默丢弃。
    """

    NONE = "none"
    DIRECT = "direct"


class VideoAudioMode(StrEnum):
    """成片音轨与音轨开关的三态。

    ``CONTROLLABLE`` 表示请求携带音轨开关，用户的开/关意图能抵达供应商；``ALWAYS_ON`` 表示
    成片必然带音轨而请求里没有开关可下发（关闭意图必然落空）；``ALWAYS_OFF`` 表示该路径不产
    音轨、也没有开关（开启意图必然落空）。

    与 ``reference_audio_mode`` 是两回事：后者描述**输入**通道（能否给模型一段音色参考），本
    枚举描述**输出**音轨。取值与前端 ``VideoAudioControl`` 字面量一一对应，两侧不各自归并。
    """

    CONTROLLABLE = "controllable"
    ALWAYS_ON = "always_on"
    ALWAYS_OFF = "always_off"


#: 视频执行路径（任务类型桶）：``i2v`` 覆盖文生与图生首帧，``r2v`` 是参考生视频。
#: 与 ``lib.config.resolver.VideoCapability`` 同一份词汇表，因分层契约（config 是最底层，
#: backend 不得反向导入）而各层各声明一次，取值一致由
#: ``tests/test_video_backend_capabilities.py`` 的守卫锁定。
VideoRoute = Literal["i2v", "r2v"]


@dataclass
class VideoCapabilities:
    """Declares what a video backend supports.

    ``first_frame`` / ``last_frame`` 描述图生视频路径的首帧与尾帧槽位。
    ``max_reference_images`` 描述参考生视频路径：后端接受 ``reference_images`` 请求字段
    的数量上限，``> 0`` 即该路径可用（不另设布尔位——两份声明会漂移出「称支持但上限为 0」
    这类自相矛盾的状态）。两条路径是否可叠加（同一请求同时带首帧与参考图）因后端而异，
    不是统一契约：部分后端拒绝叠加（如 Agnes 抛 ``VideoCapabilityError``），部分静默叠加
    （如 v2 中转、Grok、Sora 首帧与参考共享单槽）。调用方不应假设某种统一行为，需按具体
    后端核实。

    ``audio_track`` / ``reference_route_audio_track`` 描述**成片音轨**（有无音轨、开关是否可
    控），是该维度的唯一真相源——与请求构造同源，backend 是否往请求体里放音轨开关就是这一位
    的字面含义。两条执行路径各声明一次，与 ``first_frame`` / ``max_reference_images`` 把两条
    路径摊平进同一个对象同构：``reference_route_audio_track`` 为 None 表示参考生视频路径与
    ``audio_track`` 同形（绝大多数 backend 如此），非 None 时表示该路径的请求形态另有一套音轨
    行为（可灵 v3-omni 的多图主体子路径原生 schema 不含音轨开关，故该路径恒无声）。默认取
    ``CONTROLLABLE``——未声明即「无信号不收紧」，不把能力不明的 model 谎报成开关失效。
    取值请走 :meth:`audio_track_for_route`，不要直接读字段，否则每个调用方都要重写一遍
    「参考生视频优先」的合并规则。

    ``reference_audio_mode`` / ``max_reference_audio_count`` 描述参考音频路径，与参考图
    同构：模式非 ``NONE`` 时后端接受 ``reference_audio_files`` 请求字段，段数受上限约束。
    上限按 backend 各自的供应商约束声明，不取各家交集。

    ``reference_audio_per_image``：音频是否必须逐段挂在某个具体的参考素材项上（如 wan2.7-r2v
    的 ``reference_voice`` 字段），而非作为独立的音色输入通道（如 Seedance 2.0 的
    ``role: reference_audio`` content 条目）。为 True 时调用方须随 ``reference_audio_files``
    一并提供 ``VideoGenerationRequest.reference_audio_targets``，显式声明每段音频对应哪个
    参考素材项，不能假设两个列表天然同序——参考音频的编排顺序是台词 speaker 首现顺序，
    参考图的编排顺序是 mention 首现顺序，两者独立派生，位置对齐纯属巧合。

    ``max_reference_audio_total_seconds``：多段参考音频叠加的总时长上限（None = 该后端未声明
    聚合约束，仅按 ``max_reference_audio_count`` 卡段数）。段数上限推不出总时长——两段各处于
    单段合法区间的音频，合计仍可能超出供应商总时长上限，故需独立声明。判定需要读音频元数据，
    调用方在 :func:`lib.video_frame_slots.gate_video_request` 前置探测好总时长再传入。

    ``max_prompt_chars``：提示词字符数上限（None = 该后端未声明约束）。声明的是**该 model 无论
    走哪条端点都成立**的上限——部分供应商按端点各设更窄的值（如 Vidu 的参考生视频端点），那层
    收窄留在 backend 组装期按实际端点 fail-loud，不塞进这个无端点上下文的静态声明里。计量口径
    为字符数（中英文同权），与各家文档一致。超限的典型失败模式是静默截断而非报错：供应商照常
    扣费、成片与意图不符、用户无从知情，正是 :func:`lib.video_frame_slots.gate_video_request`
    要在付费前堵住的降级。

    ``first_frame_ratio_adaptive_only``：该模型的首帧（image-to-video）任务是否只接受
    "adaptive" 比例。声明为 True 时，:func:`lib.video_frame_slots.resolve_first_frame_aspect_ratio`
    把带首帧的生成请求的 ``VideoGenerationRequest.aspect_ratio`` 改写为字面量 ``"adaptive"``；
    不带首帧的请求（纯文生 / 仅参考图）与续接已发起 job 的 resume 路径不受影响。该字面量是供应商
    侧的取值，只对认得它的 backend 有意义，故本位只由这类 backend 声明——别处（如
    :func:`lib.aspect_size.parse_aspect_ratio`）解析不了它，会按非法值回退默认比例。

    「首帧在场时用户比例不适用」这一情形另有 backend 各自的表达方式：dashscope 与 vidu 在
    payload 组装期直接不下发 ratio（上游忽略或拒收）。三者形状相近而取值策略不同（省略 vs 改写
    为 adaptive），未收敛到同一开关；本位表达的是"改写为 adaptive"这一支。

    用户的比例意图仍完整作用于分镜图生成——首帧图本就按该比例生成，"跟随首帧"与用户所选比例
    等价；改写只影响视频请求实际下发的值，不改调用方持有的原始 ``aspect_ratio``（记账、版本
    元数据沿用后者）。
    """

    first_frame: bool = True
    last_frame: bool = False
    max_reference_images: int = 0
    reference_audio_mode: ReferenceAudioMode = ReferenceAudioMode.NONE
    max_reference_audio_count: int = 0
    max_reference_audio_total_seconds: float | None = None
    reference_audio_per_image: bool = False
    max_prompt_chars: int | None = None
    first_frame_ratio_adaptive_only: bool = False
    audio_track: VideoAudioMode = VideoAudioMode.CONTROLLABLE
    reference_route_audio_track: VideoAudioMode | None = None

    def audio_track_for_route(self, route: VideoRoute) -> VideoAudioMode:
        """该执行路径上成片音轨的实际形态。

        参考生视频路径未单独声明时跟随 ``audio_track``——两条路径同形是常态，逐 backend 重复
        声明只会多出一份可漂移的副本。
        """
        if route == "r2v" and self.reference_route_audio_track is not None:
            return self.reference_route_audio_track
        return self.audio_track


@dataclass
class VideoGenerationRequest:
    """通用视频生成请求。各 Backend 忽略不支持的字段。"""

    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5
    resolution: str | None = None
    start_image: Path | None = None
    end_image: Path | None = None  # For first_last mode
    reference_images: list[Path] | None = None  # For multi-reference mode
    # 参考音频（音色复刻）。列表顺序即 prompt 中「音频N」的指认契约：编排层按该顺序拼指认
    # 文本，后端按同一顺序下发，故任何一侧都不得重排或跳过。哪个角色对应哪段音频不进请求
    # ——绑定由 prompt 文本表达，供应商 API 均无结构化的「角色-音频」字段。
    reference_audio_files: list[Path] | None = None
    # 仅 ``VideoCapabilities.reference_audio_per_image`` 为 True 的 backend（如 wan2.7-r2v）
    # 读取：与 ``reference_audio_files`` 等长同序，第 i 项是该段音频对应的
    # ``reference_images`` 下标（0-based）。为 None 时这类 backend 按位置回退对齐，仅用于
    # 未经编排层填充的调用方（如手写测试）——参考音频与参考图各自独立派生顺序，位置对齐
    # 不构成契约，编排层（reference_video 渲染管线）必须显式提供。
    reference_audio_targets: list[int] | None = None
    generate_audio: bool = True

    # 项目上下文（用于构建文件服务 URL 等）
    project_name: str | None = None

    # Worker 路径下从 task["task_id"] 传入，让 backend submit 后经
    # `ProviderJobIdPersistenceMixin._persist_provider_job_id` 持久化 job_id。
    # 非 worker 路径（grid / 直生 / 测试）保持 None，统一点据此跳过持久化。
    task_id: str | None = None

    # MediaGenerator uses this one-way signal to close its compression-retry window. Resumable backends signal
    # after the provider job handle is durable; an opaque submit-and-wait backend must signal before entering a
    # call whose failure cannot prove that the provider rejected the request before accepting a paid job.
    on_provider_resubmit_unsafe: Callable[[], None] | None = None

    # 自定义供应商包装层（`CustomVideoBackend`）在转发给协议 backend 前注入的协议标识，与 job_id
    # 一并持久化到 `tasks.provider_endpoint`，记录本笔供应商任务的协议归属。内置供应商无此维度，
    # 保持 None。续跑比对协议读的是 checkpoint 的 endpoint_guard，不读该列。
    execution_endpoint: str | None = None

    # 续跑路径专用：提交本 job 时实际使用的请求域名，由 resume_executor 从 `tasks.submitted_base_url` 回放。
    # backend 轮询时优先用它而非当下配置解析出的域名——域名是连接维度而非协议维度，
    # 用户在途改配置后按新域名轮旧 job 会查无（404）而被误判成过期。提交路径恒 None。
    submitted_base_url: str | None = None

    # Seedance 特有
    service_tier: str = "default"
    seed: int | None = None


@dataclass
class VideoGenerationResult:
    """通用视频生成结果。"""

    video_path: Path
    provider: str
    model: str
    duration_seconds: int

    video_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None
    task_id: str | None = None
    generate_audio: bool | None = None


class VideoBackend(Protocol):
    """视频生成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def video_capabilities(self) -> VideoCapabilities: ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续 provider 上已发起的 job：轮询 + 下载，不重新 submit（ADR 0007）。

        未实现的 backend 抛 ``NotImplementedError``；orphan handler 据此走
        ``[resume_unsupported]``。provider 端 job 过期/未找到抛 ``ResumeExpiredError``
        走 ``[resume_expired]``。
        """
        raise NotImplementedError
