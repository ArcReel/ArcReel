"""ComfyUI API workflow discovery, validation, and request binding helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

COMFYUI_ENDPOINT = "comfyui-workflow"
COMFYUI_CONFIG_VERSION = 1

_PROMPT_FIELDS = ("prompt", "text", "positive_prompt")
_SEED_FIELDS = ("noise_seed", "seed")
_DURATION_FIELDS = ("duration_seconds", "duration", "seconds")
_FRAME_FIELDS = ("length", "num_frames", "frames", "frame_count")
_VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi"})


class ComfyUIWorkflowConfigError(ValueError):
    """Stored workflow configuration is malformed or no longer self-consistent."""


def normalize_comfyui_base_url(base_url: str) -> str:
    """Normalize a ComfyUI server URL without appending an API version path."""
    value = base_url.strip().rstrip("/")
    if value and "://" not in value:
        value = f"http://{value}"
    if not value:
        raise ComfyUIWorkflowConfigError("ComfyUI base_url is required")
    return value


def comfyui_headers(api_key: str | None) -> dict[str, str]:
    """Return optional bearer authentication headers for protected reverse proxies."""
    key = (api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _node(workflow: Mapping[str, object], node_id: str) -> dict[str, Any]:
    value = workflow.get(node_id)
    if not isinstance(value, dict):
        raise ComfyUIWorkflowConfigError(f"workflow node {node_id!r} is missing")
    inputs = value.get("inputs")
    if not isinstance(inputs, dict):
        raise ComfyUIWorkflowConfigError(f"workflow node {node_id!r} has no inputs object")
    return value


def _binding(config: Mapping[str, object], name: str, *, required: bool = False) -> dict[str, Any] | None:
    bindings = config.get("bindings")
    value = bindings.get(name) if isinstance(bindings, dict) else None
    if value is None and not required:
        return None
    if not isinstance(value, dict):
        raise ComfyUIWorkflowConfigError(f"ComfyUI binding {name!r} is required")
    return value


def validate_comfyui_endpoint_config(value: object) -> dict[str, Any]:
    """Validate the persisted endpoint_config and return a defensive deep copy."""
    if not isinstance(value, dict):
        raise ComfyUIWorkflowConfigError("ComfyUI endpoint_config must be an object")
    if value.get("version") != COMFYUI_CONFIG_VERSION:
        raise ComfyUIWorkflowConfigError(f"unsupported ComfyUI endpoint_config version: {value.get('version')!r}")
    workflow = value.get("workflow")
    bindings = value.get("bindings")
    if not isinstance(workflow, dict) or not workflow:
        raise ComfyUIWorkflowConfigError("ComfyUI workflow must be a non-empty object")
    if len(workflow) > 5000:
        raise ComfyUIWorkflowConfigError("ComfyUI workflow has too many nodes")
    if not isinstance(bindings, dict):
        raise ComfyUIWorkflowConfigError("ComfyUI bindings must be an object")

    normalized = copy.deepcopy(value)
    for name in ("prompt", "output"):
        binding = _binding(normalized, name, required=True)
        assert binding is not None
        node_id = binding.get("node_id")
        field = binding.get("field")
        if not isinstance(node_id, str) or not isinstance(field, str):
            raise ComfyUIWorkflowConfigError(f"ComfyUI binding {name!r} needs node_id and field")
        node = _node(workflow, node_id)
        if field not in node["inputs"]:
            raise ComfyUIWorkflowConfigError(f"ComfyUI binding {name!r} points to a missing input")

    for name in ("seed", "duration", "aspect_ratio", "start_image", "end_image"):
        binding = _binding(normalized, name)
        if binding is None:
            continue
        mode = binding.get("mode", "value")
        node_id = binding.get("node_id")
        field = binding.get("field")
        if mode not in {"value", "loader", "inject_loader"}:
            raise ComfyUIWorkflowConfigError(f"ComfyUI binding {name!r} has invalid mode {mode!r}")
        if not isinstance(node_id, str) or not isinstance(field, str):
            raise ComfyUIWorkflowConfigError(f"ComfyUI binding {name!r} needs node_id and field")
        node = _node(workflow, node_id)
        if mode != "inject_loader" and field not in node["inputs"]:
            raise ComfyUIWorkflowConfigError(f"ComfyUI binding {name!r} points to a missing input")

    return normalized


def _schema_inputs(object_info: Mapping[str, object], class_type: str) -> set[str]:
    info = object_info.get(class_type)
    if not isinstance(info, dict):
        return set()
    input_spec = info.get("input")
    if not isinstance(input_spec, dict):
        return set()
    names: set[str] = set()
    for group in ("required", "optional"):
        fields = input_spec.get(group)
        if isinstance(fields, dict):
            names.update(str(name) for name in fields)
    return names


def _enum_options(object_info: Mapping[str, object], class_type: str, field: str) -> list[str]:
    info = object_info.get(class_type)
    if not isinstance(info, dict):
        return []
    input_spec = info.get("input")
    if not isinstance(input_spec, dict):
        return []
    for group in ("required", "optional"):
        fields = input_spec.get(group)
        raw = fields.get(field) if isinstance(fields, dict) else None
        if isinstance(raw, list) and raw and isinstance(raw[0], list):
            return [str(option) for option in raw[0] if isinstance(option, str)]
    return []


def _first_input_field(inputs: Mapping[str, object], names: tuple[str, ...]) -> str | None:
    return next((name for name in names if name in inputs), None)


def _rank_prompt_node(node: Mapping[str, object]) -> tuple[int, int]:
    class_type = str(node.get("class_type", "")).lower()
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return (99, 99)
    field = _first_input_field(inputs, _PROMPT_FIELDS)
    if field is None:
        return (99, 99)
    return (0 if "video" in class_type else 1, _PROMPT_FIELDS.index(field))


def _find_value_binding(workflow: Mapping[str, object], fields: tuple[str, ...]) -> dict[str, Any] | None:
    for field in fields:
        for node_id, raw_node in workflow.items():
            if not isinstance(raw_node, dict) or not isinstance(raw_node.get("inputs"), dict):
                continue
            if field in raw_node["inputs"]:
                return {"node_id": str(node_id), "field": field, "mode": "value"}
    return None


def _find_duration_binding(workflow: Mapping[str, object]) -> dict[str, Any] | None:
    # Workflow templates commonly expose a PrimitiveFloat titled "duration" and derive the
    # model-specific frame grid through downstream math nodes. Binding that scalar preserves
    # the workflow author's rounding formula instead of duplicating it in ArcReel.
    for node_id, raw_node in workflow.items():
        if not isinstance(raw_node, dict) or not isinstance(raw_node.get("inputs"), dict):
            continue
        meta = raw_node.get("_meta")
        title = str(meta.get("title", "")) if isinstance(meta, dict) else ""
        if "duration" in title.lower() and isinstance(raw_node["inputs"].get("value"), (int, float)):
            return {"node_id": str(node_id), "field": "value", "mode": "value", "unit": "seconds"}

    direct = _find_value_binding(workflow, _DURATION_FIELDS)
    if direct is not None:
        direct["unit"] = "seconds"
        return direct

    frames = _find_value_binding(workflow, _FRAME_FIELDS)
    if frames is None:
        return None
    node = _node(workflow, frames["node_id"])
    value = node["inputs"].get(frames["field"])
    if isinstance(value, (int, float)):
        frames.update({"unit": "frames", "fps": 24.0, "step": 1, "offset": 0})
        return frames
    return None


def _image_binding(
    workflow: Mapping[str, object],
    object_info: Mapping[str, object],
    target_node_id: str,
    field: str,
) -> dict[str, str] | None:
    target = _node(workflow, target_node_id)
    current = target["inputs"].get(field)
    if isinstance(current, list) and current and isinstance(current[0], str):
        source_id = current[0]
        source = workflow.get(source_id)
        if isinstance(source, dict) and isinstance(source.get("inputs"), dict) and "image" in source["inputs"]:
            return {"node_id": source_id, "field": "image", "mode": "loader"}

    class_type = str(target.get("class_type", ""))
    if field in _schema_inputs(object_info, class_type):
        return {"node_id": target_node_id, "field": field, "mode": "inject_loader"}
    return None


def detect_comfyui_endpoint_config(
    workflow_value: object,
    *,
    object_info: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Detect ArcReel bindings from a ComfyUI API-format workflow."""
    if not isinstance(workflow_value, dict) or not workflow_value:
        raise ComfyUIWorkflowConfigError("ComfyUI history entry has no API workflow")
    workflow: dict[str, Any] = copy.deepcopy(workflow_value)
    object_info = object_info or {}

    candidates = [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and _rank_prompt_node(node)[0] < 99
    ]
    if not candidates:
        raise ComfyUIWorkflowConfigError("workflow has no prompt input")
    prompt_node_id, prompt_node = min(candidates, key=lambda item: _rank_prompt_node(item[1]))
    prompt_inputs = prompt_node["inputs"]
    prompt_field = _first_input_field(prompt_inputs, _PROMPT_FIELDS)
    assert prompt_field is not None

    output_candidates = [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and "savevideo" in str(node.get("class_type", "")).replace("_", "").lower()
        and isinstance(node.get("inputs"), dict)
    ]
    if not output_candidates:
        raise ComfyUIWorkflowConfigError("workflow has no SaveVideo output")
    output_node_id, output_node = output_candidates[0]
    if "filename_prefix" not in output_node["inputs"]:
        raise ComfyUIWorkflowConfigError("SaveVideo node has no filename_prefix input")
    output_field = "filename_prefix"
    original_prefix = output_node["inputs"].get(output_field)

    bindings: dict[str, Any] = {
        "prompt": {"node_id": prompt_node_id, "field": prompt_field, "mode": "value"},
        "output": {"node_id": output_node_id, "field": output_field, "mode": "value"},
    }
    if seed := _find_value_binding(workflow, _SEED_FIELDS):
        bindings["seed"] = seed
    if duration := _find_duration_binding(workflow):
        bindings["duration"] = duration
    if aspect := _find_value_binding(workflow, ("aspect_ratio",)):
        aspect_node = _node(workflow, aspect["node_id"])
        options = _enum_options(object_info, str(aspect_node.get("class_type", "")), aspect["field"])
        if options:
            aspect["options"] = options
        bindings["aspect_ratio"] = aspect
    if start := _image_binding(workflow, object_info, prompt_node_id, "first_frame"):
        bindings["start_image"] = start
    if end := _image_binding(workflow, object_info, prompt_node_id, "last_frame"):
        bindings["end_image"] = end

    # Remove per-run values before persisting a reusable template.  Besides making every queued
    # run independent, this prevents a path from the discovery workstation leaking into ArcReel.
    prompt_node["inputs"][prompt_field] = ""
    if seed := bindings.get("seed"):
        _node(workflow, seed["node_id"])["inputs"][seed["field"]] = 0
    output_node["inputs"][output_field] = "arcreel/output"
    for image_name in ("start_image", "end_image"):
        if image_binding := bindings.get(image_name):
            if image_binding.get("mode") == "loader":
                _node(workflow, image_binding["node_id"])["inputs"][image_binding["field"]] = ""

    display_name = None
    if isinstance(original_prefix, str) and original_prefix:
        display_name = PurePosixPath(original_prefix.replace("\\", "/")).name or None
    if not display_name:
        class_name = str(prompt_node.get("class_type", "ComfyUI"))
        display_name = re.sub(r"(?<!^)(?=[A-Z])", " ", class_name).strip()

    duration_default = None
    if duration := bindings.get("duration"):
        raw_duration = _node(workflow, duration["node_id"])["inputs"].get(duration["field"])
        if duration.get("unit") == "seconds" and isinstance(raw_duration, (int, float)):
            duration_default = max(1, round(raw_duration))

    config: dict[str, Any] = {
        "version": COMFYUI_CONFIG_VERSION,
        "workflow": workflow,
        "bindings": bindings,
        "metadata": {
            "display_name": display_name,
            "duration_default_seconds": duration_default,
        },
    }
    return validate_comfyui_endpoint_config(config)


