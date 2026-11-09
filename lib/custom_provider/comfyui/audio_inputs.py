"""按有序绑定填参考音频；已知多输入节点按消费顺序接线，未知欠填形态拒绝。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from arcreel_market_core.comfyui.bindings import targets_of
from arcreel_market_core.comfyui.graph import ancestors
from arcreel_market_core.comfyui.workflow import is_link, node_inputs
from arcreel_market_core.video_backend_contract import VideoCapabilityError

from .failures import ComfyuiError
from .inference_rules import load_inference_rules


def apply_reference_audio(
    workflow: dict[str, Any],
    bindings: Mapping[str, Any],
    values: Sequence[str],
) -> tuple[str, ...]:
    targets = targets_of(bindings.get("reference_audio"))
    if values and not targets:
        raise VideoCapabilityError("video_reference_audio_unsupported", provider="ComfyUI", model="workflow")
    if len(values) > len(targets):
        raise VideoCapabilityError(
            "video_reference_audio_exceeded", model="workflow", limit=len(targets), count=len(values)
        )
    if not targets:
        return ()
    rules = load_inference_rules("video")
    target_nodes = [str(target["node"]) for target in targets]
    output = str(targets_of(bindings.get("output"))[0]["node"])
    if not set(target_nodes).issubset(ancestors(workflow, output)):
        raise ComfyuiError("comfyui_reference_audio_invalid")
    # Autogrow 按编号消费，加载节点的编号与绑定次序都不能代替消费者的输入顺序。
    for node in workflow.values():
        if node.get("class_type") != "MiniMaxH3ReferenceToVideo":
            continue
        inputs = node.get("inputs", {})
        if any(not name.rsplit("_", 1)[-1].isdigit() for name in inputs if name.startswith("ref_audios.ref_audio_")):
            raise ComfyuiError("comfyui_reference_audio_invalid")
        ports = sorted(
            (name for name in inputs if name.startswith("ref_audios.ref_audio_")),
            key=lambda name: int(name.rsplit("_", 1)[-1]),
        )
        linked = []
        for name in ports:
            raw = inputs[name]
            sources = ({str(raw[0])} | ancestors(workflow, str(raw[0]))) if is_link(raw) else set()
            matches = set(target_nodes) & sources
            if len(matches) > 1:
                raise ComfyuiError("comfyui_reference_audio_invalid")
            linked.append(next(iter(matches)) if matches else None)
        selected = [node_id for node_id in target_nodes if node_id in linked]
        if not selected:
            continue
        if None in linked or len(set(linked)) != len(linked) or selected != target_nodes:
            raise ComfyuiError("comfyui_reference_audio_invalid")
        if any(name.startswith("ref_videos.") for name in inputs):
            raise ComfyuiError("comfyui_reference_audio_invalid")
        links = {node_id: inputs[name] for name, node_id in zip(ports, linked, strict=True)}
        for name, node_id in zip(ports, selected, strict=True):
            inputs[name] = links[node_id]
    dropped = []
    for index, target in enumerate(targets):
        node_id = str(target["node"])
        if index < len(values):
            workflow[node_id]["inputs"][target["input"]] = values[index]
            continue
        consumers = [
            (node, name)
            for node in workflow.values()
            for name, raw in node_inputs(node).items()
            if is_link(raw) and str(raw[0]) == node_id
        ]
        if not consumers or any(
            not rules.is_optional_input(str(node.get("class_type")), name) for node, name in consumers
        ):
            raise ComfyuiError("comfyui_reference_audio_invalid")
        for node, name in consumers:
            del node["inputs"][name]
        del workflow[node_id]
        dropped.append(node_id)
    return tuple(dropped)
