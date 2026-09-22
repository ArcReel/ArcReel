"""ComfyUI 端点定义的校验实现：``kind: comfyui`` 那一格的分派目标。

两层闸门：``schema.json`` 管结构（字段集、目标四元组的形状、条目可选键落在哪个语义键上），本模块
管语义——提示词与产物必须绑定、语义键按媒体类型走白名单、每个目标指向的节点与字段在 workflow 里
真的存在且不是连线、凭证只从 ``auth`` 节写入。两层的产出都是 :class:`DefinitionIssue`，与声明式
定义共用同一套诊断码。

纯逻辑：不碰数据库、不发请求、不读环境。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from lib.custom_provider.auth_section import API_KEY_VARIABLE, PLACEHOLDER, check_auth_section
from lib.custom_provider.definition_diagnostics import (
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    join_path,
)
from lib.custom_provider.definition_schema_errors import most_specific, translate_schema_error

from .bindings import BINDING_KEYS_BY_MEDIA_TYPE, REQUIRED_BINDING_KEYS, positive_number, targets_of
from .capabilities import fps_literals
from .graph import ancestors, link_of
from .workflow import is_link, node_inputs

SCHEMA_PATH = Path(__file__).parent / "schema.json"

#: ComfyUI 定义格式自身的版本，与声明式定义的版本线互不相干。
CURRENT_SCHEMA_VERSION = "1.0.0"

#: 声明式定义有、ComfyUI 定义没有的字段 → 其去处。照声明式的样子写一份 ComfyUI 定义时最容易写出
#: 这一个，笼统的「不认识的字段」说不清它为什么不在。
REMOVED_FIELD_REASONS: Mapping[str, str] = {
    "capabilities": "val_ce_removed_reason_comfyui_capabilities",
}


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
    auth = check_auth_section(document.get("auth") or {}, variable_issues=_auth_variable_issues)
    return DefinitionDiagnostics(
        errors=(*_semantic_issues(document), *auth.errors, *_reserved_auth_query_issues(document)),
        warnings=auth.warnings,
    )


#: 取产物那一跳（``GET /view``）自己要带的查询参数。凭证 query 与它们同名时，拼请求的那一步
#: 由产物参数覆盖凭证，提交与轮询都过得去、下载却少了凭证，代理多半回 401。
_VIEW_RESERVED_QUERY = frozenset({"filename", "subfolder", "type"})


def _reserved_auth_query_issues(document: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """``auth.query`` 里占用了产物下载路由自带参数名的条目。

    两者占不了同一个键：ComfyUI 认这三个参数才给得出文件，换掉它们等于换掉要下载的东西。
    保存期拒掉，好过让用户在一次已经出完片的执行上撞 401。
    """
    query: Mapping[str, Any] = (document.get("auth") or {}).get("query") or {}
    for name in query:
        if str(name) in _VIEW_RESERVED_QUERY:
            yield DefinitionIssue(
                join_path(join_path("auth", "query"), str(name)),
                DefinitionErrorCode.AUTH_QUERY_RESERVED,
                {"param": str(name)},
            )


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
    yield from _collision_issues(bindings, media_type)
    yield from _fps_issues(bindings, workflow, media_type)
    yield from _api_key_outside_auth_issues(document)


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
    yield from _class_type_issues(path, target, node)
    name = target.get("input")
    if name is None:
        yield from _consumer_issues(path, target, workflow)
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
        return
    yield from _consumer_issues(path, target, workflow)


def _consumer_issues(path: str, target: Mapping[str, Any], workflow: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """参考图条目嵌套的 ``consumer`` 也要对得上 workflow。

    它记的是「这个格子的图接进了谁的哪个入口」，实发构造照它决定张数变少时改图还是重复填充最后
    一张。三样都要对得上活图：节点在、入口在、类型没变，还要这个入口真的由这个格子喂着。

    只查前三样不够。定义是可分享、可手改的，随手写上一个存在且确实可选、却与这个格子无关的入口，
    前三关都过得去，实发构造于是判定「改得动图」，转而按必需分支去删节点——那一路可能直接撞上
    ``comfyui_image_drop_unsupported``，而正确处置本该是重复填充最后一张。
    """
    consumer = target.get("consumer")
    if not isinstance(consumer, Mapping):
        return
    consumer_path = join_path(path, "consumer")
    node_id = str(consumer["node"])
    node = workflow.get(node_id)
    if node is None:
        yield DefinitionIssue(
            join_path(consumer_path, "node"), DefinitionErrorCode.COMFYUI_NODE_NOT_FOUND, {"node": node_id}
        )
        return
    name = str(consumer["input"])
    inputs = node_inputs(node)
    if name not in inputs:
        yield DefinitionIssue(
            join_path(consumer_path, "input"),
            DefinitionErrorCode.COMFYUI_INPUT_NOT_FOUND,
            {"node": node_id, "input": name},
        )
    elif not _feeds(workflow, str(target["node"]), inputs[name]):
        yield DefinitionIssue(
            join_path(consumer_path, "input"),
            DefinitionErrorCode.COMFYUI_CONSUMER_NOT_FED,
            {"node": str(target["node"]), "consumer": node_id, "input": name},
        )
    yield from _class_type_issues(consumer_path, consumer, node)


def _class_type_issues(path: str, record: Mapping[str, Any], node: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """条目记下的 ``class_type`` 要与它指的那个节点现在的类型一致。

    这一项不是装饰：重匹配拿它认身份——节点还在、类型对不上，重匹配就不认这个落点，转而在全图
    找「类型加标题唯一」的另一个节点迁过去，或者判这条丢了。记错一个类型，绑定会悄悄搬到一个用户
    没指过的节点上。参考图的 ``consumer`` 同理，实发构造照它查可选入口表决定改不改图。
    """
    recorded = record.get("class_type")
    actual = node.get("class_type")
    if isinstance(recorded, str) and isinstance(actual, str) and actual != recorded:
        yield DefinitionIssue(
            join_path(path, "class_type"),
            DefinitionErrorCode.COMFYUI_CLASS_TYPE_MISMATCH,
            {"node": str(record["node"]), "class_type": recorded, "actual": actual},
        )


def _feeds(workflow: Mapping[str, Any], node_id: str, raw: object) -> bool:
    """某个入口上的这条连线，顺上游走得回这个节点吗。

    推断记下的消费者不一定是直接消费者：图会先过一段转接节点再落到收图的那个入口，因此这里按
    可达性判，而不是只比一条边的两端。
    """
    link = link_of(raw)
    if link is None:
        return False
    return link[0] == node_id or node_id in ancestors(workflow, link[0])


def _collision_issues(bindings: Mapping[str, Any], media_type: str) -> Iterator[DefinitionIssue]:
    """一个字段只能是一个语义键的写入落点。

    实发构造按语义键逐项填值，两个键落在同一个字段上时后填的那项盖掉先填的——``prompt`` 与
    ``negative_prompt`` 共用一个 ``text`` 入口时，用户拿到的负向提示词其实是正向那条，出图不对
    却看不出哪里错。同一个键上的两个条目落在一处同理：参考图第二张会盖掉第一张。

    只读目标不占落点（它只取值、不写回），节点级的产物目标没有字段可占。

    按定义里的书写顺序认定归属：先写的那个键留着落点，后写的那条报重复——报在用户能对上的位置。
    """
    allowed = BINDING_KEYS_BY_MEDIA_TYPE[media_type]
    owners: dict[tuple[str, str], str] = {}
    for key in bindings:
        if key not in allowed:
            continue
        for index, target in enumerate(bindings[key]):
            landing = _write_landing(target)
            if landing is None:
                continue
            if landing in owners:
                path = join_path(join_path(join_path("bindings", key), index), "input")
                yield DefinitionIssue(
                    path,
                    DefinitionErrorCode.COMFYUI_TARGET_COLLISION,
                    {"node": landing[0], "input": landing[1], "binding_key": key, "owner": owners[landing]},
                )
                continue
            owners[landing] = key


def _fps_issues(bindings: Mapping[str, Any], workflow: Mapping[str, Any], media_type: str) -> Iterator[DefinitionIssue]:
    """帧率只能有一个真相源：多个 ``fps`` 只读绑定读出的字面值必须一致。

    ``frames`` 的换算（``round(时长 × 帧率) + 1``）把一个帧率套到全部帧数目标上。两条只读绑定
    读出不同字面值时，这份定义自己就说不清这份 workflow 跑在哪个帧率上，构造层取第一个、另一条
    分支的帧数于是按错的帧率算出来，成片比用户选的长或短而无人报错；同一个数值由 ``fps`` 绑定与
    ``frames`` 条目上手填的常量各说一遍时同理。读不出字面值的绑定不参与判定——那是节点或字段已
    不在图里，由目标校验单独报。

    图像端点没有这两个语义键（``BINDING_KEYS_BY_MEDIA_TYPE``），不进此判。
    """
    if "fps" not in BINDING_KEYS_BY_MEDIA_TYPE[media_type]:
        return
    literals = fps_literals(workflow, bindings)
    manual = [value for target in targets_of(bindings.get("frames")) if (value := positive_number(target.get("fps")))]
    distinct = sorted({*literals, *manual})
    if len(distinct) > 1:
        yield DefinitionIssue(
            join_path("bindings", "fps"),
            DefinitionErrorCode.COMFYUI_FPS_CONFLICT,
            {"values": " / ".join(_format_fps(value) for value in distinct)},
        )


def _format_fps(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _write_landing(target: Mapping[str, Any]) -> tuple[str, str] | None:
    """这个目标会往哪个字段写值。只读目标与节点级的产物目标都没有落点。"""
    if target.get("direction") == "read":
        return None
    name = target.get("input")
    return (str(target["node"]), name) if isinstance(name, str) else None


def _auth_variable_issues(path: str, name: str) -> list[DefinitionIssue]:
    """ComfyUI 的 ``auth`` 节只认 ``api_key``：别的变量都无处取值。

    声明式定义的 auth 节可以引用 ``base_url`` 这类基础变量，ComfyUI 客户端没有那套模板上下文
    ——凭证之外的模板求值在这一侧根本不存在。
    """
    return [DefinitionIssue(path, DefinitionErrorCode.UNDECLARED_VARIABLE, {"name": name})]


def _api_key_outside_auth_issues(document: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """凭证只从 ``auth`` 节写入。

    workflow 是原样内嵌的底稿、提交时不作模板渲染，里面写 ``{{api_key}}`` 既不会生效，又会把
    真实凭证随导出文件分发出去。
    """
    for path in _api_key_outside_auth(document):
        yield DefinitionIssue(path, DefinitionErrorCode.API_KEY_OUTSIDE_AUTH)


def _api_key_outside_auth(document: Mapping[str, Any]) -> Iterator[str]:
    for field, value in document.items():
        if field == "auth":
            continue
        yield from _api_key_references(field, value)


def _api_key_references(path: str, value: object) -> Iterator[str]:
    if isinstance(value, str):
        if API_KEY_VARIABLE in PLACEHOLDER.findall(value):
            yield path
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _api_key_references(join_path(path, str(key)), child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _api_key_references(join_path(path, index), child)