def workflow_profile_id(config: Mapping[str, object]) -> str:
    """Create a stable short model id from the sanitized workflow template."""
    # Presentation metadata (notably the previous SaveVideo prefix) must not turn identical
    # executions into duplicate profiles across history entries.
    identity = {key: config.get(key) for key in ("version", "workflow", "bindings")}
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"comfy-{hashlib.sha256(payload).hexdigest()[:12]}"


def workflow_display_name(config: Mapping[str, object]) -> str:
    """Derive a readable profile name from the SaveVideo prefix and prompt node type."""
    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        stored_name = metadata.get("display_name")
        if isinstance(stored_name, str) and stored_name.strip():
            return stored_name.strip()
    workflow = config.get("workflow")
    output = _binding(config, "output", required=True)
    prompt = _binding(config, "prompt", required=True)
    assert isinstance(workflow, dict) and output is not None and prompt is not None
    output_node = _node(workflow, output["node_id"])
    prefix = output_node["inputs"].get(output["field"])
    prompt_node = _node(workflow, prompt["node_id"])
    class_name = str(prompt_node.get("class_type", "ComfyUI"))
    if isinstance(prefix, str) and prefix and prefix != "arcreel/output":
        name = PurePosixPath(prefix.replace("\\", "/")).name
        if name:
            return name
    return re.sub(r"(?<!^)(?=[A-Z])", " ", class_name).strip()


