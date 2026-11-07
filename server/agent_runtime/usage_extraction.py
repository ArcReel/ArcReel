"""Agent-session metering: per-request usage plus per-turn ``model_usage`` differences for calls invisible in streams (docs/adr/0102).

- Stream requests use ``AssistantMessage.usage``, deduplicated by ``message_id`` with per-field maxima across frames
  for the same request. Include subagent requests with ``parent_tool_use_id``. Interpret all usage using Anthropic
  semantics: ``input_tokens`` excludes cache; ``cache_creation`` splits 5-minute/1-hour writes, or all 5-minute
  when only a combined total exists.
- Off-stream calls: at turn end, subtract per-model stream totals from this turn’s ``model_usage`` increase over
  baseline, combining positive per-field differences into one request.
- Maintain session baselines: new sessions start at zero; resumed sessions establish baseline from the first result
  and count no difference for that turn; reset sessions clear it. Decreased per-model cumulative values make
  current values that model’s single-turn usage.

Treat ``model_usage`` keys and ``AssistantMessage.model`` as the same model by catalog entry (docs/adr/0105) or normalized
name. Record stream request names, or ``model_usage`` keys when only differences exist."""

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
    """Per-request token usage with disjoint fields: total input = uncached input + cache reads + both cache-write tiers."""

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
        """Merge two frames for one request by per-field maxima, replacing placeholders with later values."""
        return RequestTokens(**{f.name: max(getattr(self, f.name), getattr(other, f.name)) for f in fields(self)})

    def surplus_over(self, other: RequestTokens) -> RequestTokens:
        """Per-field excess over ``other``, clamping negative differences to 0."""
        return RequestTokens(**{f.name: max(getattr(self, f.name) - getattr(other, f.name), 0) for f in fields(self)})

    def covers(self, other: RequestTokens) -> bool:
        """Whether every field is at least ``other``, indicating no cumulative decrease."""
        return all(getattr(self, f.name) >= getattr(other, f.name) for f in fields(self))


_ZERO = RequestTokens()


@dataclass(frozen=True)
class ModelTurnUsage:
    """One model’s usage in a turn: ``requests`` contains individual calls plus one combined off-stream difference request."""

    model: str
    requests: tuple[RequestTokens, ...]

    @property
    def total(self) -> RequestTokens:
        total = _ZERO
        for request in self.requests:
            total += request
        return total


def _count(source: Mapping[str, Any], key: str) -> int:
    """One usage token count: 0 for absent/nonnumeric values; clamp negatives to 0 and log."""
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return 0
    if value < 0:
        logger.warning("Agent usage 的 %s 为负（%s），按 0 计", key, value)
        return 0
    return int(value)


def request_tokens(usage: Mapping[str, Any]) -> RequestTokens:
    """Interpret ``AssistantMessage.usage`` using Anthropic semantics."""
    cache_write = _count(usage, "cache_creation_input_tokens")
    split = usage.get("cache_creation")
    write_5m, write_1h = cache_write, 0
    if isinstance(split, Mapping):
        write_1h = _count(split, "ephemeral_1h_input_tokens")
        # Count totals exceeding available splits, such as gateway totals only, at the 5-minute tier.
        write_5m = max(_count(split, "ephemeral_5m_input_tokens"), cache_write - write_1h)
    return RequestTokens(
        uncached_input=_count(usage, "input_tokens"),
        cache_read=_count(usage, "cache_read_input_tokens"),
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
        output=_count(usage, "output_tokens"),
    )


def _model_usage_tokens(entry: Mapping[str, Any]) -> RequestTokens:
    """One cumulative ``model_usage`` value; cache writes lack tier splits, so count totals at the 5-minute tier."""
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
    """Metering for one session connection. ``resumed`` indicates resumed cold start, whose CLI cumulative values include prior spend."""

    def __init__(self, *, resumed: bool) -> None:
        self._baseline: dict[str, RequestTokens] | None = None if resumed else {}
        # Stream requests this turn: message_id → (model name, usage), in first-occurrence order.
        self._pending: dict[str, tuple[str, RequestTokens]] = {}
        # Already billed requests: later frames do not count again; differences account for usage increases.
        self._settled: set[str] = set()
        self._model_keys: dict[str, object] = {}

    def observe(self, msg: Mapping[str, Any]) -> None:
        """Read a message: record assistant request usage; session resets clear difference baselines."""
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
        """End a turn by returning request usage for every model with usage and advancing baseline to current cumulative values.

        Use the result’s ``model_usage`` field; absent fields produce no difference and leave baselines unchanged."""
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
        # model_usage combines both write tiers, billed as 5-minute writes; compare against the sum of stream tiers.
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
        """Model identity: use a matched catalog entry, otherwise the normalized name."""
        key = self._model_keys.get(model)
        if key is None:
            entry = catalog.find_entry(model, media_type="text")
            key = entry.key if entry is not None else normalize_model_name(model)
            self._model_keys[model] = key
        return key
