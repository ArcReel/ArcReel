"""ComfyUI 端点定义的校验实现：``kind: comfyui`` 那一格的分派目标。

两层闸门：``schema.json`` 管结构（字段集、目标四元组的形状、条目可选键落在哪个语义键上），本模块
管语义——提示词与产物必须绑定、语义键按媒体类型走白名单、每个目标指向的节点与字段在 workflow 里
真的存在且不是连线、凭证只从 ``auth`` 节写入。两层的产出都是 :class:`DefinitionIssue`，与声明式
定义共用同一套诊断码。

纯逻辑：不碰数据库、不发请求、不读环境。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from lib.custom_provider.definition_diagnostics import (
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    join_path,
)
from lib.custom_provider.definition_schema_errors import most_specific, translate_schema_error

from .bindings import BINDING_KEYS_BY_MEDIA_TYPE, REQUIRED_BINDING_KEYS
from .workflow import is_link, node_inputs

SCHEMA_PATH = Path(__file__).parent / "schema.json"

#: ComfyUI 定义格式自身的版本，与声明式定义的版本线互不相干。
CURRENT_SCHEMA_VERSION = "1.0.0"

#: 声明式定义有、ComfyUI 定义没有的字段 → 其去处。照声明式的样子写一份 ComfyUI 定义时最容易写出
#: 这一个，笼统的「不认识的字段」说不清它为什么不在。
REMOVED_FIELD_REASONS: Mapping[str, str] = {
    "capabilities": "val_ce_removed_reason_comfyui_capabilities",
}

#: ``auth`` 里唯一可用的变量。
_API_KEY_VARIABLE = "api_key"

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")


@cache
def load_schema() -> dict[str, Any]:
    """读入并缓存 ComfyUI 定义的 ``schema.json``。对外公开，供文档站与前端取同一份契约。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def _schema_validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_comfyui_definition(document: Mapping[str, Any]) -> DefinitionDiagnostics:
    """ComfyUI 定义的两层闸门。

    结构层有错时不再跑语义层：绑定与 workflow 的交叉检查都以两边形状成立为前提，在残缺结构上
    继续跑只会产出误导性的次生错误。
    """
    structural = structural_diagnostics(document)
    if structural.errors:
        return structural
    return DefinitionDiagnostics(errors=tuple(_semantic_issues(document)))


def structural_diagnostics(document: object) -> DefinitionDiagnostics:
    """只跑结构层：字段集、目标四元组的形状、条目可选键落在哪个语义键上。

    节点绑定推断要在「结构立得住、绑定还对不上」的定义上跑——重导入一份改过的 workflow 时，既有
    条目指向的节点大半已不存在，那正是重匹配要处理的输入，不是拒绝它的理由。语义层在那种定义上
    只会报一串注定为真的错误。
    """
    return DefinitionDiagnostics(errors=tuple(_structural_issues(document)))


def _structural_issues(document: Any) -> Iterator[DefinitionIssue]:
    for error in _schema_validator().iter_errors(document):
        yield from translate_schema_error(most_specific(error), removed_fields=REMOVED_FIELD_REASONS)


def _semantic_issues(document: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    bindings: Mapping[str, Any] = document["bindings"]
    workflow: Mapping[str, Any] = document["workflow"]
    media_type = str(document["media_type"])
    yield from _required_binding_issues(bindings)
    yield from _media_type_issues(bindings, media_type)
    yield from _target_issues(bindings, workflow, media_type)
    yield from _auth_issues(document)


def _required_binding_issues(bindings: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """提示词与产物必须已绑定：空列表与键缺失都不够。"""
    for key in REQUIRED_BINDING_KEYS:
        if not bindings.get(key):
            yield DefinitionIssue(
                join_path("bindings", key), DefinitionErrorCode.COMFYUI_BINDING_REQUIRED, {"binding_key": key}
            )


def _media_type_issues(bindings: Mapping[str, Any], media_type: str) -> Iterator[DefinitionIssue]:
    """语义键按媒体类型走白名单：图像端点没有首尾帧，也没有帧数与帧率。"""
    allowed = BINDING_KEYS_BY_MEDIA_TYPE[media_type]
    for key in bindings:
        if key not in allowed:
            yield DefinitionIssue(
                join_path("bindings", key),
                DefinitionErrorCode.COMFYUI_BINDING_KEY_NOT_ALLOWED,
                {"binding_key": key, "media_type": media_type, "allowed": " / ".join(sorted(allowed))},
            )


def _target_issues(
    bindings: Mapping[str, Any], workflow: Mapping[str, Any], media_type: str
) -> Iterator[DefinitionIssue]:
    """每个目标都要落在 workflow 里真实存在、且不是连线的字段上。

    越界的语义键不再逐条查目标：它的条目本就不会被填值，再报一串定位到节点的错误只会淹没
    「这个键在图像端点上不存在」这条真正的诊断。
    """
    allowed = BINDING_KEYS_BY_MEDIA_TYPE[media_type]
    for key, targets in bindings.items():
        if key not in allowed:
            continue
        for index, target in enumerate(targets):
            yield from _one_target_issues(join_path(join_path("bindings", key), index), target, workflow)


def _one_target_issues(path: str, target: Mapping[str, Any], workflow: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    node_id = str(target["node"])
    node = workflow.get(node_id)
    if node is None:
        yield DefinitionIssue(join_path(path, "node"), DefinitionErrorCode.COMFYUI_NODE_NOT_FOUND, {"node": node_id})
        return
    name = target.get("input")
    if name is None:
        return
    inputs = node_inputs(node)
    input_path = join_path(path, "input")
    if name not in inputs:
        yield DefinitionIssue(
            input_path, DefinitionErrorCode.COMFYUI_INPUT_NOT_FOUND, {"node": node_id, "input": str(name)}
        )
        return
    if is_link(inputs[name]):
        yield DefinitionIssue(
            input_path, DefinitionErrorCode.COMFYUI_INPUT_IS_LINK, {"node": node_id, "input": str(name)}
        )


def _auth_issues(document: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """凭证只从 ``auth`` 节写入，且该节只认 ``api_key`` 一个变量。

    workflow 是原样内嵌的底稿、提交时不作模板渲染，里面写 ``{{api_key}}`` 既不会生效，又会把
    真实凭证随导出文件分发出去。
    """
    auth: Mapping[str, Any] = document.get("auth") or {}
    references_api_key = False
    for path, template in _auth_templates(auth):
        for name in _PLACEHOLDER.findall(template):
            if name == _API_KEY_VARIABLE:
                references_api_key = True
            else:
                yield DefinitionIssue(path, DefinitionErrorCode.UNDECLARED_VARIABLE, {"name": name})
    if auth and not references_api_key:
        yield DefinitionIssue("auth", DefinitionErrorCode.AUTH_WITHOUT_API_KEY)
    for path in _api_key_outside_auth(document):
        yield DefinitionIssue(path, DefinitionErrorCode.API_KEY_OUTSIDE_AUTH)


def _auth_templates(auth: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for group in ("headers", "query"):
        values: Mapping[str, Any] = auth.get(group) or {}
        for name, template in values.items():
            yield join_path(join_path("auth", group), name), str(template)


def _api_key_outside_auth(document: Mapping[str, Any]) -> Iterator[str]:
    for field, value in document.items():
        if field == "auth":
            continue
        yield from _api_key_references(field, value)


def _api_key_references(path: str, value: object) -> Iterator[str]:
    if isinstance(value, str):
        if _API_KEY_VARIABLE in _PLACEHOLDER.findall(value):
            yield path
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _api_key_references(join_path(path, str(key)), child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _api_key_references(join_path(path, index), child)
