"""参考生视频草稿的最终提示词，使用当前请求投影的实发参考图与能力。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib.generation.batch_admission import DURATION_CONFIRMATION_CODE
from lib.script.reference_video.prompt_render import render_video_unit_prompt, resolve_reference_audio_paths
from lib.script.reference_video.request_projection import ReferenceUnitRequestProjection
from lib.script.reference_video.voice_settings import VoiceRenderSettings


def render_reference_prompt_preview(
    *,
    project: dict,
    unit: dict,
    project_path: Path,
    projection: ReferenceUnitRequestProjection,
    translate: Callable[..., str],
) -> dict[str, Any]:
    """只读渲染；时长待确认不妨碍查看文本，其他投影阻断项显式显示为不可用。

    与分镜预览同一口径：``text`` 与 ``unavailable`` 恰有一个非 ``None``，``warnings`` 只随文本出现。
    """
    result: dict[str, Any] = {
        "text": None,
        "unavailable": None,
        "is_text_form": True,
        "references": [],
        "warnings": [],
    }
    blockers = [problem for problem in projection.blocking_problems if problem.code != DURATION_CONFIRMATION_CODE]
    if blockers:
        result["unavailable"] = translate(blockers[0].code, **blockers[0].parameters())
        return result
    request_facts = projection.request_facts
    if request_facts is None:
        result["unavailable"] = translate(
            "reference_capability_unavailable", capability=projection.hydrated_generation_type
        )
        return result
    if not str(unit.get("text") or "").strip():
        result["unavailable"] = translate("reference_prompt_preview_missing")
        return result

    rendered = render_video_unit_prompt(
        unit,
        project,
        VoiceRenderSettings(
            voice_consistency=request_facts.voice_consistency,
            requested_generate_audio=request_facts.requested_generate_audio,
            max_reference_audio=request_facts.max_reference_audio_count,
            model_id=request_facts.model_id,
            audio_ready=resolve_reference_audio_paths(project, project_path),
            requires_reference_image=request_facts.reference_audio_per_image,
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
        *(translate(problem.code, **problem.parameters()) for problem in projection.problems),
        *(translate(warning["key"], **warning["params"]) for warning in rendered.warnings),
    ]
    return result
