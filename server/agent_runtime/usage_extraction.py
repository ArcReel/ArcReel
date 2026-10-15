"""Agent 会话的用量计量：按请求取用量，按轮差分 ``model_usage`` 补齐流中看不到的调用（docs/adr/0102）。

- 流内请求：用量取自 ``AssistantMessage.usage``，按 ``message_id`` 去重（部分消息流下同一请求会出现多帧，
  同一项取各帧的最大值），子智能体的请求（带 ``parent_tool_use_id``）一并计入。usage 一律按 Anthropic 口径解读：
  ``input_tokens`` 不含缓存；缓存写取 ``cache_creation`` 的 5 分钟与 1 小时拆分，只有合计时全部按 5 分钟档计。
- 流外调用：每轮结束时，用本轮 ``model_usage`` 相对差分基线的增量，按模型减去流内各请求的合计；差额为正的
  各项合成一次请求。
- 差分基线按会话维护：新会话从零开始；恢复会话时以恢复后第一条结果为起点，这一轮不计差额；会话重置时清零；
  某个模型的累计值回落时，把本轮值当作这个模型的单轮值。

``model_usage`` 的键与 ``AssistantMessage.model`` 按同一模型条目（docs/adr/0105）或归一化后的名字视为同一模型，
记录取流内请求的模型名，只有差额时取 ``model_usage`` 的键。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

from arcreel_catalog_core import normalize_model_name
from lib.catalog import query as catalog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestTokens:
    """一次 API 请求的 token 用量，各项互不重叠：总输入 = 未缓存输入 + 缓存读 + 两档缓存写。"""

    uncached_input: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    output: int = 0

    @property
    def total_input(self) -> int:
        return self.uncached_input + self.cache_read + self.cache_write_5m + self.cache_write_1h

    @property
    def is_empty(self) -> bool:
        return self == _ZERO

    def __add__(self, other: RequestTokens) -> RequestTokens:
        return RequestTokens(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})

    def merge_max(self, other: RequestTokens) -> RequestTokens:
        """同一请求的两帧：各项取较大值（占位值会被之后的帧更新）。"""
        return RequestTokens(**{f.name: max(getattr(self, f.name), getattr(other, f.name)) for f in fields(self)})

    def surplus_over(self, other: RequestTokens) -> RequestTokens:
        """各项超出 ``other`` 的部分，不足的项记 0。"""
        return RequestTokens(**{f.name: max(getattr(self, f.name) - getattr(other, f.name), 0) for f in fields(self)})

    def covers(self, other: RequestTokens) -> bool:
        """各项都不小于 ``other``：累计值没有回落。"""
        return all(getattr(self, f.name) >= getattr(other, f.name) for f in fields(self))


_ZERO = RequestTokens()


@dataclass(frozen=True)
class ModelTurnUsage:
    """一轮里一个模型的用量：``requests`` 是各次请求，流外调用的差额合成其中一次。"""

    model: str
    requests: tuple[RequestTokens, ...]

    @property
    def total(self) -> RequestTokens:
        total = _ZERO
        for request in self.requests:
            total += request
        return total


def _count(source: Mapping[str, Any], key: str) -> int:
    """usage 里的一项 token 数；缺失或不是数时为 0，为负时按 0 计并打日志。"""
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return 0
    if value < 0:
        logger.warning("Agent usage 的 %s 为负（%s），按 0 计", key, value)
        return 0
    return int(value)


def request_tokens(usage: Mapping[str, Any]) -> RequestTokens:
    """按 Anthropic 口径解读一条 ``AssistantMessage.usage``。"""
    cache_write = _count(usage, "cache_creation_input_tokens")
    split = usage.get("cache_creation")
    write_5m, write_1h = cache_write, 0
    if isinstance(split, Mapping):
        write_1h = _count(split, "ephemeral_1h_input_tokens")
        # 拆分不足合计的部分（网关只给出合计）按 5 分钟档计。
        write_5m = max(_count(split, "ephemeral_5m_input_tokens"), cache_write - write_1h)
    return RequestTokens(
        uncached_input=_count(usage, "input_tokens"),
        cache_read=_count(usage, "cache_read_input_tokens"),
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
        output=_count(usage, "output_tokens"),
    )


def _model_usage_tokens(entry: Mapping[str, Any]) -> RequestTokens:
    """``model_usage`` 的一项累计值；它没有缓存写的两档拆分，合计按 5 分钟档计。"""
    return RequestTokens(
        uncached_input=_count(entry, "inputTokens"),
        cache_read=_count(entry, "cacheReadInputTokens"),
        cache_write_5m=_count(entry, "cacheCreationInputTokens"),
        output=_count(entry, "outputTokens"),
    )


def _parse_model_usage(raw: object) -> dict[str, RequestTokens] | None:
    if not isinstance(raw, Mapping):
        return None
    return {str(name): _model_usage_tokens(entry) for name, entry in raw.items() if name and isinstance(entry, Mapping)}


class TurnUsageMeter:
    """一个会话连接的用量计量。``resumed`` 为真表示恢复会话的冷启动：CLI 的累计值带着此前的花费。"""

    def __init__(self, *, resumed: bool) -> None:
        self._baseline: dict[str, RequestTokens] | None = None if resumed else {}
        # 本轮流内请求：message_id → (模型名, 用量)，按首次出现的顺序。
        self._pending: dict[str, tuple[str, RequestTokens]] = {}
        # 已经记账的请求：之后再出现的帧不重复计入，用量的增长由差额补齐。
        self._settled: set[str] = set()
        self._model_keys: dict[str, object] = {}

    def observe(self, msg: Mapping[str, Any]) -> None:
        """读入一条消息：assistant 消息记下它所属请求的用量，会话重置时差分基线清零。"""
        msg_type = msg.get("type")
        if msg_type == "conversation_reset":
            self._baseline = {}
            return
        if msg_type != "assistant":
            return
        usage = msg.get("usage")
        message_id = msg.get("message_id")
        model = msg.get("model")
        if not isinstance(usage, Mapping) or not isinstance(message_id, str) or not message_id:
            return
        if not isinstance(model, str) or not model or message_id in self._settled:
            return
        tokens = request_tokens(usage)
        previous = self._pending.get(message_id)
        self._pending[message_id] = (model, tokens if previous is None else previous[1].merge_max(tokens))

    def close_turn(self, model_usage: object) -> list[ModelTurnUsage]:
        """一轮结束：返回本轮每个有用量的模型各次请求的用量，并把差分基线推进到本轮的累计值。

        ``model_usage`` 取本轮 result 的同名字段；没有这个字段时不计差额，基线保持不变。
        """
        groups: dict[object, tuple[str, list[RequestTokens]]] = {}
        for model, tokens in self._pending.values():
            groups.setdefault(self._model_key(model), (model, []))[1].append(tokens)
        self._settled.update(self._pending)
        self._pending.clear()

        current = _parse_model_usage(model_usage)
        if current is not None:
            if self._baseline is not None:
                for name, total in current.items():
                    self._add_gap(groups, name, self._turn_delta(name, total, self._baseline.get(name, _ZERO)))
            self._baseline = current

        return [
            ModelTurnUsage(model=model, requests=tuple(requests))
            for model, requests in groups.values()
            if any(not request.is_empty for request in requests)
        ]

    def _add_gap(self, groups: dict[object, tuple[str, list[RequestTokens]]], name: str, delta: RequestTokens) -> None:
        key = self._model_key(name)
        model, requests = groups.get(key, (name, []))
        streamed = _ZERO
        for request in requests:
            streamed += request
        # model_usage 的缓存写是两档合计（按 5 分钟档记），与流内两档之和比较。
        streamed = replace(streamed, cache_write_5m=streamed.cache_write_5m + streamed.cache_write_1h, cache_write_1h=0)
        gap = delta.surplus_over(streamed)
        if gap.is_empty:
            return
        groups[key] = (model, [*requests, gap])

    @staticmethod
    def _turn_delta(name: str, total: RequestTokens, baseline: RequestTokens) -> RequestTokens:
        if total.covers(baseline):
            return total.surplus_over(baseline)
        logger.info("Agent model_usage 的累计值回落，本轮值按单轮值计 model=%s", name)
        return total

    def _model_key(self, model: str) -> object:
        """同一模型的标识：对应得到条目时取条目，否则取归一化后的名字。"""
        key = self._model_keys.get(model)
        if key is None:
            entry = catalog.find_entry(model, media_type="text")
            key = entry.key if entry is not None else normalize_model_name(model)
            self._model_keys[model] = key
        return key
