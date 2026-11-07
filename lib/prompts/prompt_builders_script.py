"""Script-generation prompt builders (drama / narration content_mode).

Design principles:
- Do not duplicate schema enums (shot_type / camera_motion, etc.); response_schema enforces them.
- Do not impose selection criteria for multi-select enums or human camera aesthetics on the LLM;
  let it choose based on visual content.
- Avoid word-count limits the LLM cannot self-check (such as ≤200 characters); examples imply pacing.
- Explain fields with a few positive examples and annotated counterexamples, not must/never lists.
- Render prompt authoring and narration splitting through builtin templates; project asset appearance/read-only content in code.
"""

from lib.infra.text_metrics import reading_unit_noun
from lib.project.asset_types import ALIASES_FIELD
from lib.prompts.prompt_rules.asset_appearance import asset_reference_names, iter_asset_appearances
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.speech.speech_rate import speech_rate_units_per_second


def format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """Describe duration constraints. Use a range for at least five consecutive integers; otherwise enumerate."""
    if not supported_durations:
        raise ValueError("supported_durations 不能为空：调用方必须提供 model 的合法时长列表")

    sorted_d = sorted(set(supported_durations))
    is_continuous = len(sorted_d) >= 5 and all(sorted_d[i] == sorted_d[i - 1] + 1 for i in range(1, len(sorted_d)))
    if is_continuous:
        body = f"choose any integer from {sorted_d[0]} to {sorted_d[-1]} seconds"
    else:
        durations_str = ", ".join(str(d) for d in sorted_d)
        body = f"choose from [{durations_str}] seconds"

    if default_duration is not None:
        if default_duration not in sorted_d:
            raise ValueError(
                f"default_duration={default_duration} 不在 supported_durations={sorted_d} 内，"
                "调用方必须保证默认值合法（否则 prompt 会自相矛盾）"
            )
        return f"Duration: {body}; default {default_duration} seconds"
    return f"Duration: {body}; choose according to content pacing"


def format_aspect_ratio_desc(aspect_ratio: str) -> str:
    if aspect_ratio == "9:16":
        return "portrait composition"
    if aspect_ratio == "16:9":
        return "landscape composition"
    return f"{aspect_ratio} composition"


def overview_slot(project_overview: dict) -> dict[str, object]:
    """Project overview into complete-key slots; missing keys become None, rendered empty by templates."""
    return {key: project_overview.get(key) for key in ("synopsis", "genre", "theme", "world_setting")}


def _stripped_text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def outline_slot(outline: object) -> dict | None:
    """Project an outline with complete keys; blank/nonstring fields count as missing. No valid content yields ``None`` and omits the block.

    Outlines contractually bound episode content: splitting must know where this episode ends and the next
    begins, avoiding cross-episode plot leakage and premature reveals.
    """
    if not isinstance(outline, dict):
        return None
    beats = outline.get("story_beats")
    projected = {
        "title": _stripped_text(outline.get("title")),
        "story_beats": [beat for beat in beats if isinstance(beat, str) and beat.strip()]
        if isinstance(beats, list)
        else [],
        "hook": _stripped_text(outline.get("hook")),
        "next_episode_teaser": _stripped_text(outline.get("next_episode_teaser")),
    }
    return projected if any(projected.values()) else None


# ---------------------------------------------------------------------------
# Two-stage wording (ADR 0041): script_plan (normalize) = content, prompt_authoring (drama) = visuals.
#
# Extract content earlier in script_plan: boundaries, appearing assets, verbatim utterances, source_text anchors,
# and scene_description are finalized once; source_kind selects adaptation versus extraction.
# prompt_authoring fills only image_prompt / video_prompt, passing content through by scene_id without speech recognition
# or source_kind branching; its wording therefore has no novel/screenplay variants.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def neutralize_tags(value: str) -> str:
    """Neutralize dynamic angle brackets: tag sequences such as </segments> in novel_text / asset names must not break
    tagged prompt blocks. This improves robustness, not a security boundary; response_schema still enforces prompt_authoring output.
    """
    return value.replace("<", "＜").replace(">", "＞")


