"""Anthropic 兼容端点的连通性体检 + 诊断分类。

只探测 Agent 真正调用的 ``POST {base_url}/v1/messages``：测试连接的结论必须与运行时
一致，模型发现是独立的尽力而为路径，不参与体检。

走 httpx 直调（不走 Claude SDK 子进程）：
- SDK 路径冷启动 6s+ / timeout 30s / stderr 不含 HTTP status，诊断精度差
- httpx 直调能拿到精确 status code 和上游错误 body，分类更可靠也更快

日志严格只打 URL 与 status，不打 body / headers / api_key。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

import httpx

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset
from lib.config.url_utils import (
    ANTHROPIC_MESSAGES_PATH,
    anthropic_endpoint_url,
    validate_anthropic_base_url,
)
from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_ERR_TRUNCATE = 200

# 超时与其他网络异常同为 status_code=None；error 以此前缀开头即为「服务可达但
# 响应慢」，classify 据此与「网络不通」分开。只有 ReadTimeout（连接已建立、请求已
# 发出、等响应超时）能证明服务可达；ConnectTimeout / WriteTimeout / PoolTimeout
# 不能，与其他网络异常同路径处理。
_TIMEOUT_ERROR_PREFIX = "timeout: "

# messages 探测上限。带推理的模型（如火山方舟 Agent Plan 的 doubao-seed 系列）
# 首 token 常达 8~10s，上限须留出足够余量，否则正常端点会被误判为网络不通。
_MESSAGES_PROBE_TIMEOUT_S = 30.0

# 400 错误体里「缺参」措辞紧挨着 model 时 = 模型参数没传（而非模型名不被识别）。
# 中间允许任意字符以容纳 "missing parameter model" 这类措辞，但限定邻近距离，
# 避免 body 里另一个参数的缺参提示（如 missing max_tokens）连坐到 model。
_MISSING_MODEL_RE = re.compile(r"(missing|required|empty).{0,16}model|model.{0,16}(missing|required|is empty)")


def anthropic_auth_headers(api_key: str, *, bearer: bool = False) -> dict[str, str]:
    """Anthropic 协议请求头；``bearer=True`` 给只认 Authorization 的网关（火山方舟）用。"""
    auth = {"Authorization": f"Bearer {api_key}"} if bearer else {"x-api-key": api_key}
    return {**auth, "anthropic-version": "2023-06-01"}


class DiagnosisCode(StrEnum):
    OPENAI_COMPAT_ONLY = "openai_compat_only"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_REQUIRED = "model_required"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None  # 截断到 200 字符


async def _post(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
    http_client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    """POST 出站间接层；``http_client`` 缺省时用共享客户端。"""
    client = http_client or get_http_client()
    return await client.post(url, headers=headers, json=payload, timeout=timeout_s)


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    return s if len(s) <= _ERR_TRUNCATE else s[:_ERR_TRUNCATE] + "…"


async def probe_messages(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_s: float = _MESSAGES_PROBE_TIMEOUT_S,
    http_client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    """POST {base_url}/v1/messages 发最小请求 (max_tokens=1)。

    判定:
    - 2xx 且响应 JSON 含 type=message → success
    - 2xx 但响应不像 anthropic JSON → 判失败 (后续 classify 为 OPENAI_COMPAT_ONLY)
    - 非 2xx → 失败 (上游错误 body 截 200 字符放入 error 字段)
    - 网络异常/超时 → 失败 (status_code=None)
    """
    url = anthropic_endpoint_url(base_url, ANTHROPIC_MESSAGES_PATH)
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {**anthropic_auth_headers(api_key), "content-type": "application/json"}
    started = time.perf_counter()
    try:
        resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s, http_client=http_client)
    except httpx.ReadTimeout as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages timeout url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=f"{_TIMEOUT_ERROR_PREFIX}{exc!s}")
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages network err url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=_truncate(str(exc)))

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("probe_messages url=%s status=%d elapsed_ms=%d", url, resp.status_code, elapsed)

    if resp.status_code >= 400:
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error=_truncate(resp.text),
        )

    # 2xx：检查是否真的是 anthropic JSON（识别 OpenAI 兼容代理冒充）
    try:
        data = resp.json()
    except ValueError:
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error="non-anthropic response: not JSON",
        )
    if not isinstance(data, dict) or data.get("type") != "message":
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error="non-anthropic JSON: missing type=message",
        )
    return ProbeResult(success=True, status_code=resp.status_code, latency_ms=elapsed, error=None)


def classify_probe_failure(result: ProbeResult) -> DiagnosisCode:
    """把失败 ProbeResult 映射到 DiagnosisCode。"""
    if result.success:
        return DiagnosisCode.UNKNOWN  # caller misuse
    err = (result.error or "").lower()
    code = result.status_code
    if code in (401, 403):
        return DiagnosisCode.AUTH_FAILED
    if code == 429:
        return DiagnosisCode.RATE_LIMITED
    # 启发式：404 body 含 "model" 关键词即视为模型不存在；后端改措辞时会退化到 UNKNOWN
    if code == 404 and ("model" in err or "model_not_found" in err):
        return DiagnosisCode.MODEL_NOT_FOUND
    # 400 + body 提到 model：网关在参数校验阶段就拒了。缺参（火山方舟的
    # MissingParameter）与模型名不被识别是两种用户动作，按 body 关键词分开。
    if code == 400 and "model" in err:
        if _MISSING_MODEL_RE.search(err):
            return DiagnosisCode.MODEL_REQUIRED
        return DiagnosisCode.MODEL_NOT_FOUND
    if code is not None and 200 <= code < 300:
        # 2xx 但 probe 判失败 = 协议不匹配（OpenAI 兼容响应冒充 anthropic）
        return DiagnosisCode.OPENAI_COMPAT_ONLY
    if code is None:
        if (result.error or "").startswith(_TIMEOUT_ERROR_PREFIX):
            return DiagnosisCode.TIMEOUT
        return DiagnosisCode.NETWORK
    return DiagnosisCode.UNKNOWN


_DEFAULT_TEST_MODEL = "claude-3-5-sonnet-20241022"


@dataclass(frozen=True)
class SuggestionAction:
    kind: Literal["check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None = None


@dataclass(frozen=True)
class TestConnectionResponse:
    overall: Literal["ok", "fail"]
    messages_probe: ProbeResult
    diagnosis: DiagnosisCode | None
    suggestion: SuggestionAction | None
    messages_url: str
    """本次实际请求的完整地址；预览与请求同一个字符串。"""


async def run_test(
    *,
    preset_id: str | None,
    base_url: str | None,
    api_key: str,
    model: str | None,
    http_client: httpx.AsyncClient | None = None,
) -> TestConnectionResponse:
    """校验 base_url → 探测 Agent 真正调用的 messages 端点 → 诊断。

    Raises:
        ValueError: 未知 preset、自定义模式缺 base_url，或 base_url 不合法
            （``InvalidAnthropicBaseUrlError`` 是其子类）。
    """
    if preset_id and preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(preset_id)
        if preset is None:
            raise ValueError(f"unknown preset: {preset_id!r}")
        # 覆盖了 preset.messages_url（如内部代理）时与自定义模式同路径；未覆盖时用目录值
        effective_base = validate_anthropic_base_url(base_url) if base_url else preset.messages_url
        effective_model = model or preset.default_model
    else:
        if not base_url:
            raise ValueError("base_url required for __custom__ mode")
        effective_base = validate_anthropic_base_url(base_url)
        effective_model = model or _DEFAULT_TEST_MODEL

    messages_url = anthropic_endpoint_url(effective_base, ANTHROPIC_MESSAGES_PATH)

    # 预设模型为空：messages 请求必然被上游以「缺 model」拒掉，直接短路，
    # 让用户看到「请先填默认模型」而不是一条泛化的上游报错。
    if not effective_model:
        return TestConnectionResponse(
            overall="fail",
            messages_probe=ProbeResult(success=False, status_code=None, latency_ms=None, error=None),
            diagnosis=DiagnosisCode.MODEL_REQUIRED,
            suggestion=SuggestionAction(kind="run_discovery"),
            messages_url=messages_url,
        )

    msg = await probe_messages(
        base_url=effective_base,
        api_key=api_key,
        model=effective_model,
        http_client=http_client,
    )
    return TestConnectionResponse(
        overall="ok" if msg.success else "fail",
        messages_probe=msg,
        diagnosis=None if msg.success else classify_probe_failure(msg),
        suggestion=None,
        messages_url=messages_url,
    )
