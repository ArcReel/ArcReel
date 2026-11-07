"""Final reference-to-video draft prompts using actual sent references/capabilities from the current request projection."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib.generation.batch_admission import DURATION_CONFIRMATION_CODE
from lib.i18n import render_message
from lib.i18n.display_names import DisplayNames
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
    names: DisplayNames,
) -> dict[str, Any]:
    """Read-only rendering: pending duration confirmation does not block viewing text; other projection blockers explicitly render unavailable.

    Match storyboard previews: exactly one of ``text`` and ``unavailable`` is non-``None``; ``warnings`` accompanies text only.
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
        result["unavailable"] = render_message(blockers[0].code, blockers[0].parameters(), translate, names)
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
        *(render_message(problem.code, problem.parameters(), translate, names) for problem in projection.problems),
        *(render_message(warning["key"], warning["params"], translate, names) for warning in rendered.warnings),
    ]
    return result