def _format_narration_script_plan_segments(script_plan_segments: list[dict]) -> str:
    """Render structured script_plan segments as read-only prompt_authoring context: segment_id, content fields, and verbatim source.

    These fields are finalized in script_plan and passed through without regeneration; show them only as the basis for authoring visuals.
    """
    if not script_plan_segments:
        return "(no storyboards)"
    lines: list[str] = []
    for seg in script_plan_segments:
        sid = neutralize_tags(str(seg.get("segment_id", "?")))
        dur = seg.get("duration_seconds", "?")
        brk = ", scene change" if seg.get("segment_break") else ""
        chars = neutralize_tags("、".join(seg.get("characters_in_segment") or []) or "none")
        scene_names = neutralize_tags("、".join(seg.get("scenes") or []) or "none")
        prop_names = neutralize_tags("、".join(seg.get("props") or []) or "none")
        # Indent multiline novel_text into its source block to prevent flush-left leakage from <segments>; neutralize brackets too.
        novel_block = neutralize_tags(seg.get("novel_text") or "").replace("\n", "\n  ")
        lines.append(
            f"- {sid} (duration {dur}s{brk}) | Characters: {chars} | Scenes: {scene_names} | Props: {prop_names}\n  Source: {novel_block}"
        )
    return "\n".join(lines)


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    script_plan_segments: list[dict],
    episode: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """Build narration/commentary prompt_authoring (visual-layer) prompts.

    Pass finalized script_plan novel_text / duration / segment_break / characters / scenes / props through by segment_id.
    prompt_authoring produces only image_prompt and video_prompt. The ``<segments>`` block is read-only;
    the LLM never regenerates these fields, preventing novel_text drift through visual expansion.
    """
    return builtin_templates.render(
        "text/narration_prompt_authoring",
        project_overview=overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=format_aspect_ratio_desc(aspect_ratio),
        assets=project_asset_appearances(characters, scenes, props),
        segments_content=_format_narration_script_plan_segments(script_plan_segments),
        episode=episode,
        target_language=target_language,
        instructions=instructions,
    )


