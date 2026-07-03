"""文本生成服务层核心接口定义。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

_logger = logging.getLogger(__name__)

# Chat Completions 输出上限参数名：官方 OpenAI 端点已弃用 max_tokens（推理模型
# 直接拒绝），改用 max_completion_tokens；第三方兼容端点对新参数支持情况不一，
# 须保守沿用 max_tokens。由调用方按端点选择。
TokenParam = Literal["max_tokens", "max_completion_tokens"]


def warn_if_truncated(
    finish_reason: str | None,
    *,
    provider: str,
    model: str,
    output_tokens: int | None = None,
    truncation_values: tuple[str, ...] = ("length", "MAX_TOKENS", "max_tokens"),
) -> bool:
    """检测模型响应是否因 token 上限被截断，若是则 logger.warning。

    返回 True 表示被截断（供调用方用于进一步处理）。
    """
    if finish_reason is None:
        return False
    if finish_reason in truncation_values:
        _logger.warning(
            "%s/%s 输出被截断（finish_reason=%s, output_tokens=%s）：已达模型输出上限。"
            "考虑切换到更大输出上限的模型，或减少请求规模。",
            provider,
            model,
            finish_reason,
            output_tokens,
        )
        return True
    return False


class TextCapability(StrEnum):
    """文本后端支持的能力枚举。"""

    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"


class TextTaskType(StrEnum):
    """文本生成任务类型。"""

    SCRIPT = "script"
    OVERVIEW = "overview"
    STYLE_ANALYSIS = "style"


@dataclass
class ImageInput:
    """图片输入（用于 vision）。"""

    path: Path | None = None
    url: str | None = None


@dataclass
class TextGenerationRequest:
    """通用文本生成请求。各 Backend 忽略不支持的字段。"""

    prompt: str
    response_schema: dict | type | None = None
    images: list[ImageInput] | None = None
    system_prompt: str | None = None
    max_output_tokens: int | None = None


@dataclass
class TextGenerationResult:
    """通用文本生成结果。"""

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def resolve_schema(schema: dict | type[BaseModel]) -> dict:
    """将 response_schema 转为无 $ref 的纯 JSON Schema dict。

    - BaseModel 子类: 调用 model_json_schema() 后内联 $ref
    - dict: 直接内联 $ref（如果有）
    """
    if isinstance(schema, type):
        if not issubclass(schema, BaseModel):
            raise TypeError(f"resolve_schema 仅接受 dict 或 Pydantic BaseModel 子类，得到 {schema!r}")
        schema_dict: dict = schema.model_json_schema()
    else:
        schema_dict = schema

    defs = schema_dict.get("$defs", {})
    if not defs:
        return schema_dict

    def _inline(obj: Any, visited_refs: frozenset[str] = frozenset()) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                if ref_name in visited_refs:
                    raise ValueError(f"检测到 schema 中的循环引用: {ref_name}")
                resolved = _inline(defs[ref_name], visited_refs | {ref_name})
                extra = {k: v for k, v in obj.items() if k != "$ref"}
                return {**resolved, **extra} if extra else resolved
            return {k: _inline(v, visited_refs) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_inline(item, visited_refs) for item in obj]
        return obj

    result: dict = _inline(schema_dict)
    result.pop("$defs", None)
    return result


def is_valid_json(text: str) -> bool:
    """判断字符串是否为合法 JSON。

    一些原生结构化通道（OpenAI 兼容代理、自定义供应商常见情况）会静默忽略结构化输出
    参数并返回纯文本/markdown，需要据此触发降级。
    """
    if not text or not text.strip():
        return False
    try:
        json.loads(text)
        return True
    except (ValueError, TypeError):
        return False


def summarize_validation_error(exc: ValidationError) -> str:
    """把 ValidationError 压成简短的字段定位摘要。

    只取字段路径（loc）与错误数，**不含模型原始输入值**——后者可能很大且会把
    模型生成内容写进日志（经诊断日志打包外泄），也避免单条日志膨胀到数 KB。
    用 include_input=False 在源头剔除 input，不让原始值进入 error dict（防御纵深）。
    """
    locs = [
        ".".join(str(part) for part in err.get("loc", ())) or "<root>" for err in exc.errors(include_input=False)[:5]
    ]
    suffix = "…" if exc.error_count() > 5 else ""
    return f"{exc.error_count()} 处字段不符（{', '.join(locs)}{suffix}）"


def structured_fallback_reason(text: str, response_schema: dict | type | None, *, strict: bool = True) -> str | None:
    """判断原生结构化调用 HTTP 200 的返回是否需要降级到带校验的路径。

    与 provider / client 类型 / 原生 API 形态无关的纯函数，全文本后端共享。返回降级原因
    （用于日志）；None 表示原生输出可直接采用。两类触发场景：

    1. 返回非 JSON：供应商静默忽略结构化输出参数，吐出纯文本/markdown。
    2. 返回违反 schema 的合法 JSON：供应商接受结构化输出参数却不真正强制 schema（国内中转 /
       非原厂模型常见），枚举值非法或缺必填字段。此类违例 JSON 若直接放行，会一路漏到下游
       Pydantic 校验或渲染处才抛裸 ValidationError。

    ``strict`` 由调用方按各后端原生请求是否声明 strict 传入对齐：声明了 strict 的后端用
    strict=True（可强转但类型不严格匹配的值，如 int 字段给 "30"，也判为未强制 schema）；
    未声明 strict 的后端用 strict=False（容忍可强转值，只对真正无法满足 schema 的输出降级），
    避免对供应商已接受的合法响应触发多余的计费降级调用。

    仅 Pydantic 模型可做 schema 校验；dict schema 无对应模型，沿用「仅校验是否合法 JSON」的
    既有行为，不额外收紧。response_schema 为 None（无结构化输出诉求）直接视为无需降级。
    """
    if response_schema is None:
        # 纯文本生成无结构化诉求，原生输出直接采用，不能按「非 JSON」误判触发降级。
        return None
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        # model_validate_json 单次解析即同时覆盖「非 JSON」与「违反 schema」两种情况。
        try:
            response_schema.model_validate_json(text, strict=strict)
        except ValidationError as exc:
            return f"返回内容不满足 response_schema（供应商可能未强制 schema）：{summarize_validation_error(exc)}"
        return None
    if not is_valid_json(text):
        return "返回非 JSON 内容（供应商可能未支持结构化输出）"
    return None


class TextBackend(Protocol):
    """文本生成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> set[TextCapability]: ...

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult: ...
