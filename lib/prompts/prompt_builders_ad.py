"""Ad/short-film (content_mode=ad) script-generation prompt builders.

Storyboard-to-video produces flat ``shots[]``; reference-to-video directly produces self-contained
``video_units[]`` reference-syntax bodies in one stage. Both select the eight-section product-promotion
framework's pacing tier from target duration (15/30/60/90 seconds, maintainer-approved tables);
other integer durations proportionally adapt the nearest tier.
Empty ``products`` automatically selects general short-film prompts without promotion structure.

Both paths render whole builtin templates; this module supplies only slots: rounded tiers, per-shot duration
constraints, speech rates, product blocks, and candidates. Shared rules and four pacing tables are
``shared/ad_pacing`` partials, resolved as tier-named variants.

For existing formal scripts, prompt authoring fills pending shots/units: input contains the whole script
(marking pending entries, with others as context), producing only pending entries' visual layer or body.
"""

from collections.abc import Collection, Mapping

from lib.infra.schema_guards import is_int
from lib.infra.text_metrics import reading_unit_noun
from lib.prompts.prompt_builders_script import (
    format_aspect_ratio_desc,
    format_duration_constraint,
    neutralize_tags,
    overview_slot,
    project_asset_appearances,
)
from lib.prompts.prompt_rules.asset_appearance import asset_reference_names
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.script.script_models import REFERENCE_UNIT_DURATION_RANGE
from lib.speech.speech_rate import speech_rate_units_per_second

# ---------------------------------------------------------------------------
# Approved tiers
# ---------------------------------------------------------------------------

#: Four approved tiers in seconds. Other integers proportionally adapt the nearest tier;
#: ties favor the side closer to the recommended 30-second default.
AD_DURATION_TIERS: tuple[int, ...] = (15, 30, 60, 90)

#: Recommended default seconds, anchoring equidistant-tier tie-breaking.
AD_DEFAULT_TIER = 30


def nearest_ad_tier(target_duration: int) -> int:
    """Select the nearest approved tier; ties favor the side closer to recommended default 30 seconds."""
    return min(AD_DURATION_TIERS, key=lambda t: (abs(t - target_duration), abs(t - AD_DEFAULT_TIER)))


# ---------------------------------------------------------------------------
# Context block rendering
# ---------------------------------------------------------------------------


def _format_products(products: dict) -> str:
    """Render product blocks: name / brand / description / selling_points."""
    lines: list[str] = []
    for name, data in products.items():
        data = data if isinstance(data, dict) else {}
        lines.append(f"### {name}")
        brand = data.get("brand")
        if brand:
            lines.append(f"Brand: {brand}")
        desc = data.get("description")
        if desc:
            lines.append(f"Description: {desc}")
        # Accept string lists only: malformed values bypassing allowlists (such as whole strings) would iterate into character-sized points.
        raw_points = data.get("selling_points")
        points = [p for p in raw_points if isinstance(p, str) and p.strip()] if isinstance(raw_points, list) else []
        if points:
            lines.append("Selling points:")
            lines.extend(f"- {point}" for point in points)
        lines.append("")
    return "\n".join(lines).strip()


def _shot_duration_constraint(generation_mode: str | None, supported_durations: list[int] | None) -> str:
    """Render per-shot storyboard-to-video duration constraints; reference-to-video must use the self-contained unit builder."""
    if generation_mode == "reference_video":
        raise ValueError("reference_video 路径须使用 build_ad_reference_prompt")
    if not supported_durations:
        raise ValueError("storyboard 路径必须提供 supported_durations（视频模型的合法时长集合）")
    return format_duration_constraint(supported_durations, None)


def _asset_registry_slot(
    registry: Mapping[str, Mapping[str, object]] | None, characters: dict, scenes: dict, props: dict
) -> dict:
    """Asset-registry slot: include descriptions/aliases when available, otherwise candidate names only."""
    if registry is None:
        return project_asset_appearances(characters, scenes, props)
    return project_asset_appearances(
        dict(registry.get("characters") or {}), dict(registry.get("scenes") or {}), dict(registry.get("props") or {})
    )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _pacing_slots(target_duration: int) -> dict[str, object]:
    """Pacing tier axis and target seconds for adaptation notes; exact tier matches need no adaptation note."""
    if not is_int(target_duration, minimum=1):
        raise ValueError(f"target_duration 必须为正整数秒，当前为 {target_duration!r}")
    tier = nearest_ad_tier(target_duration)
    return {
        "target_duration": target_duration,
        "ad_duration_tier": str(tier),
        "off_tier_target_duration": None if tier == target_duration else target_duration,
    }


def build_ad_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    target_duration: int,
    generation_mode: str | None,
    supported_durations: list[int] | None,
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    speech_rate_override: float | None = None,
    instructions: str | None = None,
    asset_registry: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    """Build ad/short-film script-generation prompts.

    Nonempty ``products`` selects the eight-section promotion structure and approved pacing tables;
    empty selects general short films without an explicit submode toggle. ``speech_rate_override`` is
    project rate override (resolved by callers through ``project_speech_rate_override``); None uses language defaults.
    ``asset_registry`` contains project character/scene/prop descriptions and aliases (``characters`` / ``scenes`` /
    ``props``), enabling identity recognition and new-asset listing; omission renders candidate names only.
    """
    pacing = _pacing_slots(target_duration)
    duration_constraint = _shot_duration_constraint(generation_mode, supported_durations)
    # Speech-size→duration conversion uses lib.speech.speech_rate (same as drama lower bounds and subtitles):
    # project overrides take precedence, otherwise language defaults. Rates are keyed by zh / en / vi / ru;
    # target_language is free text (default Chinese), and unknown values fall back to zh rate. Reading-unit
    # nouns (characters / words) derive from reading_unit_noun using the same language source.
    speech_rate = speech_rate_units_per_second(target_language, speech_rate_override)
    return builtin_templates.render(
        "text/ad_storyboard_script",
        target_language=target_language,
        project_overview=overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        assets=_asset_registry_slot(asset_registry, characters, scenes, props),
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        products=_format_products(products) if products else None,
        product_names=list(products),
        duration_constraint=duration_constraint,
        speech_rate=f"{speech_rate:g}",
        unit_noun=reading_unit_noun(target_language),
        episode=episode,
        instructions=instructions or None,
        **pacing,
    )


