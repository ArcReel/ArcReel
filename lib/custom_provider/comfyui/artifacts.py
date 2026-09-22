"""一次执行的终态判定与产物挑选：两个媒体通道共读这一份。

ComfyUI 的 history 条目不说「这次成功了」——它把每个节点的产出与一串执行事件摊在一起，成败
要读末尾事件，成片要读 ``output`` 绑定指的那个节点，而「产出的是图还是片」只有文件扩展名说得
准（``VHS_VideoCombine`` 也能导 webp 动图，节点类型不足以分辨）。

判定与挑选都是纯函数，落在子包里：它们只认 history 的形状与绑定表，与 backend 层的请求 / 结果
类型无关。扩展名白名单按 ``media_type`` 收在一张表上，而不是各通道一份——两份表意味着「这个端点
该产什么」有两种理解，一边放行的扩展名另一边会判类型不符。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .bindings import targets_of
from .failures import EXECUTION_ERROR, INTERRUPTED, ComfyuiError

#: ``media_type`` → 该媒体类型的产物允许的扩展名。
#:
#: 图像这一侧刻意不含 ``.gif`` / ``.apng``：两者是动图，一份图像端点产出它们意味着产物绑定指
#: 错了节点（多半指在了视频合成节点上），当成分镜图入库会得到一张只有首帧的图。视频这一侧同理
#: 不含它们——动图不是成片容器。
ARTIFACT_SUFFIXES_BY_MEDIA_TYPE: Mapping[str, frozenset[str]] = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".webp"}),
    "video": frozenset({".mp4", ".webm", ".mov"}),
}

#: history 条目里可能挂产物的三个键。只读这三个，且只读 ``output`` 绑定的那个节点。
_ARTIFACT_KEYS = ("images", "gifs", "audio")

#: ``status.messages`` 里表示这次执行没能出片的两个事件名。
_EXECUTION_ERROR_EVENT = "execution_error"
_EXECUTION_INTERRUPTED_EVENT = "execution_interrupted"

#: 认不出出错节点时 ``comfyui_execution_error`` 的 ``node`` 占位值。
_UNKNOWN_NODE = "-"


def output_nodes_of(definition: Mapping[str, Any]) -> list[str]:
    """``output`` 绑定指的那些节点号，按绑定次序。"""
    return [str(target["node"]) for target in targets_of((definition.get("bindings") or {}).get("output"))]


def output_artifacts(entry: Mapping[str, Any], output_nodes: Sequence[str]) -> list[Mapping[str, Any]]:
    """``output`` 绑定的节点这次产出的文件，按绑定次序、每个节点按三个键的次序。

    只读被绑定的那些节点：一份 workflow 里 ``PreviewImage`` 之类的旁支同样会往 history 写产物，
    扫全图会把一张预览图当成成片取走。``type != "output"`` 的条目一律跳过——``temp`` 是中间预览，
    服务端随时会清掉它。
    """
    outputs = entry.get("outputs")
    if not isinstance(outputs, Mapping):
        return []
    found: list[Mapping[str, Any]] = []
    for node_id in output_nodes:
        node = outputs.get(node_id)
        if not isinstance(node, Mapping):
            continue
        for key in _ARTIFACT_KEYS:
            items = node.get(key)
            if not isinstance(items, list):
                continue
            found.extend(
                item for item in items if isinstance(item, Mapping) and str(item.get("type") or "") == "output"
            )
    return found


def pick_artifact(artifacts: Sequence[Mapping[str, Any]], media_type: str) -> Mapping[str, Any] | None:
    """这批产物里第一个属于 ``media_type`` 的文件；一个都没有时 ``None``。

    按扩展名挑而不是取第一个：一个绑定节点可以同时往 ``images`` 与 ``gifs`` 写（缩略图加成片），
    而 :func:`output_artifacts` 给出的次序是 ``_ARTIFACT_KEYS`` 自己的次序，与「哪个是成片」无关。

    白名单外的扩展名一律不算命中，连同没有登记的 ``media_type``：那两种情形下调用方会报
    ``comfyui_output_type_mismatch``，把「绑定指错了节点」说给用户，而不是把一份不该入库的文件
    当成这一版的产物。
    """
    allowed = ARTIFACT_SUFFIXES_BY_MEDIA_TYPE.get(media_type, frozenset())
    return next((item for item in artifacts if Path(filename_of(item)).suffix.lower() in allowed), None)


def expected_suffixes_text(media_type: str) -> str:
    """``media_type`` 允许的扩展名，拼成失败文案里那一段清单；未登记的媒体类型给空串。

    读侧渲染 ``comfyui_output_type_mismatch`` 时按落库的 ``media_type`` 现算，白名单因此不进
    落库参数：清单是一张静态表的投影，不是这次失败的事实，存一份只会让历史记录与表各说各话。
    """
    return " / ".join(sorted(ARTIFACT_SUFFIXES_BY_MEDIA_TYPE.get(media_type, frozenset())))


def filename_of(artifact: Mapping[str, Any]) -> str:
    """一个产物条目的文件名；缺失时空串（扩展名判定与失败文案都容得下它）。"""
    return str(artifact.get("filename") or "")


def terminal_failure(entry: Mapping[str, Any]) -> ComfyuiError | None:
    """一条终态记录说的是成功还是失败——失败给出失败码，成功给 ``None``。

    判据是 ``status.messages`` 的**末尾事件**而不是 ``status_str`` / ``completed``：一次执行里
    前面的节点报错、后面的节点照跑完是常态，按「有没有出现过 error」判会把成片误判成失败，而
    ``completed`` 在被打断的执行上同样为真。

    ``status`` 为 null（部分版本与代理的形状）时无从判起：这一格照 outputs 分——有产出就当它跑
    完了，一个产出都没有则按执行失败兜底，否则这次执行会一路走到「产物节点没出东西」，把一个
    环境问题说成绑定配错了。
    """
    status = entry.get("status")
    if not isinstance(status, Mapping):
        if _has_outputs(entry):
            return None
        return ComfyuiError(
            EXECUTION_ERROR,
            node=_UNKNOWN_NODE,
            detail="ComfyUI reported no execution status and no outputs",
        )
    event, data = _last_message(status)
    if event == _EXECUTION_ERROR_EVENT:
        return ComfyuiError(EXECUTION_ERROR, node=_error_node(data), detail=_error_detail(data))
    if event == _EXECUTION_INTERRUPTED_EVENT:
        return ComfyuiError(INTERRUPTED)
    return None


def history_digest(entry: Mapping[str, Any], output_nodes: Sequence[str]) -> dict[str, Any]:
    """留痕用的 history 摘要：只留状态与 ``output`` 节点那一段。

    整份 history 带着每个节点的全部产出，一条留痕就能把诊断列撑到几百 KB，而排查要看的只有这
    两块。
    """
    outputs = entry.get("outputs")
    kept = (
        {node_id: outputs[node_id] for node_id in output_nodes if node_id in outputs}
        if isinstance(outputs, Mapping)
        else {}
    )
    return {"status": entry.get("status"), "outputs": kept}


def _last_message(status: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """``status.messages`` 的末尾事件，形状是 ``[事件名, 数据]``；没有消息时事件名为空串。"""
    messages = status.get("messages")
    if not isinstance(messages, list) or not messages:
        return "", {}
    last = messages[-1]
    if not isinstance(last, list) or not last:
        return "", {}
    data = last[1] if len(last) > 1 and isinstance(last[1], Mapping) else {}
    return str(last[0] or ""), data


def _error_node(data: Mapping[str, Any]) -> str:
    """报错节点在用户那里的名字：``node_type`` 就是画布上的节点类型，认不出退到节点号。"""
    return str(data.get("node_type") or data.get("node_id") or _UNKNOWN_NODE)


def _error_detail(data: Mapping[str, Any]) -> str:
    """异常摘要只取消息与类型两行，不带 traceback——它有上百行，而失败原因要整条落库。"""
    message = str(data.get("exception_message") or "").strip()
    exception_type = str(data.get("exception_type") or "").strip()
    if message and exception_type:
        return f"{exception_type}: {message}"
    return message or exception_type or "node raised an exception"


def _has_outputs(entry: Mapping[str, Any]) -> bool:
    outputs = entry.get("outputs")
    return isinstance(outputs, Mapping) and bool(outputs)
