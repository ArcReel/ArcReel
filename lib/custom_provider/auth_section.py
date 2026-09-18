"""``auth`` 节的模板原语与两种 ``kind`` 共用的语义检查。

声明式定义与 ComfyUI 定义的 ``auth`` 节是同一份 schema、同一套渲染语义：``headers`` 与 ``query``
两张表，值是模板，凭证只以 ``api_key`` 占位符的形态出现。两种 kind 的差别只有一条——除 ``api_key``
外还认哪些变量（声明式的 auth 节里 ``base_url`` 之类的基础变量同样可用，ComfyUI 的只认 ``api_key``）
——故变量作用域由调用方以 ``variable_issues`` 给出，其余三条检查（占位符写坏、字面凭证、头名重复）
与 kind 无关，落在这里由两侧共读。

本模块在 ``lib.custom_provider`` 顶层而不在 ``endpoint_definition`` 内：comfyui 子包受「不依赖
声明式运行时」的 import 契约约束，够不到那个包里的任何实现；同 ``definition_diagnostics`` 与
``definition_schema_errors``，这是两种 kind 都向下依赖的叶子。

渲染同住这里的理由一样：ComfyUI 客户端要把同一份 ``auth`` 节渲染成真实请求头与查询参数，而声明式
的模板引擎在它够不到的那一侧。凭证的写法与它的校验因此不会各持一份对「什么是合法占位符」的理解。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from lib.custom_provider.definition_diagnostics import DefinitionErrorCode, DefinitionIssue, join_path

#: ``auth`` 里唯一承载凭证的变量。
API_KEY_VARIABLE = "api_key"

#: 合法占位符：``{{ 变量名 }}``，变量名允许点号分段（``inputs.first_frame``）。
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")

#: 模板里每一处 ``{{``。不落在 :data:`PLACEHOLDER` 起点上的即写法不合法：格式只认裸变量，
#: 过滤器、下标、表达式与未闭合的开括号都不是占位符，渲染时会原样发给供应商。
_PLACEHOLDER_OPEN = re.compile(r"\{\{")

#: 凭证长相：20+ 位含数字的 token 串（API key / JWT / base64 的公共形态）。版本号、固定
#: 字段这类短静态值不命中；误报的代价只是一条不拦保存的 warning。
_CREDENTIAL_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{20,}")

#: ``auth`` 节里的两张表。
_AUTH_GROUPS = ("headers", "query")


def placeholder_names(text: str) -> list[str]:
    """模板里全部合法占位符的变量名，按出现次序。"""
    return PLACEHOLDER.findall(text)


def malformed_placeholders(text: str) -> list[str]:
    """所有不构成合法占位符的 ``{{`` 片段，取到最近的 ``}}``（没有就到串尾）。"""
    valid_starts = {match.start() for match in PLACEHOLDER.finditer(text)}
    fragments: list[str] = []
    for match in _PLACEHOLDER_OPEN.finditer(text):
        if match.start() in valid_starts:
            continue
        closing = text.find("}}", match.start())
        fragments.append(text[match.start() : closing + 2] if closing != -1 else text[match.start() :])
    return fragments


def looks_like_literal_credential(template: str) -> bool:
    """auth 值剔除占位符后，剩余字面部分是否形似直接写入的凭证。

    凭证以 api_key 占位符形态出现是导出剥凭证的前提；字面凭证会随导出与「复制为我的」
    原样外流，只能靠形态识别提示。
    """
    literal = PLACEHOLDER.sub(" ", template)
    return any(any(ch.isdigit() for ch in token) for token in _CREDENTIAL_TOKEN.findall(literal))


def auth_templates(auth: Mapping[str, Any]) -> Iterator[tuple[str, str, str, str]]:
    """``auth`` 节里每个模板值：``(所在表, 键名, 定位路径, 模板)``。

    键名与定位路径一并给出：头名里允许出现点号，从路径末段反推键名会把 ``X-Trace.Id`` 截成
    ``Id``。
    """
    for group in _AUTH_GROUPS:
        values: Mapping[str, Any] = auth.get(group) or {}
        for name, template in values.items():
            yield group, str(name), join_path(join_path("auth", group), str(name)), str(template)


def declares_credentials(auth: Mapping[str, Any]) -> bool:
    """这一节到底会不会发出凭据。

    按两张表里有没有条目算，不按 ``auth`` 这个对象本身算：``{"headers": {}}`` 一个头都不会发
    出去，与整节缺席等效——:func:`check_auth_section` 与 :func:`render_auth` 都是这个口径，
    「这份定义要不要 api_key」自然也得是。
    """
    return any(True for _ in auth_templates(auth))


def duplicate_header_issues(path: str, headers: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """同一张头表里大小写不同的同名键：HTTP 头名不区分大小写，两条会一起发出去。"""
    seen: dict[str, str] = {}
    for name in headers:
        first = seen.setdefault(name.lower(), name)
        if first != name:
            yield DefinitionIssue(
                join_path(path, name),
                DefinitionErrorCode.HEADER_NAME_DUPLICATE,
                {"header": name, "first": first},
            )


@dataclass(frozen=True)
class AuthIssues:
    """一次 ``auth`` 节检查的产出，按严重度分两列——字面凭证只提示，不拦保存。"""

    errors: tuple[DefinitionIssue, ...] = ()
    warnings: tuple[DefinitionIssue, ...] = ()


def check_auth_section(
    auth: Mapping[str, Any],
    *,
    variable_issues: Callable[[str, str], Iterable[DefinitionIssue]],
) -> AuthIssues:
    """跑 ``auth`` 节的全部语义规则。

    ``variable_issues(path, name)`` 是调用方那种 kind 的变量作用域判定，只在 ``api_key`` 以外的
    变量上调用：``api_key`` 在 auth 节里恒合法，且它是否出现过决定最后那条「非空 auth 必须引用
    凭证」——把这一条留在各 kind 里，两侧就会各自漂移出一份「凭证算不算写到了」的理解。

    「非空」按两张表里有没有条目算，不按 ``auth`` 这个对象本身算：``{"headers": {}}`` 一个头都
    不会发出去，与整节缺席等效。
    """
    errors: list[DefinitionIssue] = []
    warnings: list[DefinitionIssue] = []
    references_api_key = False
    for _group, _name, path, template in auth_templates(auth):
        errors.extend(
            DefinitionIssue(path, DefinitionErrorCode.MALFORMED_PLACEHOLDER, {"fragment": fragment})
            for fragment in malformed_placeholders(template)
        )
        for name in placeholder_names(template):
            if name == API_KEY_VARIABLE:
                references_api_key = True
                continue
            errors.extend(variable_issues(path, name))
        if looks_like_literal_credential(template):
            warnings.append(DefinitionIssue(path, DefinitionErrorCode.AUTH_LITERAL_CREDENTIAL))
    errors.extend(duplicate_header_issues(join_path("auth", "headers"), auth.get("headers") or {}))
    if declares_credentials(auth) and not references_api_key:
        errors.append(DefinitionIssue("auth", DefinitionErrorCode.AUTH_WITHOUT_API_KEY))
    return AuthIssues(tuple(errors), tuple(warnings))


def render_auth(auth: Mapping[str, Any], *, api_key: str) -> tuple[dict[str, str], dict[str, str]]:
    """把 ``auth`` 节渲染成实际要发出的 ``(headers, query)``。

    ``api_key`` 为空时整节不渲染：ComfyUI 原生无鉴权，凭证只在套了反向代理的部署里才需要，而把
    ``Authorization: Bearer `` 这样的空凭证发出去，代理会以 401 拒绝一个本该放行的请求。

    ``api_key`` 以外的变量按校验器的规则在保存期就被拒（ComfyUI 的 auth 节只认这一个），故这里
    不做通用模板求值——真有漏网的占位符，原样留在值里比替换成空串更容易看出是哪一处写错了。
    """
    if not api_key:
        return {}, {}
    rendered: dict[str, dict[str, str]] = {"headers": {}, "query": {}}
    for group, name, _path, template in auth_templates(auth):
        rendered[group][name] = PLACEHOLDER.sub(
            lambda match: api_key if match.group(1) == API_KEY_VARIABLE else match.group(0), template
        )
    return rendered["headers"], rendered["query"]
