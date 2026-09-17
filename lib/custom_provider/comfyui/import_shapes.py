"""导入分流：一份粘进来的 JSON 是端点定义、API 格式 workflow，还是 UI 格式 workflow。

用户手上最常见的文件不是端点定义，而是 ComfyUI 自己导出的 workflow，且导出菜单有两项：
「Export」给出 UI 格式（带 ``nodes`` / ``links`` 的画布存档，没有 ``class_type``，提交不了），
「Export (API)」给出提交用的节点表。两者都不带 ``kind``，靠形状区分：分不出来就只能让用户对着
一堆字段级报错猜自己导错了菜单项。

API 格式在这里被包成一份 ``kind: comfyui`` 定义，节点绑定留空——包装只是把它接进端点定义这条
路，绑定推断是另一步，此刻还没有任何一项语义有着落。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from lib.custom_provider.definition_diagnostics import (
    ROOT_PATH,
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
)
from lib.custom_provider.endpoint_definition.kinds import COMFYUI_KIND

from .validator import CURRENT_SCHEMA_VERSION
from .workflow import has_any_node

#: 自动包装时写进 ``meta`` 的占位值：原始 workflow 里没有作者与名称可取，保存前由用户改写。
PLACEHOLDER_META_NAME = "ComfyUI workflow"
PLACEHOLDER_META_AUTHOR = "unknown"
PLACEHOLDER_META_VERSION = "1.0.0"


class ImportShape(StrEnum):
    """一份导入载荷的形状。"""

    #: 带 ``kind`` 的端点定义，按它自己的 kind 校验。
    ENDPOINT_DEFINITION = "endpoint_definition"
    #: ComfyUI「Export (API)」的节点表，自动包成 ComfyUI 端点定义。
    COMFYUI_API_WORKFLOW = "comfyui_api_workflow"
    #: ComfyUI「Export」的画布存档，提交不了，拒绝并让用户改用 Export (API)。
    COMFYUI_UI_WORKFLOW = "comfyui_ui_workflow"


def route_import_payload(document: object, *, media_type: str) -> tuple[ImportShape, Any]:
    """判定一份导入载荷的形状，并在它是 API workflow 时一并包成端点定义。

    非对象一律按端点定义走：定义的容器层已经会报出「定义必须是对象」，改口说「这不是 workflow」
    只会把根上的同一个类型错误说成两件事。UI 格式认 ``nodes`` 是数组加一个 ``links``——API 格式
    的键是节点 id，它的值是节点对象而非数组，两者撞不上。

    一个节点也没有的对象同样按端点定义走：漏写 ``kind`` 的端点定义此时该听见「缺 kind」，
    而不是它的每一节都被当成节点、报回一串定位到 ``workflow.<节名>`` 的次生错误。
    """
    if not isinstance(document, Mapping) or "kind" in document:
        return ImportShape.ENDPOINT_DEFINITION, document
    if isinstance(document.get("nodes"), list) and "links" in document:
        return ImportShape.COMFYUI_UI_WORKFLOW, document
    if not has_any_node(document):
        return ImportShape.ENDPOINT_DEFINITION, document
    return ImportShape.COMFYUI_API_WORKFLOW, wrap_api_workflow(document, media_type=media_type)


def ui_workflow_refusal() -> DefinitionDiagnostics:
    """UI 格式的拒绝理由：它不进定义校验器，一条结构化的诊断说清该改用哪个导出菜单。"""
    return DefinitionDiagnostics(errors=(DefinitionIssue(ROOT_PATH, DefinitionErrorCode.COMFYUI_UI_FORMAT_WORKFLOW),))


def wrap_api_workflow(workflow: Mapping[str, Any], *, media_type: str) -> dict[str, Any]:
    """把一份原始 API workflow 包成 ComfyUI 端点定义。

    ``bindings`` 是空对象而不是逐键的空列表：空列表表示「显式不支持」，而此刻每一项都只是还没
    推断过。产出的定义因此过不了「提示词与产物必须绑定」，这正是它的真实状态。
    """
    return {
        "kind": COMFYUI_KIND,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "meta": {
            "name": PLACEHOLDER_META_NAME,
            "author": PLACEHOLDER_META_AUTHOR,
            "version": PLACEHOLDER_META_VERSION,
        },
        "media_type": media_type,
        "workflow": dict(workflow),
        "bindings": {},
    }