def build_ad_reference_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    target_duration: int,
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
    asset_registry: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    """Single-stage ad/short-film reference-to-video prompt, producing flat units with reference-syntax bodies.

    ``asset_registry`` follows :func:`build_ad_prompt`.
    """
    pacing = _pacing_slots(target_duration)
    min_unit_duration, max_unit_duration = REFERENCE_UNIT_DURATION_RANGE
    return builtin_templates.render(
        "text/ad_reference_video_script",
        target_language=target_language,
        project_overview=overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        assets=_asset_registry_slot(asset_registry, characters, scenes, props),
        products=_format_products(products) if products else None,
        product_names=list(products),
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        min_unit_duration=min_unit_duration,
        max_unit_duration=max_unit_duration,
        episode=episode,
        instructions=instructions or None,
        **pacing,
    )


def _names(value: object) -> str:
    names = [name for name in value if isinstance(name, str)] if isinstance(value, list) else []
    return neutralize_tags("、".join(names) or "none")


def _prompt_summary(value: object, field: str) -> str:
    """One-sentence existing-prompt context: structured values use ``field``, text uses the original; completeness is not required."""
    if isinstance(value, Mapping):
        return str(value.get(field) or "")
    return value if isinstance(value, str) else ""


_PENDING_MARK = "【待编写】"


def render_ad_shots_for_prompt_authoring(shots: list[dict], target_ids: Collection[str]) -> str:
    """Render all ad shots in playback order: mark pending shots and show existing visual/action summaries for others as context."""
    blocks: list[str] = []
    for shot in shots:
        shot_id = str(shot.get("shot_id") or "?")
        pending = shot_id in target_ids
        header = f"### {neutralize_tags(shot_id)}{_PENDING_MARK if pending else ''} (duration {shot.get('duration_seconds', '?')} seconds)"
        lines = [
            header,
            f"Section: {neutralize_tags(str(shot.get('section') or 'none'))}",
            f"Appearing: characters [{_names(shot.get('characters_in_shot'))}], scenes [{_names(shot.get('scenes'))}], "
            f"props [{_names(shot.get('props'))}], products [{_names(shot.get('products_in_shot'))}]",
        ]
        voiceover = str(shot.get("voiceover_text") or "").strip()
        lines.append(f"Speech: {neutralize_tags(voiceover) if voiceover else '(none)'}")
        if not pending:
            scene = _prompt_summary(shot.get("image_prompt"), "scene").strip()
            action = _prompt_summary(shot.get("video_prompt"), "action").strip()
            if scene:
                lines.append(f"Existing visuals: {neutralize_tags(scene)}")
            if action:
                lines.append(f"Existing action: {neutralize_tags(action)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "(no storyboards)"


def render_ad_units_for_prompt_authoring(units: list[dict], target_ids: Collection[str]) -> str:
    """Render all ad units in playback order: mark pending units; manually added units may have empty bodies."""
    blocks: list[str] = []
    for index, unit in enumerate(units, start=1):
        pending = str(unit.get("unit_id") or "") in target_ids
        body = str(unit.get("text") or "").strip()
        header = (
            f"#### unit {index}{_PENDING_MARK if pending else ''} (duration {int(unit.get('duration_seconds') or 0)}s)"
        )
        blocks.append(f"{header}\n{body or '(empty body)'}")
    return "\n\n".join(blocks) or "(no units)"


def build_ad_shot_prompt_authoring_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    shots: list[dict],
    target_ids: Collection[str],
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """Ad shot-authoring prompt: produce image_prompt / video_prompt only for shots in ``target_ids``."""
    return builtin_templates.render(
        "text/ad_storyboard_prompt_authoring",
        target_language=target_language,
        project_overview=overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        products=_format_products(products) if products else None,
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        shots_content=render_ad_shots_for_prompt_authoring(shots, target_ids),
        episode=episode,
        instructions=instructions or None,
    )


def build_ad_reference_prompt_authoring_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    units: list[dict],
    target_ids: Collection[str],
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """Ad reference-unit authoring prompt: produce bodies only for ``target_ids``, in playback order."""
    return builtin_templates.render(
        "text/ad_reference_video_prompt_authoring",
        target_language=target_language,
        project_overview=overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        products=_format_products(products) if products else None,
        product_names=list(products),
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        units_content=render_ad_units_for_prompt_authoring(units, target_ids),
        pending_count=sum(1 for unit in units if str(unit.get("unit_id") or "") in target_ids),
        episode=episode,
        instructions=instructions or None,
    )


__all__ = [
    "build_ad_prompt",
    "build_ad_reference_prompt",
    "build_ad_reference_prompt_authoring_prompt",
    "build_ad_shot_prompt_authoring_prompt",
    "nearest_ad_tier",
]
