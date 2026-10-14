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

#: 请求名 → 整条替换的路径。
PathOverrides = Mapping[str, str]
NO_PATH_OVERRIDES: PathOverrides = MappingProxyType({})

#: 没有覆盖时，端点在默认路径之前怎样归一化 base_url；界面据此还原最终请求地址。
#: ``as_is`` 只去尾斜杠；``strip_version`` 剥去末尾的版本段（如 ``/v1``）；``append_v1``
#: 末尾没有版本段时补 ``/v1``。有覆盖的请求一律相对原样值，不按本规则归一化。
BaseUrlRule = Literal["as_is", "strip_version", "append_v1"]

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

    路径必须以 ``/`` 开头，占位符之外不含空白与花括号；占位符只能取请求声明的那几个。
    """
    literal = _PLACEHOLDER.sub("", path)
    if not path.startswith("/") or len(path) > _MAX_PATH_LENGTH or any(c.isspace() or c in "{}" for c in literal):
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
