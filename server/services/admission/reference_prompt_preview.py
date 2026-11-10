"""参考生视频单元的最终提示词，使用当前请求投影的实发参考图与能力。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib.generation.batch_admission import DURATION_CONFIRMATION_CODE
from lib.i18n import render_message
from lib.i18n.display_names import DisplayNames
from lib.script.reference_video.prompt_render import render_video_unit_prompt, resolve_reference_audio_paths
from lib.script.reference_video.request_projection import ReferenceUnitRequestProjection
from lib.script.reference_video.voice_settings import VoiceRenderSettings


def reference_prompt_preview_facts(
    *,
    project: dict,
    unit: dict,
    project_path: Path,
    projection: ReferenceUnitRequestProjection,
) -> dict[str, Any]:
    """只读渲染；时长待确认不妨碍查看文本，其他投影阻断项显式显示为不可用。

    与分镜预览同一口径：``text`` 与 ``unavailable`` 恰有一个非 ``None``，``warnings`` 只随文本出现。
    """
    result: dict[str, Any] = {
        "text": None,
        "unavailable": None,
        "unavailable_params": {},
        "is_text_form": True,
        "references": [],
        "warnings": [],
    }
    blockers = [problem for problem in projection.blocking_problems if problem.code != DURATION_CONFIRMATION_CODE]
    if blockers:
        result["unavailable"] = blockers[0].code
        result["unavailable_params"] = blockers[0].parameters()
        return result
    request_facts = projection.request_facts
    if request_facts is None:
        result["unavailable"] = "reference_capability_unavailable"
        result["unavailable_params"] = {"capability": projection.hydrated_generation_type}
        return result
    if not str(unit.get("text") or "").strip():
        result["unavailable"] = "reference_prompt_preview_missing"
        return result

    rendered = render_video_unit_prompt(
        unit,
        project,
        VoiceRenderSettings.from_request_facts(
            request_facts, audio_ready=resolve_reference_audio_paths(project, project_path)
        ),
        request_references=[asset.reference for asset in projection.request_assets],
    )
    result["text"] = rendered.prompt
    result["references"] = [
        {
            "type": asset.reference.type,
            "name": asset.reference.name,
            "path": asset.path.relative_to(project_path).as_posix(),
        }
        for asset in projection.request_assets
    ]
    result["warnings"] = [
        *({"key": problem.code, "params": problem.parameters()} for problem in projection.problems),
        *rendered.warnings,
    ]
    return result


def render_reference_prompt_preview(
    *,
    project: dict,
    unit: dict,
    project_path: Path,
    projection: ReferenceUnitRequestProjection,
    translate: Callable[..., str],
    names: DisplayNames,
) -> dict[str, Any]:
    """将共享预览的原因码与警告渲染为 Web 文案。"""
    result = reference_prompt_preview_facts(
        project=project,
        unit=unit,
        project_path=project_path,
        projection=projection,
    )
    params = result.pop("unavailable_params")
    if result["unavailable"] is not None:
        result["unavailable"] = render_message(result["unavailable"], params, translate, names)
    result["warnings"] = [
        render_message(warning["key"], warning["params"], translate, names) for warning in result["warnings"]
    ]
    return result
