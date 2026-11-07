"""Reference-to-video prompt builders.

Design principles match prompt_builders_script.py:
- Do not repeat schema enums (type, etc.); response_schema enforces them.
- Do not specify selection criteria for multi-select enums; let the model choose from visual content.
- Give field guidance and examples, not must/never lists.
- Explicitly inject backend duration/reference limits as parameters, never hardcode seconds in text.

Both prompt levels render through builtin templates and share one reference-syntax partial; this module
projects asset appearances, outlines, tiers, and unit lists into slots.
"""

from __future__ import annotations

from lib.infra.text_metrics import reading_unit_noun
from lib.prompts.prompt_builders_script import outline_slot, overview_slot, project_asset_appearances
from lib.prompts.prompt_rules.asset_appearance import asset_reference_names
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.speech.speech_rate import speech_rate_units_per_second


def _candidate_names(characters: dict | None, scenes: dict | None, props: dict | None) -> dict:
    return {
        "character_names": asset_reference_names("character", characters),
        "scene_names": asset_reference_names("scene", scenes),
        "prop_names": asset_reference_names("prop", props),
    }


def _join_durations(durations: list[int]) -> str:
    return ", ".join(str(d) for d in durations)


def build_reference_units_split_prompt(
    *,
    novel_text: str,
    project_overview: dict,
    characters: dict,
    scenes: dict,
    props: dict,
    supported_durations: list[int],
    reference_supported_durations: list[int] | None = None,
    text_supported_durations: list[int] | None = None,
    max_duration: int,
    max_reference_images: int | None,
    default_duration: int | None,
    episode: int,
    source_kind: str = "novel",
    target_language: str = "中文",
    source_language: str | None = None,
    speech_rate_override: float | None = None,
    episode_target_duration: int | None = None,
    episode_outline: dict | None = None,
    next_episode_outline: dict | None = None,
    instructions: str | None = None,
) -> str:
    """Step-1 video_unit split prompt: source → flat units (duration + source anchor + reference-syntax body).

    Consumed by reference-to-video ``generate_script_plan``. script_plan sets the **structure/content contract**:
    boundaries, durations (billing units), dialogue placement, and core asset identification; prompt_authoring
    later handles shot scale/composition/camera movement. response_schema (``build_reference_units_script_plan_model``,
    with hard duration enums) constrains output; unit_id / utterances are machine-derived and never in LLM output.

    Args:
        supported_durations: allowed unit seconds, union of both reference-state tier sets,
            matching response_schema enums.
        reference_supported_durations: tiers for units with ``@`` references.
        text_supported_durations: tiers for units without ``@`` references. If sets match (or either is
            None), omit this interaction constraint; most models declare no reference↔duration interaction,
            so redundant constraints waste attention. When different, **write both**, assuming neither contains the other.
        max_duration: maximum seconds per generation, the greatest tier.
        max_reference_images: per-unit reference limit; None omits hard count constraints.
        default_duration: project default seconds preference; a supported_durations member or None.
        source_kind: episode source type; ``"screenplay"`` extracts existing scenes and verbatim dialogue,
            while every other value renders as ``"novel"``.
        source_language: project source code (zh / en / vi / ru or None), selecting speech rate for dialogue-duration lower bounds.
        speech_rate_override: project reading-units/second override, resolved by callers through
            ``project_speech_rate_override``; None uses language defaults.
        episode_target_duration: project episode target seconds, resolved by callers through
            ``project_episode_target_duration``. Organize packing efficiency around this target when set;
            otherwise (None), use the generation-call maximum, matching projects without this setting.
        episode_outline / next_episode_outline: ledger outlines (``episode_outline_context`` return),
            bounding episode content; None omits this section.
        instructions: additional instruction body; empty / None omits the section.
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")
    normalized_reference_durations = sorted({int(d) for d in reference_supported_durations or []})
    normalized_text_durations = sorted({int(d) for d in text_supported_durations or []})
    for name, tier in (
        ("reference_supported_durations", normalized_reference_durations),
        ("text_supported_durations", normalized_text_durations),
    ):
        if not set(tier) <= set(normalized_durations):
            raise ValueError(f"{name}={tier} 不是 supported_durations={normalized_durations} 的子集")

    # Include reference↔duration interaction only when declared and tier sets differ. Most models use
    # equivalent sets, so redundant rules waste attention. Always state both sets, not union plus image narrowing:
    # buckets can use different models and reference tiers may be broader, restricting unreferenced units instead.
    # Describing references only would let unreferenced units choose unsupported tiers from the union.
    # Tools enforce this per unit using mechanically derived references; prompt guidance is not the only defense.
    duration_tiers = None
    if (
        normalized_reference_durations
        and normalized_text_durations
        and normalized_reference_durations != normalized_text_durations
    ):
        # Default preference is priority 3, optimized within priority 1 hard constraints, but may be valid
        # for only one reference state (Veo 3.1 at 720p allows only 8s with references, while default may be 4s).
        # State its scope to avoid planning reference units with unrequestable default durations.
        in_reference = default_duration is not None and int(default_duration) in normalized_reference_durations
        in_text = default_duration is not None and int(default_duration) in normalized_text_durations
        duration_tiers = {
            "with_references": _join_durations(normalized_reference_durations),
            "without_references": _join_durations(normalized_text_durations),
            "default_with_references_only": in_reference and not in_text,
            "default_without_references_only": in_text and not in_reference,
        }
    # Speech rate comes from lib.speech.speech_rate (project override, otherwise language default), never hardcoded.
    # Tool dialogue-overload validation uses the same conversion, giving prompt and validator the same scale.
    source_language = source_language if isinstance(source_language, str) else None
    speech_rate = speech_rate_units_per_second(source_language, speech_rate_override)

    return builtin_templates.render(
        "text/reference_video_script_plan",
        source_kind="screenplay" if source_kind == "screenplay" else "novel",
        target_language=target_language,
        project_overview=overview_slot(project_overview),
        assets=project_asset_appearances(characters, scenes, props),
        **_candidate_names(characters, scenes, props),
        novel_text=novel_text,
        episode_outline=outline_slot(episode_outline),
        next_episode_outline=outline_slot(next_episode_outline),
        episode=episode,
        durations=_join_durations(normalized_durations),
        duration_tiers=duration_tiers,
        default_duration=default_duration,
        speech_rate=f"{speech_rate:g}",
        speech_unit=reading_unit_noun(source_language),
        max_duration=max_duration,
        max_reference_images=max_reference_images,
        episode_target_duration=episode_target_duration,
        instructions=instructions,
    )


def render_reference_units_for_prompt_authoring(units: list[dict]) -> str:
    """Render script_plan units into prompt_authoring input text.

    Mechanical, without LLM involvement: emit index + duration + body in persisted unit order.
    This is prompt_authoring's sole basis for visual expansion (ADR 0041). ``unit_id`` is omitted because
    it derives mechanically from index; without generated IDs, there is no ID drift to validate.
    """
    blocks: list[str] = []
    for index, unit in enumerate(units, start=1):
        duration = int(unit.get("duration_seconds") or 0)
        body = str(unit.get("text") or "")
        blocks.append(f"#### unit {index} (duration {duration}s)\n{body}")
    return "\n\n".join(blocks)


def build_reference_video_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    script_plan_units: list[dict],
    max_refs: int | None,
    episode: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """Build the reference-to-video prompt_authoring LLM prompt.

    prompt_authoring only expands each script_plan unit body into visual detail using the same syntax,
    **preserving structure**: unit count/order and verbatim dialogue stay unchanged; durations are omitted
    from output (finalized in script_plan and passed through mechanically).

    Args:
        project_overview: project overview (synopsis, genre, theme, world_setting).
        style / style_description: visual style label and description.
        characters / scenes / props: three registered asset dictionaries for candidates.
        script_plan_units: structured units (validated dict list from ``script_plan_reference_units.json``),
            mechanically rendered by ``render_reference_units_for_prompt_authoring``.
        max_refs: current model reference-image maximum; None omits hard count constraints.
        instructions: additional instruction body; empty / None omits the section.
    """
    return builtin_templates.render(
        "text/reference_video_prompt_authoring",
        target_language=target_language,
        project_overview=overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        assets=project_asset_appearances(characters, scenes, props),
        **_candidate_names(characters, scenes, props),
        units_content=render_reference_units_for_prompt_authoring(script_plan_units),
        episode=episode,
        max_refs=max_refs,
        instructions=instructions,
    )
