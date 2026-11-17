"""在 JSON 字符预算内收窄错误参数，保持结构合法。

任务失败原因与使用记录的领域失败按同一口径裁剪：任务侧落库 ``[code] {params}`` 整串，使用记录分列
存 code 与 params，两者都按整串信封计入 :data:`FAILURE_MESSAGE_LIMIT`。
"""

from __future__ import annotations

import json
from typing import Any, TypeGuard

#: 结构化失败信封 ``[code] {params}`` 的字符上限。任务 ``error_message`` 落库按它收窄，使用记录的
#: code 与 params 分列落库，同样按整串信封计入，详情里显示的参数才与任务侧一致。
FAILURE_MESSAGE_LIMIT = 2000


def params_budget(code: str, limit: int) -> int:
    """信封 ``[code] {params}`` 在 ``limit`` 内留给 params JSON 的字符数。"""
    return limit - len(f"[{code}] ")


def as_shrinkable(value: Any) -> Any:
    """把容器参数值换成自身的 JSON 文本，好让裁剪逻辑能收窄它。

    截断一个容器的字符串形态比截断 JSON 字面量安全——后者会留下不闭合的括号。嵌套过深到
    ``json.dumps`` 撞递归上限的值降级为省略号：裁剪发生在落库路径上，抛出去会让任务卡在
    running。

    只动容器：``int`` / ``float`` / ``bool`` / ``None`` 本就是 JSON 原生标量，撑不爆预算也
    无从收窄，转成文本反而会在重新编码时被加上引号存回去（``42`` 变 ``"42"``、``True`` 变
    ``"true"``、``None`` 变 ``"null"``），把落库信封里的参数类型改掉。而裁剪是整个 params
    一起过一遍的，所以只要有任一参数触发裁剪，同条失败里全部标量都会跟着变形。
    """
    if not isinstance(value, dict | list):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except RecursionError:
        return "…"


def is_validation_message(value: Any) -> TypeGuard[dict[str, Any]]:
    """Return whether ``value`` is the locale-neutral nested diagnostic envelope."""
    return (
        isinstance(value, dict)
        and set(value) == {"key", "params"}
        and isinstance(value.get("key"), str)
        and isinstance(value.get("params"), dict)
    )


def shrinkable_copy(params: dict[str, Any]) -> dict[str, Any]:
    """容器参数换成可裁剪文本的副本；``detail`` 是嵌套诊断时保留 key/params 信封，只换它的 params。

    ``detail`` 必须保持机器结构，读侧才能按请求 locale 渲染。
    """
    copied = {key: as_shrinkable(value) for key, value in params.items()}
    detail = params.get("detail")
    if is_validation_message(detail):
        copied["detail"] = {
            "key": detail["key"],
            "params": {key: as_shrinkable(value) for key, value in detail["params"].items()},
        }
    return copied


def nested_detail_slots(params: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """嵌套诊断 ``detail`` 里的字符串叶子，与顶层参数一起参与裁剪预算竞争。"""
    detail = params.get("detail")
    if not is_validation_message(detail):
        return []
    nested = detail["params"]
    return [(nested, key) for key, value in nested.items() if isinstance(value, str)]


def shrink_json_strings(params: dict[str, Any], shrinkable: list[tuple[dict[str, Any], str]], limit: int) -> bool:
    """原位裁剪指定字符串，按实际 JSON 转义长度收敛；骨架放不下时返回 False。"""
    encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    while len(encoded) > limit:
        if not shrinkable:
            return False
        owner, longest_key = max(shrinkable, key=lambda item: len(item[0][item[1]]))
        current: str = owner[longest_key]
        if not current:
            return False
        # JSON 转义会放大长度；差值超过原串时逐次减半，保留可容纳的诊断内容。
        deficit = len(encoded) - limit
        drop = deficit if deficit < len(current) else max(1, len(current) // 2)
        owner[longest_key] = current[: len(current) - drop]
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    return True