def render_drama_content_for_prompt_authoring(content_scenes: list) -> str:
    """Render finalized script_plan shot content as visual-generation input, one block per shot.

    Speech/source anchors only explain dramatic rhythm. ``build_drama_prompt`` declares once before
    ``<shots>`` that they must not enter visual fields; individual entries do not repeat it. The backend
    passes them through by scene_id (``ScriptGenerator._merge_visual_layer``); prompt_authoring produces only image_prompt / video_prompt.

    Embed results in prompt_authoring's ``<shots>`` block. Asset-name / utterances fields first check ``isinstance(_, list)``:
    degraded/manually edited plans may contain nonlists (strings iterate characterwise, numbers raise TypeError); treat them as empty.
    Filter list items with ``isinstance(_, str)``. All dynamic text uses ``neutralize_tags``, so verbatim
    source_text / utterances / scene_description containing ``<...>`` cannot break tagged blocks (same approach as narration
    ``_format_narration_script_plan_segments``). This function fails softly; upstream _load_drama_script_plan_content enforces structure loudly.
    """
    if not content_scenes:
        return "(no storyboard content)"
    blocks: list[str] = []
    for scene in content_scenes:
        if not isinstance(scene, dict):
            continue
        sid = neutralize_tags(str(scene.get("scene_id") or "?"))
        duration = scene.get("duration_seconds")
        header = f"### {sid}" + (f" (duration {duration} seconds)" if duration else "")
        lines = [header]
        raw_chars = scene.get("characters_in_scene")
        raw_scenes_ref = scene.get("scenes")
        raw_props_ref = scene.get("props")
        chars = [c for c in raw_chars if isinstance(c, str)] if isinstance(raw_chars, list) else []
        scenes_ref = [s for s in raw_scenes_ref if isinstance(s, str)] if isinstance(raw_scenes_ref, list) else []
        props_ref = [p for p in raw_props_ref if isinstance(p, str)] if isinstance(raw_props_ref, list) else []
        lines.append(
            f"Appearing assets: characters [{neutralize_tags(', '.join(chars) or 'none')}], "
            f"scenes [{neutralize_tags(', '.join(scenes_ref) or 'none')}], props [{neutralize_tags(', '.join(props_ref) or 'none')}]"
        )
        # Content confirmation passes visual adaptation into formal scripts; legacy scripts may omit it, so omit absent lines.
        raw_scene_desc = scene.get("scene_description")
        if raw_scene_desc:
            scene_desc = neutralize_tags(str(raw_scene_desc)).replace("\n", "\n  ")
            lines.append(f"Visual adaptation: {scene_desc}")
        raw_utterances = scene.get("utterances")
        utterances = raw_utterances if isinstance(raw_utterances, list) else []
        if utterances:
            lines.append("Speech:")
            for u in utterances:
                if not isinstance(u, dict):
                    continue
                text = neutralize_tags(str(u.get("text") or "")).replace("\n", "\n    ")
                if u.get("kind") == "dialogue":
                    speaker = neutralize_tags(str(u.get("speaker") or ""))
                    lines.append(f"  - [Dialogue] {speaker}: {text}")
                else:
                    lines.append(f"  - [Voiceover] {text}")
        source_text = scene.get("source_text")
        if source_text:
            source_block = neutralize_tags(str(source_text)).replace("\n", "\n  ")
            lines.append(f"Source anchor: {source_block}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _asset_aliases(bucket: dict | None, name: str) -> list[str]:
    """Asset-entry aliases; derivative reference names are not table keys and have no aliases."""
    entry = (bucket or {}).get(name)
    aliases = entry.get(ALIASES_FIELD) if isinstance(entry, dict) else None
    if not isinstance(aliases, list):
        return []
    return [neutralize_tags(alias) for alias in aliases if isinstance(alias, str) and alias.strip()]


def project_asset_appearances(characters: dict | None, scenes: dict | None, props: dict | None) -> dict:
    """Project appearance, aliases, and reference names with complete keys; neutralize dynamic angle brackets for template data."""
    return {
        key: [
            {
                "name": neutralize_tags(name),
                "aliases": _asset_aliases(bucket, name),
                "appearance": neutralize_tags(appearance) or None,
            }
            for name, appearance in iter_asset_appearances(asset_type, bucket)
        ]
        for key, asset_type, bucket in (
            ("characters", "character", characters),
            ("scenes", "scene", scenes),
            ("props", "prop", props),
        )
    }


def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    scenes_content: str,
    episode: int,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
    characters: dict | None = None,
    scenes: dict | None = None,
    props: dict | None = None,
    instructions: str | None = None,
) -> str:
    """Build drama prompt_authoring (visual-layer) prompts.

    Content extraction happens earlier in script_plan (ADR 0041): boundaries, appearing assets, verbatim speech utterances,
    source_text anchors, and visual adaptation descriptions are final. ``scenes_content`` is their rendered input
    (``render_drama_content_for_prompt_authoring``). prompt_authoring produces only image_prompt / video_prompt;
    backend merging aligns LLM output by scene_id to script_plan. It no longer branches on source_kind, recognizes speech,
    or annotates assets/durations; those belong to script_plan.

    ``characters`` / ``scenes`` / ``props`` inject appearance descriptions from project.json buckets,
    informing clothes/material/furnishing details. If all three are None, omit the asset block.
    """
    return builtin_templates.render(
        "text/drama_prompt_authoring",
        project_overview=overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=format_aspect_ratio_desc(aspect_ratio),
        assets=(
            project_asset_appearances(characters, scenes, props)
            if characters is not None or scenes is not None or props is not None
            else None
        ),
        scenes_content=scenes_content,
        episode=episode,
        target_language=target_language,
        instructions=instructions,
    )


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
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
    """Normalize source into structured shot content: utterances + source_text + visual adaptation descriptions.

    Consumed by the drama ``generate_script_plan`` variant, with wording in ``text/drama_script_plan``.
    response_schema (``DramaNormalizedScript``) constrains output JSON. Any ``source_kind`` other than ``"screenplay"``
    renders as ``"novel"``.

    ``source_language`` selects speech rate and reading-unit nouns for the minimum-duration sentence
    (single source ``lib.speech.speech_rate``); nonstrings use default rate. ``speech_rate_override`` overrides project rate;
    ``None`` uses language defaults. Omit corresponding sections when ``episode_target_duration`` / ``episode_outline`` /
    ``next_episode_outline`` / ``instructions`` are ``None`` or empty.
    """
    # Empty sets or nonmember defaults produce contradictory prompts; fail before generation for diagnosis.
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")

    # source_language may be a corrupted nonstring from project.json, which would break downstream .strip().
    source_language = source_language if isinstance(source_language, str) else None
    return builtin_templates.render(
        "text/drama_script_plan",
        source_kind="screenplay" if source_kind == "screenplay" else "novel",
        target_language=target_language,
        project_overview=overview_slot(project_overview),
        style=style,
        assets=project_asset_appearances(characters, scenes, props),
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        novel_text=novel_text,
        episode=episode,
        durations=", ".join(str(d) for d in normalized_durations),
        max_duration=normalized_durations[-1],
        default_duration=default_duration,
        speech_rate=f"{speech_rate_units_per_second(source_language, speech_rate_override):g}",
        speech_unit=reading_unit_noun(source_language),
        episode_target_duration=episode_target_duration,
        episode_outline=outline_slot(episode_outline),
        next_episode_outline=outline_slot(next_episode_outline),
        instructions=instructions or None,
    )