def apply_value_binding(workflow: dict[str, Any], binding: Mapping[str, object], value: object) -> None:
    node_id = str(binding["node_id"])
    field = str(binding["field"])
    _node(workflow, node_id)["inputs"][field] = value


def _aspect_value(aspect_ratio: str, options: object, current: object) -> str:
    if isinstance(options, list):
        normalized = aspect_ratio.strip().lower()
        for option in options:
            if isinstance(option, str) and option.strip().lower().startswith(normalized):
                return option
    if isinstance(current, str) and current.strip().lower().startswith(aspect_ratio.strip().lower()):
        return current
    return aspect_ratio


def bind_scalar_inputs(
    config: Mapping[str, object],
    *,
    prompt: str,
    duration_seconds: int,
    aspect_ratio: str,
    seed: int | None,
    output_prefix: str,
) -> dict[str, Any]:
    """Clone a template and bind all scalar ArcReel request values."""
    normalized = validate_comfyui_endpoint_config(config)
    workflow = normalized["workflow"]
    prompt_binding = _binding(normalized, "prompt", required=True)
    output_binding = _binding(normalized, "output", required=True)
    assert prompt_binding is not None and output_binding is not None
    apply_value_binding(workflow, prompt_binding, prompt)
    apply_value_binding(workflow, output_binding, output_prefix)

    if seed is not None and (seed_binding := _binding(normalized, "seed")):
        apply_value_binding(workflow, seed_binding, seed)
    if duration_binding := _binding(normalized, "duration"):
        if duration_binding.get("unit") == "frames":
            fps = float(duration_binding.get("fps", 24.0))
            step = max(1, int(duration_binding.get("step", 1)))
            offset = int(duration_binding.get("offset", 0))
            raw = max(offset, round(duration_seconds * fps))
            value = offset + math.ceil(max(0, raw - offset) / step) * step
        else:
            value = float(duration_seconds)
        apply_value_binding(workflow, duration_binding, value)
    if aspect_binding := _binding(normalized, "aspect_ratio"):
        node = _node(workflow, str(aspect_binding["node_id"]))
        current = node["inputs"].get(str(aspect_binding["field"]))
        value = _aspect_value(aspect_ratio, aspect_binding.get("options"), current)
        apply_value_binding(workflow, aspect_binding, value)
    return workflow


