"""随版声明式内置端点：把 ``builtin_endpoints/*.json`` 读成经校验的定义并派生端点元数据。

内置调用端点有两种实现形态——Python backend，或随版附带的一份声明式定义。后者的定义 JSON 放在
``builtin_endpoints/`` 下，**文件名即内置键**，由 :mod:`lib.custom_provider.endpoints` 在 import
期读入注册表。定义不落库、用户不可编辑删除，升级换文件即生效。

装载是 fail-fast 的：任何一份定义不合法（JSON 解析失败、过不了共享校验器、键占用 ``ce-`` 前缀、
作者不是 ArcReel）进程就起不来，而不是等到用户挑中该端点发起生成才失败。校验一律走
:func:`lib.custom_provider.endpoint_definition.validate_definition`——随版定义与用户定义同一把尺子。

本模块只做「文件 → 定义 + 元数据」，不构造 :class:`~lib.custom_provider.endpoints.EndpointSpec`，
故不依赖 ``endpoints``（后者依赖本模块）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.custom_provider import CUSTOM_ENDPOINT_KEY_PREFIX
from lib.custom_provider.endpoint_definition import DefinitionIssue, validate_definition
from lib.video_backends.base import VideoCapabilities

#: 随版定义所在目录。文件名（不含 ``.json``）即内置端点键。
BUILTIN_DEFINITIONS_DIR = Path(__file__).parent / "builtin_endpoints"

#: 随版定义的作者署名。「复制为我的」产出的副本沿用它，用户改名改版本自便。
BUILTIN_DEFINITION_AUTHOR = "ArcReel"

#: 声明式定义描述的是「JSON in/out + 提交/轮询」的视频协议，媒体类型恒为 video。
DECLARATIVE_MEDIA_TYPE = "video"


class BuiltinDefinitionError(RuntimeError):
    """随版定义不合法。import 期抛出，进程随即起不来。"""


def load_builtin_definitions(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """读入目录下全部 ``<key>.json``，逐份校验，返回「内置键 → 定义」。

    键按文件名排序，注册表里的呈现顺序因此不随文件系统遍历顺序漂移。
    """
    base = BUILTIN_DEFINITIONS_DIR if directory is None else directory
    if not base.is_dir():
        raise BuiltinDefinitionError(f"随版定义目录不存在: {base}")
    return {path.stem: load_builtin_definition(path) for path in sorted(base.glob("*.json"))}


def load_builtin_definition(path: Path) -> dict[str, Any]:
    """读入并校验单份随版定义。不合法即抛 :class:`BuiltinDefinitionError`。"""
    key = path.stem
    if key.startswith(CUSTOM_ENDPOINT_KEY_PREFIX):
        raise BuiltinDefinitionError(
            f"随版定义 {path.name} 的内置键占用了自定义端点前缀 {CUSTOM_ENDPOINT_KEY_PREFIX!r}"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuiltinDefinitionError(f"随版定义 {path.name} 不是合法 JSON: {exc}") from exc
    diagnostics = validate_definition(document)
    if not diagnostics.valid:
        raise BuiltinDefinitionError(f"随版定义 {path.name} 未通过校验: {_format_issues(diagnostics.errors)}")
    author = document["meta"]["author"]
    if author != BUILTIN_DEFINITION_AUTHOR:
        raise BuiltinDefinitionError(
            f"随版定义 {path.name} 的 meta.author 必须是 {BUILTIN_DEFINITION_AUTHOR!r}，实际 {author!r}"
        )
    return document


def _format_issues(issues: Sequence[DefinitionIssue]) -> str:
    """把诊断拼成 import 期可读的一行。此处无请求上下文可取语言，故只渲染码与路径。"""
    return "; ".join(f"{issue.path}: {issue.code.value}" for issue in issues)


def declarative_family(key: str) -> str:
    """内置键的首段即家族（``newapi-video`` → ``newapi``）。"""
    return key.split("-", 1)[0]


def declarative_display_name(definition: Mapping[str, Any]) -> str:
    """显示名取 ``meta.name``：端点名是供应商专有名词，不翻译、不在定义之外另维护映射。"""
    name: str = definition["meta"]["name"]
    return name


def declarative_request_path(definition: Mapping[str, Any]) -> str:
    """提交 URL 模板去掉 ``{{ base_url }}`` 前缀后的部分，供 catalog 展示调用路径。

    定义里的 URL 是完整模板，而 catalog 的 ``request_path_template`` 与 Python 内置一样只呈现
    base_url 之后的路径；模板不以 base_url 起头时（供应商域名写死在定义里）原样返回。
    """
    url: str = definition["submit"]["url"]
    stripped = url.strip()
    for prefix in ("{{base_url}}", "{{ base_url }}"):
        if stripped.startswith(prefix):
            return stripped[len(prefix) :]
    return stripped


def declarative_video_capabilities(definition: Mapping[str, Any]) -> VideoCapabilities:
    """声明式能力投影的公开兼容名；实现集中在能力合成模块。"""
    from lib.custom_provider.capabilities import video_capabilities_from_definition

    return video_capabilities_from_definition(definition)