def build_narration_split_prompt(
    *,
    novel_text: str,
    project_overview: dict,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
    episode: int,
    target_language: str = "中文",
    episode_target_duration: int | None = None,
    instructions: str | None = None,
) -> str:
    """Split source into structured narration/commentary storyboards: verbatim novel_text + duration + asset registration.

    Consumed by narration ``generate_script_plan``. response_schema (``NarrationScriptPlanDraft``) constrains JSON;
    ``novel_text`` preserves source verbatim as voiceover/pass-through truth. Later prompt_authoring fills visuals by
    ``segment_id``. The tool post-validates duration membership in ``supported_durations`` because static
    ``NarrationScriptPlanSegment.duration_seconds`` uses the ``ge=1, le=60`` range, not schema enum constraints
    (reusing the existing storyboard schema).

    ``default_duration`` is a per-shot seconds preference. Unlike ``build_normalize_prompt``, defaults drifting outside
    ``supported_durations`` become None (a soft preference overridable by content), rather than failing loudly;
    consistent with split-narration-segments treating nonmember defaults as null.

    ``episode_target_duration`` is the project's episode target seconds, guiding shot count;
    ``None`` omits it. It and ``default_duration`` are soft preferences at different scales.
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        default_duration = None

    return builtin_templates.render(
        "text/narration_script_plan",
        project_overview=overview_slot(project_overview),
        assets=project_asset_appearances(characters, scenes, props),
        novel_text=novel_text,
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        durations=", ".join(str(d) for d in normalized_durations),
        max_duration=normalized_durations[-1],
        default_duration=default_duration,
        episode_target_duration=episode_target_duration,
        episode=episode,
        target_language=target_language,
        instructions=instructions,
    )


def build_overview_prompt(source_content: str, source_kind: str = "novel", target_language: str = "中文") -> str:
    """Build project overview prompts from builtin ``text/source_overview``.

    Any ``source_kind`` other than ``"screenplay"`` (including missing/invalid) renders as ``"novel"``. Overview fields enter
    all downstream generation prompts; output language must follow the other builders (callers resolve target_language
    from project.json source_language).
    """
    return builtin_templates.render(
        "text/source_overview",
        source_kind="screenplay" if source_kind == "screenplay" else "novel",
        target_language=target_language,
        source_content=source_content,
    )