def bind_uploaded_image(
    workflow: dict[str, Any],
    binding: Mapping[str, object],
    uploaded_path: str,
    *,
    loader_id: str,
) -> None:
    """Bind an uploaded ComfyUI input file through an existing or injected LoadImage node."""
    mode = binding.get("mode")
    if mode == "loader":
        apply_value_binding(workflow, binding, uploaded_path)
        return
    if mode != "inject_loader":
        raise ComfyUIWorkflowConfigError(f"unsupported image binding mode: {mode!r}")
    target_id = str(binding["node_id"])
    field = str(binding["field"])
    unique_id = loader_id
    suffix = 2
    while unique_id in workflow:
        unique_id = f"{loader_id}_{suffix}"
        suffix += 1
    workflow[unique_id] = {
        "class_type": "LoadImage",
        "inputs": {"image": uploaded_path},
        "_meta": {"title": "ArcReel input image"},
    }
    _node(workflow, target_id)["inputs"][field] = [unique_id, 0]


def extract_video_output(history_record: object, output_node_id: str) -> dict[str, str]:
    """Extract a video file descriptor from a completed ComfyUI history record."""
    if not isinstance(history_record, dict):
        raise ComfyUIWorkflowConfigError("ComfyUI history record is malformed")
    outputs = history_record.get("outputs")
    if not isinstance(outputs, dict):
        raise ComfyUIWorkflowConfigError("ComfyUI job completed without outputs")
    roots: list[object] = []
    if output_node_id in outputs:
        roots.append(outputs[output_node_id])
    roots.extend(value for key, value in outputs.items() if key != output_node_id)

    def _walk(value: object):
        if isinstance(value, dict):
            filename = value.get("filename")
            if isinstance(filename, str) and PurePosixPath(filename).suffix.lower() in _VIDEO_EXTENSIONS:
                yield {
                    "filename": filename,
                    "subfolder": str(value.get("subfolder", "")),
                    "type": str(value.get("type", "output")),
                }
            for nested in value.values():
                yield from _walk(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from _walk(nested)

    for root in roots:
        if result := next(_walk(root), None):
            return result
    raise ComfyUIWorkflowConfigError("ComfyUI job completed without a video file")
