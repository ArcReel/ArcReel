"""Derived Phase 1 QA findings for short-drama step1 review.

The QA result is intentionally stateless: callers derive it from project metadata
and the current step1 content on read/confirm.  It does not mutate project.json
and does not replace Pydantic structure validation.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

Severity = Literal["info", "warn", "block"]
GateStatus = Literal["clear", "warning", "blocked"]


class QAFinding(TypedDict):
    code: str
    severity: Severity
    message: str
    path: NotRequired[str]
    evidence: NotRequired[str]
    recommendation: NotRequired[str]


class QASummary(TypedDict):
    info_count: int
    warn_count: int
    block_count: int
    gate_status: GateStatus
    top_codes: list[str]


class QAResult(TypedDict):
    qa_findings: list[QAFinding]
    qa_summary: QASummary
    qa_gate_status: GateStatus


_ASSET_FIELDS = {
    "characters": ("characters_in_scene", "characters_in_segment", "character_sheet"),
    "scenes": ("scenes", "scene_sheet"),
    "props": ("props", "prop_sheet"),
}
_HOOK_TOKENS = ("？", "?", "！", "!", "危机", "秘密", "突然", "反转", "真相", "失控", "血", "跪", "逃")
_CLIFFHANGER_TOKENS = ("？", "?", "！", "!", "真相", "秘密", "门开", "出现", "回头", "电话", "证据", "身份")


def empty_result() -> QAResult:
    return _build_result([])


def evaluate_short_drama_qa(project: dict[str, Any], step1_content: dict[str, Any] | None) -> QAResult:
    """Return derived QA findings for Phase 1.

    Inputs are restricted to project metadata and step1 content.  Optional script
    or storyboard artifacts are deliberately excluded from this phase.
    """
    if not isinstance(step1_content, dict):
        return empty_result()

    content_mode = project.get("content_mode")
    if content_mode == "drama":
        items = _list_items(step1_content.get("scenes"))
        id_key = "scene_id"
        text_keys = ("scene_description", "source_text")
    elif content_mode == "narration":
        items = _list_items(step1_content.get("segments"))
        id_key = "segment_id"
        text_keys = ("novel_text",)
    else:
        return empty_result()

    findings: list[QAFinding] = []
    findings.extend(_asset_findings(project, items, id_key))
    findings.extend(_duration_findings(project, items, id_key))
    if content_mode == "drama":
        findings.extend(_empty_drama_visual_findings(items))
    findings.extend(_creative_warn_findings(items, id_key, text_keys))
    return _build_result(findings)


def has_blocking_findings(result: QAResult) -> bool:
    return result["qa_summary"]["block_count"] > 0


def _asset_findings(project: dict[str, Any], items: list[Any], id_key: str) -> list[QAFinding]:
    findings: list[QAFinding] = []
    registered = {bucket: _registered_assets(project.get(bucket)) for bucket in _ASSET_FIELDS}
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_id = _item_id(item, id_key, idx)
        for bucket, config in _ASSET_FIELDS.items():
            field_names = config[:-1]
            sheet_field = config[-1]
            field_name = next((field for field in field_names if field in item), field_names[0])
            refs = item.get(field_name) or []
            if not isinstance(refs, list):
                continue
            string_refs = _non_empty_strings(refs)
            missing = sorted({ref for ref in string_refs if ref not in registered[bucket]})
            if not missing:
                for ref in sorted(set(string_refs)):
                    asset = registered[bucket].get(ref)
                    sheet_value = asset.get(sheet_field) if isinstance(asset, dict) else None
                    if isinstance(asset, dict) and (not isinstance(sheet_value, str) or not sheet_value.strip()):
                        findings.append(
                            {
                                "code": f"missing_{bucket[:-1]}_sheet",
                                "severity": "warn",
                                "message": f"{item_id} 引用了尚未补齐设计图的 {bucket} 资产。",
                                "path": f"$.{bucket}.{ref}.{sheet_field}",
                                "evidence": ref,
                                "recommendation": "补齐该资产的设计图/参考图后再进入高保真视频生成。",
                            }
                        )
                continue
            findings.append(
                {
                    "code": f"missing_{bucket[:-1]}_reference",
                    "severity": "block",
                    "message": f"{item_id} 引用了未登记的 {bucket} 资产。",
                    "path": f"$.{_items_key(id_key)}[{idx}].{field_name}",
                    "evidence": ", ".join(missing),
                    "recommendation": "先在 project.json 中登记对应角色/场景/道具，或从 step1 引用中移除该名称。",
                }
            )
    return findings


def _duration_findings(project: dict[str, Any], items: list[Any], id_key: str) -> list[QAFinding]:
    supported = _supported_durations(project)
    if not supported:
        return []
    findings: list[QAFinding] = []
    allowed = set(supported)
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        duration = item.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration in allowed:
            continue
        item_id = _item_id(item, id_key, idx)
        findings.append(
            {
                "code": "unsupported_duration",
                "severity": "block",
                "message": f"{item_id} 使用了当前视频模型不支持的时长。",
                "path": f"$.{_items_key(id_key)}[{idx}].duration_seconds",
                "evidence": f"{duration} not in {supported}",
                "recommendation": "改为项目视频模型 supported_durations 中的秒数。",
            }
        )
    return findings


def _empty_drama_visual_findings(items: list[Any]) -> list[QAFinding]:
    findings: list[QAFinding] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if not _non_empty_string(item.get("scene_description")):
            sid = _item_id(item, "scene_id", idx)
            findings.append(
                {
                    "code": "empty_scene_description",
                    "severity": "block",
                    "message": f"{sid} 缺少视觉改编描述，step2 无法可靠生成视觉层。",
                    "path": f"$.scenes[{idx}].scene_description",
                    "evidence": "empty",
                    "recommendation": "补充该场可见的角色动作、环境、光线或氛围描述。",
                }
            )
    return findings


def _creative_warn_findings(items: list[Any], id_key: str, text_keys: tuple[str, ...]) -> list[QAFinding]:
    if not items:
        return []
    findings: list[QAFinding] = []
    first_text = _joined_text(items[0], text_keys)
    first_id = _item_id(items[0], id_key, 0)
    if first_text and not any(token in first_text for token in _HOOK_TOKENS):
        findings.append(
            {
                "code": "weak_opening_hook",
                "severity": "warn",
                "message": f"{first_id} 开篇钩子信号偏弱。",
                "path": f"$.{_items_key(id_key)}[0]",
                "evidence": first_text[:80],
                "recommendation": "考虑在开头加入危机、反差、秘密、强情绪或失控动作。",
            }
        )
    last_idx = len(items) - 1
    last_text = _joined_text(items[last_idx], text_keys)
    last_id = _item_id(items[last_idx], id_key, last_idx)
    if last_text and not any(token in last_text for token in _CLIFFHANGER_TOKENS):
        findings.append(
            {
                "code": "weak_cliffhanger",
                "severity": "warn",
                "message": f"{last_id} 结尾追更钩子偏弱。",
                "path": f"$.{_items_key(id_key)}[{last_idx}]",
                "evidence": last_text[:80],
                "recommendation": "考虑让末镜停在身份揭露、证据出现、关系反转或未完成动作上。",
            }
        )
    return findings


def _build_result(findings: list[QAFinding]) -> QAResult:
    info_count = sum(1 for f in findings if f.get("severity") == "info")
    warn_count = sum(1 for f in findings if f.get("severity") == "warn")
    block_count = sum(1 for f in findings if f.get("severity") == "block")
    gate_status: GateStatus = "blocked" if block_count else "warning" if warn_count or info_count else "clear"
    top_codes: list[str] = []
    for finding in sorted(findings, key=lambda f: _severity_rank(f.get("severity"))):
        code = finding.get("code")
        if code and code not in top_codes:
            top_codes.append(code)
        if len(top_codes) >= 5:
            break
    return {
        "qa_findings": findings,
        "qa_summary": {
            "info_count": info_count,
            "warn_count": warn_count,
            "block_count": block_count,
            "gate_status": gate_status,
            "top_codes": top_codes,
        },
        "qa_gate_status": gate_status,
    }


def _registered_assets(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, list):
        assets: dict[str, Any] = {}
        for item in value:
            if isinstance(item, dict) and item.get("name"):
                assets[str(item["name"])] = item
            elif isinstance(item, str):
                assets[item] = {}
        return assets
    return {}


def _list_items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _supported_durations(project: dict[str, Any]) -> list[int]:
    raw = project.get("_supported_durations") or project.get("supported_durations")
    if not isinstance(raw, list):
        return []
    durations: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if item > 0 and item not in durations:
            durations.append(item)
    return sorted(durations)


def _items_key(id_key: str) -> str:
    return "segments" if id_key == "segment_id" else "scenes"


def _item_id(item: Any, id_key: str, idx: int) -> str:
    if isinstance(item, dict):
        value = item.get(id_key)
        if isinstance(value, str) and value.strip():
            return value
    return f"#{idx}"


def _non_empty_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _non_empty_strings(values: list[Any]) -> list[str]:
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _joined_text(item: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(item, dict):
        return ""
    return " ".join(str(item.get(key) or "") for key in keys).strip()


def _severity_rank(severity: Severity | None) -> int:
    if severity is None:
        return 3
    ranks: dict[Severity, int] = {"block": 0, "warn": 1, "info": 2}
    return ranks.get(severity, 3)


__all__ = [
    "GateStatus",
    "QAFinding",
    "QAResult",
    "QASummary",
    "empty_result",
    "evaluate_short_drama_qa",
    "has_blocking_findings",
]
