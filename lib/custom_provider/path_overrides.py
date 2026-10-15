"""路径覆盖（``docs/adr/0101``）：调用端点的请求声明、覆盖路径的校验与生效值。

每个调用端点在 :attr:`EndpointSpec.requests <lib.custom_provider.endpoints.EndpointSpec.requests>`
里声明自己发出的可覆盖请求；供应商模型的 ``path_overrides`` 按请求名记录整条替换的路径，
路径相对供应商填写的 base_url 原样值，不再经过端点的 base_url 归一化。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit

import httpx

#: 请求名 → 整条替换的路径。
PathOverrides = Mapping[str, str]
NO_PATH_OVERRIDES: PathOverrides = MappingProxyType({})

#: 没有覆盖时，端点在默认路径之前怎样归一化 base_url；界面据此还原最终请求地址。
#: ``as_is`` 只去尾斜杠；``strip_version`` 剥去末尾的版本段（如 ``/v1``）；``append_v1``
#: 末尾没有版本段时补 ``/v1``；``ensure_<后缀>`` 末尾不是该后缀时补上（方舟 ``/api/v3``、Vidu ``/ent/v2``、
#: 可灵与 MiniMax ``/v1``）；``dashscope_api_v1`` 剥去百炼已知后缀后接 ``/api/v1``；``strip_messages`` 剥去整条
#: 复制进来的 ``/v1/messages`` 后缀（``normalize_anthropic_base_url``）。
#: 有覆盖的请求一律相对原样值，不按本规则归一化。
BaseUrlRule = Literal[
    "as_is",
    "strip_version",
    "append_v1",
    "ensure_api_v3",
    "ensure_ent_v2",
    "ensure_v1",
    "dashscope_api_v1",
    "strip_messages",
]

#: 路径中的占位符写法，与声明式定义的模板同形：``{{ model }}``、``{{ task_id }}``。
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")
_MAX_PATH_LENGTH = 512

PathProblem = Literal["path_override_invalid_path", "path_override_placeholder_not_allowed"]


@dataclass(frozen=True)
class EndpointRequest:
    """调用端点发出的一条可覆盖路径的请求。"""

    #: 请求名，路径覆盖按它记录，例如声明式定义的 ``submit`` / ``poll`` / ``result``。
    name: str
    #: 前端 i18n key（dashboard ns），端点有多条请求时标在各自的地址条上方。
    display_name_key: str
    #: 归一化后的 base_url 之后的默认路径，可含占位符。
    default_path: str
    #: 覆盖路径里允许出现的占位符：只限该请求本身已有的值（API 模型名、任务 id 等）。
    placeholders: tuple[str, ...]
    base_url_rule: BaseUrlRule = "as_is"


def placeholder_names(path: str) -> set[str]:
    return set(_PLACEHOLDER.findall(path))


def path_problem(request: EndpointRequest, path: str) -> PathProblem | None:
    """覆盖路径不合法的原因；合法时 None。

    路径必须以 ``/`` 开头，占位符之外不含空白、花括号与 ``#``（片段不随请求发出）；占位符只能取请求声明的那几个。
    """
    literal = _PLACEHOLDER.sub("", path)
    if not path.startswith("/") or len(path) > _MAX_PATH_LENGTH or any(c.isspace() or c in "{}#" for c in literal):
        return "path_override_invalid_path"
    if not placeholder_names(path) <= set(request.placeholders):
        return "path_override_placeholder_not_allowed"
    return None


def effective_path_overrides(stored: object, requests: Iterable[EndpointRequest]) -> dict[str, str]:
    """库里存的覆盖中仍然生效的部分：端点定义里已不存在的请求名忽略。"""
    if not isinstance(stored, Mapping):
        return {}
    names = {r.name for r in requests}
    return {k: v for k, v in stored.items() if k in names and isinstance(v, str)}


def raw_base_url(base_url: str) -> str:
    """覆盖路径所相对的 base_url：供应商填写的原样值，只补缺失的协议、去尾斜杠。"""
    stripped = base_url.strip().rstrip("/")
    return f"https://{stripped}" if stripped and "://" not in stripped else stripped


def endpoint_guard(endpoint: str, overrides: PathOverrides) -> str:
    """续跑比对的协议标识：调用端点键，有覆盖时附上覆盖的规范 JSON。

    没有覆盖时与端点键逐字相等，存量执行检查点照旧比对。
    """
    if not overrides:
        return endpoint
    return f"{endpoint} paths={json.dumps(dict(overrides), sort_keys=True, ensure_ascii=False, separators=(',', ':'))}"


def _path_pattern(prefix: str, default_path: str, placeholder: str) -> re.Pattern[str]:
    """``prefix + default_path`` 的整串匹配式，占位符按 ``placeholder`` 匹配。"""
    parts = _PLACEHOLDER.split(default_path)  # 字面量与占位符名交替
    body = "".join(re.escape(part) if i % 2 == 0 else f"(?P<{part}>{placeholder})" for i, part in enumerate(parts))
    return re.compile(re.escape(prefix) + body)


class PathOverrideLayer:
    """Python 端点的路径覆盖：按请求路径认出请求名，有覆盖时把路径换成「原样 base_url 的路径 + 覆盖路径」。

    只认「归一化 base_url 的路径 + 默认路径」整串相符的请求，取自响应的下载地址不会被认成任何请求。
    不改域名：续跑回放提交时的域名照旧生效。
    """

    def __init__(self, routes: list[tuple[re.Pattern[str], str]], raw_path: str, model: str) -> None:
        self._routes = routes
        self._raw_path = raw_path
        self._model = model

    def __call__(self, inner: httpx.AsyncBaseTransport | httpx.BaseTransport, /) -> _PathOverrideTransport:
        return _PathOverrideTransport(inner, self)

    def rewrite(self, request: httpx.Request) -> None:
        # 按未解码的路径匹配：前缀取自 base_url 原文，%2F 这类转义解码后就对不上；捕获的占位符值也原样写回
        raw_path = request.url.raw_path.decode("ascii").partition("?")[0]
        found = next(((m, override) for pattern, override in self._routes if (m := pattern.fullmatch(raw_path))), None)
        if found is None:
            return
        match, override = found
        values = {"model": self._model, **match.groupdict()}
        target = self._raw_path + _PLACEHOLDER.sub(lambda p: values.get(p.group(1), p.group(0)), override)
        path, has_query, query = target.partition("?")
        request.url = (
            request.url.copy_with(path=path, query=query.encode()) if has_query else request.url.copy_with(path=path)
        )


class _PathOverrideTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | httpx.BaseTransport, layer: PathOverrideLayer) -> None:
        self._inner = inner
        self._layer = layer

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(self._inner, httpx.BaseTransport)
        self._layer.rewrite(request)
        return self._inner.handle_request(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(self._inner, httpx.AsyncBaseTransport)
        self._layer.rewrite(request)
        return await self._inner.handle_async_request(request)

    def close(self) -> None:
        assert isinstance(self._inner, httpx.BaseTransport)
        self._inner.close()

    async def aclose(self) -> None:
        assert isinstance(self._inner, httpx.AsyncBaseTransport)
        await self._inner.aclose()


def path_override_layer(
    requests: Iterable[EndpointRequest],
    overrides: PathOverrides,
    base_url: str | None,
    normalized_base_url: str | None,
    model: str,
    placeholder: str = "[^/]+",
) -> PathOverrideLayer | None:
    """Python 端点注入调用通道的路径覆盖层；没有覆盖时 None，调用通道照原样构造。

    ``normalized_base_url`` 是端点没有覆盖时实际使用的 base_url，默认路径相对它匹配。``placeholder`` 是占位符的
    匹配式，默认只匹配一段路径；值本身含 ``/`` 的家族（Google 的 operation name）按自己的路径形态另给。
    """
    prefix = urlsplit(normalized_base_url or "").path.rstrip("/")
    routes = [
        (_path_pattern(prefix, r.default_path, placeholder), overrides[r.name]) for r in requests if r.name in overrides
    ]
    if not routes:
        return None
    return PathOverrideLayer(routes, urlsplit(raw_base_url(base_url or "")).path, model)
