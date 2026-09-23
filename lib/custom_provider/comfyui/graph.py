"""API 格式 workflow 的图原语：连线解析、上下游遍历、依赖深度与常量上溯。

节点绑定的推断读的是图的形状而不是某个字段的字面值：一张图里 ``LoadImage.image`` 是首帧还是
参考图，取决于它的输出接到了谁的哪个入口；两个 ``SaveVideo`` 哪个是最终产物，取决于谁的上游
更深。这些判定都要按边走，:mod:`.workflow` 只认得单个字段的形状，因此把边的部分单列在这里。

节点 id 在 API 格式里是对象的键，边写在消费方：``"positive": ["6", 0]`` 是「本节点的 positive
入口接 6 号节点的 0 号输出」。反向边（谁接了我）在导出物里没有，本模块按整图扫描一次建出来。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .workflow import VALUE_WRAPPER_KEY, is_link, node_inputs

#: 一条连线：``(上游节点 id, 上游输出序号)``。
type LinkRef = tuple[str, int]


@dataclass(frozen=True)
class Consumer:
    """接在某个节点输出上的下游入口。"""

    node: str
    input: str
    class_type: str
    title: str
    #: 被接的是上游节点的第几号输出。模型条件节点的 0 号是 positive、1 号是 negative。
    slot: int


def node_title(node: Mapping[str, Any]) -> str:
    """节点的 ``_meta.title``。缺省为空串——导出物里它可能整节缺失。"""
    meta = node.get("_meta")
    if not isinstance(meta, Mapping):
        return ""
    title = meta.get("title")
    return title if isinstance(title, str) else ""


def class_type_of(node: Mapping[str, Any]) -> str:
    raw = node.get("class_type")
    return raw if isinstance(raw, str) else ""


def unwrap_value(raw: object) -> object:
    """去掉数组字面值的 ``{"__value__": [...]}`` 包装，其余值原样返回。"""
    if isinstance(raw, Mapping) and set(raw) == {VALUE_WRAPPER_KEY}:
        return raw[VALUE_WRAPPER_KEY]
    return raw


def link_of(raw: object) -> LinkRef | None:
    """该字段接的上游连线；是字面值则为 ``None``。"""
    if not is_link(raw):
        return None
    pair: Any = raw
    return str(pair[0]), int(pair[1]) if isinstance(pair[1], int) else 0


def consumers_by_node(workflow: Mapping[str, Any]) -> Mapping[str, tuple[Consumer, ...]]:
    """反向边：上游节点 id → 接在它输出上的全部下游入口。"""
    found: dict[str, list[Consumer]] = {}
    for node_id, node in workflow.items():
        if not isinstance(node, Mapping):
            continue
        for name, raw in node_inputs(node).items():
            link = link_of(raw)
            if link is None:
                continue
            found.setdefault(link[0], []).append(
                Consumer(node_id, name, class_type_of(node), node_title(node), link[1])
            )
    return {node_id: tuple(items) for node_id, items in found.items()}


def upstream_of(workflow: Mapping[str, Any], node_id: str) -> Iterator[tuple[str, LinkRef]]:
    """某个节点的全部上游边：``(本节点入口名, 上游连线)``。"""
    node = workflow.get(node_id)
    if not isinstance(node, Mapping):
        return
    for name, raw in node_inputs(node).items():
        link = link_of(raw)
        if link is not None:
            yield name, link


def ancestors(workflow: Mapping[str, Any], node_id: str) -> frozenset[str]:
    """该节点的全部上游节点（不含自身）。用来圈出最终产物那条链路。"""
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for _, (parent, _slot) in upstream_of(workflow, current):
            if parent not in seen and parent != node_id:
                seen.add(parent)
                stack.append(parent)
    return frozenset(seen)


def dependency_depth(workflow: Mapping[str, Any], node_id: str) -> int:
    """该节点上游最长链的长度。两段式 workflow 里第二段的产物深度严格大于第一段。

    环在合法的 workflow 里不存在（ComfyUI 的执行器要求 DAG），但导入的是用户文件，遇到环时按
    「不再往回走」收敛：回边上的节点记 0，不再往回走第二遍。

    显式栈而非递归：推断接口收的是用户导入的整份 workflow，节点数与链路长度都没有上限，一条足够
    长的合法链会把递归打穿成 ``RecursionError``——那会让一份没有任何问题的 workflow 拿到 500。
    """
    memo: dict[str, int] = {}
    on_path: set[str] = set()
    stack: list[tuple[str, bool]] = [(node_id, False)]
    while stack:
        current, expanded = stack.pop()
        if expanded:
            on_path.discard(current)
            parents = [parent for _, (parent, _slot) in upstream_of(workflow, current)]
            # 此刻不在 memo 里的父节点只可能是回边：它还在当前路径上，按 0 计。
            memo[current] = 1 + max(memo.get(parent, 0) for parent in parents) if parents else 0
            continue
        if current in memo:
            continue
        on_path.add(current)
        stack.append((current, True))
        for _, (parent, _slot) in upstream_of(workflow, current):
            if parent not in memo and parent not in on_path:
                stack.append((parent, False))
    return memo[node_id]


@dataclass(frozen=True)
class Resolution:
    """一次「顺连线找字面值」的结果。

    ``node`` / ``input`` 是字面值真正所在的落点——原字段本身是字面值时即它自己，是连线时是上溯
    到的常量节点。上溯停在计算节点上时三项全空：那种字面值不存在，绑过去也写不动。
    """

    node: str | None
    input: str | None
    value: object | None

    @property
    def found(self) -> bool:
        return self.node is not None


#: 上溯停不下来时的结果。
UNRESOLVED = Resolution(None, None, None)


def resolve_literal(
    workflow: Mapping[str, Any], node_id: str, input_name: str, *, constants: Mapping[str, str]
) -> Resolution:
    """取某个入口的字面值；它是连线时沿上游找常量节点。

    ``constants`` 是「常量节点 class_type → 它持有值的入口名」。官方模板把时长与帧率放在
    ``PrimitiveInt`` / ``PrimitiveFloat`` 上再连进条件节点，绑到常量节点上与绑到字面值字段上
    等效；上溯到 ``ComfyMathExpression`` 这类计算节点则没有落点，该语义交给用户手动指定。
    """
    node = workflow.get(node_id)
    if not isinstance(node, Mapping):
        return UNRESOLVED
    inputs = node_inputs(node)
    # 按键判在不在，而不是按值是不是 None：``"text": null`` 是个存在的字面值字段，填得进去，
    # 与「这个节点根本没有这个入口」不是一回事。
    if input_name not in inputs:
        return UNRESOLVED
    raw = inputs[input_name]
    link = link_of(raw)
    if link is None:
        return Resolution(node_id, input_name, unwrap_value(raw))
    seen: set[str] = {node_id}
    current = link[0]
    while current not in seen:
        seen.add(current)
        upstream = workflow.get(current)
        if not isinstance(upstream, Mapping):
            return UNRESOLVED
        constant_input = constants.get(class_type_of(upstream))
        if constant_input is None:
            return UNRESOLVED
        upstream_inputs = node_inputs(upstream)
        if constant_input not in upstream_inputs:
            return UNRESOLVED
        value = upstream_inputs[constant_input]
        next_link = link_of(value)
        if next_link is None:
            return Resolution(current, constant_input, unwrap_value(value))
        current = next_link[0]
    return UNRESOLVED
