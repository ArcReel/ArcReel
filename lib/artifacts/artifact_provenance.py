"""Canonical direct-input bases for structured content artifacts.

These builders intentionally accept the full project mapping but project only formal
content semantics. Execution configuration does not participate in the currency of an
existing structured artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from lib.artifacts.artifact_manifest import ArtifactBasis
from lib.episode.episode_ledger import episode_outline_context
from lib.episode.episode_target_duration import project_episode_target_duration
from lib.episode.source_kinds import DEFAULT_SOURCE_KIND, episode_source_kind
from lib.infra.text_metrics import reading_unit_noun
from lib.infra.text_utils import normalize_newlines
from lib.speech.speech_rate import project_speech_rate_override, speech_rate_units_per_second

_STRUCTURED_CONTENT_MODES = frozenset({"narration", "drama"})
_GENERATION_MODES = frozenset({"storyboard", "reference_video"})
_DEFAULT_SOURCE_LANGUAGE = "中文"
_AD_OVERVIEW_FIELDS = ("synopsis", "genre", "theme")

ScriptPlanPromptVariant = Literal["drama", "narration", "reference_video"]


def decode_script_plan_source(raw: bytes) -> str:
    """Decode episode source bytes into the text the script_plan tools consume.

    Script_plan generation reads the source through ``Path.read_text`` and the
    basis freezes that text, so canonical basis reconstruction must apply the
    same universal-newline translation: ``\\r\\n`` and lone ``\\r`` both become
    ``\\n``.  Otherwise a source written with platform line endings never
    matches its registered basis and the script_plan reads stale forever.
    """

    return normalize_newlines(raw.decode("utf-8"))


def build_script_plan_basis(
    source_content: object,
    *,
    episode: int,
    project: Mapping[str, object],
) -> ArtifactBasis:
    """Describe every durable prompt input consumed by one script_plan artifact."""

    content_mode, generation_mode = _content_axes(project)
    prompt_inputs = project_script_plan_prompt_inputs(episode, project=project)
    return _build_script_plan_basis(
        source_content,
        content_mode=content_mode,
        generation_mode=generation_mode,
        prompt_inputs=prompt_inputs,
    )


def build_script_plan_request(
    source_content: object,
    *,
    episode: int,
    project: Mapping[str, object],
    expected_variant: ScriptPlanPromptVariant,
) -> tuple[dict[str, object], ArtifactBasis]:
    """Freeze the prompt projection and basis for one route-specific script_plan request."""

    content_mode, generation_mode = _content_axes(project)
    prompt_inputs = project_script_plan_prompt_inputs(
        episode,
        project=project,
        expected_variant=expected_variant,
    )
    basis = _build_script_plan_basis(
        source_content,
        content_mode=content_mode,
        generation_mode=generation_mode,
        prompt_inputs=prompt_inputs,
    )
    return prompt_inputs, basis


#: Script-plan basis kind and its input key in script basis v2. Both enter digest computation; persisted
#: ``.arcreel_artifacts.json`` retains only digests, not inputs, so changed values cannot be recomputed: existing script plans
#: would all become stale and block downstream generation. **Persisted-format values compatible with manifest digests; never rename with terminology.** Script basis
#: v3 excludes script plans; the input key serves only v14→v15 recomputation of existing entries.
SCRIPT_PLAN_BASIS_KIND = "structured-content/step1"
SCRIPT_PLAN_BASIS_INPUT_KEY = "step1_content"

#: Non-ad script-basis kind. Also persisted format: v14→v15 recognizes existing entries by its historical versions.
EPISODE_SCRIPT_BASIS_KIND = "structured-content/episode-script"


def _build_script_plan_basis(
    source_content: object,
    *,
    content_mode: str,
    generation_mode: str,
    prompt_inputs: Mapping[str, object],
) -> ArtifactBasis:
    return ArtifactBasis.build(
        SCRIPT_PLAN_BASIS_KIND,
        kind_version=2,
        inputs={
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "source_content": source_content,
            "prompt_context": _freeze_script_plan_prompt_inputs(prompt_inputs, generation_mode=generation_mode),
        },
    )


def project_script_plan_prompt_inputs(
    episode: int,
    *,
    project: Mapping[str, object],
    expected_variant: ScriptPlanPromptVariant | None = None,
) -> dict[str, object]:
    """Project persisted fields passed to the selected script_plan prompt builder.

    Capability tiers and one-shot instructions are execution inputs and stay out
    of this projection. Asset mappings preserve insertion order because all
    three prompt builders render that order verbatim.
    """

    if type(episode) is not int or episode < 1:
        raise ValueError("episode must be a positive integer")
    content_mode, generation_mode = _content_axes(project)
    variant = _script_plan_prompt_variant(content_mode, generation_mode)
    if expected_variant is not None and variant != expected_variant:
        raise ValueError(
            f"{expected_variant} script_plan is unavailable for "
            f"content_mode={content_mode!r}, generation_mode={generation_mode!r}"
        )
    overview = _mapping_or_empty(project.get("overview"))
    source_language = project.get("source_language") or _DEFAULT_SOURCE_LANGUAGE
    if not isinstance(source_language, str):
        raise ValueError(f"source_language must be a non-empty string or null, got {source_language!r}")

    inputs: dict[str, object] = {
        "episode": episode,
        "project_overview": {field: overview.get(field, "") for field in (*_AD_OVERVIEW_FIELDS, "world_setting")},
        "characters": _project_script_plan_asset_mapping(project.get("characters")),
        "scenes": _project_script_plan_asset_mapping(project.get("scenes")),
        "props": _project_script_plan_asset_mapping(project.get("props")),
        "target_language": source_language,
        # Per-episode target duration is a persisted project preference shared by three script_plan variants. Set values enter basis;
        # changing them invalidates frozen output baselines because model splitting rules change. Unset is None,
        # removed from basis by _freeze_script_plan_prompt_inputs (prompts then match omission of the parameter).
        "episode_target_duration": project_episode_target_duration(project),
    }

    if variant in {"reference_video", "drama"}:
        episode_outline, next_episode_outline = episode_outline_context(project, episode)
        inputs.update(
            {
                "episode_outline": episode_outline,
                "next_episode_outline": next_episode_outline,
            }
        )

    if variant in {"reference_video", "drama"}:
        raw_source_language = project.get("source_language")
        inputs.update(
            {
                "source_language": raw_source_language if isinstance(raw_source_language, str) else None,
                "speech_rate_override": project_speech_rate_override(project),
            }
        )

    # Resolve source kind per episode (drama only); sourceless episodes use novel.
    source_kind = episode_source_kind(project, episode) or DEFAULT_SOURCE_KIND
    if variant == "drama":
        inputs.update(
            {
                "source_kind": source_kind,
                "style": _optional_string(project.get("style"), "style"),
            }
        )
    elif variant == "reference_video":
        # Novel prompts match type omission byte for byte and omit basis fields, preserving existing reference-to-video plan bases.
        # Screenplay prompts use the extraction branch, changing frozen baselines.
        inputs["source_kind"] = source_kind if source_kind != DEFAULT_SOURCE_KIND else None

    return inputs


def _freeze_script_plan_prompt_inputs(
    prompt_inputs: Mapping[str, object],
    *,
    generation_mode: str,
) -> dict[str, object]:
    """Convert runtime prompt mappings into an order-sensitive JSON basis."""

    frozen = dict(prompt_inputs)
    for field in ("characters", "scenes", "props"):
        raw_assets = _mapping_or_empty(prompt_inputs.get(field))
        if generation_mode == "reference_video":
            frozen[field] = [
                {
                    "name": name,
                    "description": (raw.get("description", "") if isinstance(raw, Mapping) else ""),
                }
                for name, raw in raw_assets.items()
            ]
        else:
            frozen[field] = list(raw_assets)

    if "source_language" in frozen:
        source_language = frozen.pop("source_language")
        source_language = source_language if isinstance(source_language, str) else None
        speech_rate_override = frozen.pop("speech_rate_override", None)
        speech_rate_override = speech_rate_override if isinstance(speech_rate_override, (int, float)) else None
        frozen["speech_rate_units_per_second"] = speech_rate_units_per_second(
            source_language,
            speech_rate_override,
        )
        frozen["reading_unit_noun"] = reading_unit_noun(source_language)

    if generation_mode == "reference_video":
        for field in ("episode_outline", "next_episode_outline"):
            frozen[field] = _freeze_reference_outline(prompt_inputs.get(field))

    # Unset episode target duration omits this basis key: prompts then match parameter omission byte for byte. Hashing None
    # would outdate both script plans and scripts in every existing project that never used this setting. Include it only when set,
    # when prompts really change and frozen baselines should invalidate.
    if frozen.get("episode_target_duration") is None:
        frozen.pop("episode_target_duration", None)
    if generation_mode == "reference_video" and frozen.get("source_kind") is None:
        frozen.pop("source_kind", None)
    return frozen


def _freeze_reference_outline(value: object) -> dict[str, object] | None:
    """Match the reference-video outline renderer's whitespace semantics."""

    if not isinstance(value, Mapping):
        return None
    result: dict[str, object] = {}
    for field in ("title", "hook", "next_episode_teaser"):
        raw = value.get(field)
        if isinstance(raw, str) and raw.strip():
            result[field] = raw.strip()
    raw_beats = value.get("story_beats")
    if isinstance(raw_beats, list):
        beats = [beat for beat in raw_beats if isinstance(beat, str) and beat.strip()]
        if beats:
            result["story_beats"] = beats
    return result or None


def _script_plan_prompt_variant(content_mode: str, generation_mode: str) -> ScriptPlanPromptVariant:
    if generation_mode == "reference_video":
        return "reference_video"
    if content_mode == "drama":
        return "drama"
    return "narration"


def build_episode_script_basis(*, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the durable project inputs rendered into an episode script's prompt authoring.

    The formal script is the episode's own content truth once content review has
    materialized it, so the script_plan is not an input: rerunning the plan without
    confirming it leaves the script current.
    """

    content_mode, generation_mode = _content_axes(project)
    return ArtifactBasis.build(
        EPISODE_SCRIPT_BASIS_KIND,
        kind_version=3,
        inputs={
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "prompt_context": project_episode_script_prompt_inputs(project),
        },
    )


def project_episode_script_prompt_inputs(project: Mapping[str, object]) -> dict[str, object]:
    """Project the persisted project fields rendered into non-ad prompt_authoring prompts.

    Provider/model capabilities and one-shot user instructions are execution
    inputs, not durable project state. Asset order is retained because the
    prompt renders mappings in insertion order; changing that order changes the
    exact provider input even though JSON object equality would not.
    """

    content_mode, generation_mode = _content_axes(project)
    overview = _mapping_or_empty(project.get("overview"))
    aspect_ratio = project.get("aspect_ratio")
    if not isinstance(aspect_ratio, str):
        aspect_ratio = "9:16" if content_mode == "narration" else "16:9"
    target_language = project.get("source_language") or _DEFAULT_SOURCE_LANGUAGE
    if not isinstance(target_language, str):
        raise ValueError("episode script source_language must be a string or null")

    return {
        "overview": {field: overview.get(field, "") for field in (*_AD_OVERVIEW_FIELDS, "world_setting")},
        "style": _optional_string(project.get("style"), "style"),
        "style_description": _optional_string(project.get("style_description"), "style_description"),
        "aspect_ratio": aspect_ratio,
        "target_language": target_language,
        "characters": _project_prompt_authoring_assets(project.get("characters"), generation_mode=generation_mode),
        "scenes": _project_prompt_authoring_assets(project.get("scenes"), generation_mode=generation_mode),
        "props": _project_prompt_authoring_assets(project.get("props"), generation_mode=generation_mode),
    }


def build_ad_episode_script_basis(episode: int, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the persisted business inputs consumed by ad script generation."""

    inputs = project_ad_episode_script_inputs(episode, project=project)
    return ArtifactBasis.build(
        "structured-content/ad-episode-script",
        kind_version=2,
        inputs=_freeze_ad_prompt_table_order(inputs),
    )


def project_ad_episode_script_inputs(
    episode: int,
    *,
    project: Mapping[str, object],
) -> dict[str, object]:
    """Project exactly the durable ad inputs shared by prompt and provenance."""

    if type(episode) is not int or episode < 1:
        raise ValueError("episode must be a positive integer")
    if project.get("content_mode") != "ad":
        raise ValueError("ad episode script basis requires content_mode='ad'")
    generation_mode = project.get("generation_mode")
    if not isinstance(generation_mode, str) or generation_mode not in _GENERATION_MODES:
        raise ValueError(f"unsupported generation_mode: {generation_mode!r}")
    target_duration = project.get("target_duration")
    if type(target_duration) is not int or target_duration <= 0:
        raise ValueError("ad target_duration must be a positive integer")

    overview = _mapping_or_empty(project.get("overview"))
    overview_fields = (
        (*_AD_OVERVIEW_FIELDS, "world_setting") if generation_mode == "storyboard" else _AD_OVERVIEW_FIELDS
    )
    projected_overview = {field: overview.get(field, "") for field in overview_fields}
    target_language = project.get("source_language") or _DEFAULT_SOURCE_LANGUAGE
    if not isinstance(target_language, str):
        raise ValueError("ad source_language must be a string or null")

    inputs: dict[str, object] = {
        "content_mode": "ad",
        "generation_mode": generation_mode,
        "episode": episode,
        "overview": projected_overview,
        "style": _optional_string(project.get("style"), "style"),
        "style_description": _optional_string(project.get("style_description"), "style_description"),
        "characters": _project_named_assets(project.get("characters")),
        "scenes": _project_named_assets(project.get("scenes")),
        "props": _project_named_assets(project.get("props")),
        "products": _project_products(project.get("products")),
        "brief": _optional_string(project.get("brief"), "brief"),
        "target_duration": target_duration,
        "aspect_ratio": project.get("aspect_ratio") if isinstance(project.get("aspect_ratio"), str) else "9:16",
        "target_language": target_language,
    }
    if generation_mode == "storyboard":
        inputs["speech_rate_override"] = project_speech_rate_override(project)
    return inputs


def _mapping_or_empty(value: object) -> Mapping[object, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_string(value: object, field: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"ad {field} must be a string or null")
    return value


def _freeze_ad_prompt_table_order(inputs: Mapping[str, object]) -> dict[str, object]:
    """Encode prompt-rendered mapping order explicitly for canonical JSON."""

    frozen = dict(inputs)
    for field in ("characters", "scenes", "props", "products"):
        frozen[field] = [{"name": name, "value": value} for name, value in _mapping_or_empty(inputs.get(field)).items()]
    return frozen


def _project_named_assets(value: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in _mapping_or_empty(value):
        if not isinstance(name, str):
            raise ValueError("ad asset names must be strings")
        result[name] = {}
    return result


def _project_script_plan_asset_mapping(value: object) -> dict[str, object]:
    """Copy the ordered asset table rendered by script_plan prompt builders."""

    result: dict[str, object] = {}
    for name, raw in _mapping_or_empty(value).items():
        if not isinstance(name, str):
            raise ValueError("script_plan asset names must be strings")
        result[name] = dict(raw) if isinstance(raw, Mapping) else {}
    return result


def _project_prompt_authoring_assets(value: object, *, generation_mode: str) -> list[dict[str, object]]:
    """Project the ordered name/description pairs rendered by prompt_authoring builders."""

    result: list[dict[str, object]] = []
    for raw_name, raw in _mapping_or_empty(value).items():
        if not isinstance(raw_name, str):
            raise ValueError("episode script asset names must be strings")
        data = raw if isinstance(raw, Mapping) else {}
        description = data.get("description")
        if generation_mode == "reference_video":
            rendered_description: object = description if "description" in data else ""
        else:
            rendered_description = description.strip() if isinstance(description, str) and description.strip() else ""
        result.append({"name": raw_name, "description": rendered_description})
    return result


def _project_products(value: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, raw in _mapping_or_empty(value).items():
        if not isinstance(name, str):
            raise ValueError("ad product names must be strings")
        data = raw if isinstance(raw, Mapping) else {}
        product: dict[str, object] = {}
        for field in ("brand", "description"):
            item = data.get(field)
            if item:
                if not isinstance(item, str):
                    raise ValueError(f"ad product {field} must be a string")
                product[field] = item
        raw_points = data.get("selling_points")
        if isinstance(raw_points, list):
            points = [point for point in raw_points if isinstance(point, str) and point.strip()]
            if points:
                product["selling_points"] = points
        result[name] = product
    return result


def _content_axes(project: Mapping[str, object]) -> tuple[str, str]:
    content_mode = project.get("content_mode")
    if not isinstance(content_mode, str) or content_mode not in _STRUCTURED_CONTENT_MODES:
        raise ValueError(f"structured content basis does not support content_mode: {content_mode!r}")
    generation_mode = project.get("generation_mode")
    if not isinstance(generation_mode, str) or generation_mode not in _GENERATION_MODES:
        raise ValueError(f"unsupported generation_mode: {generation_mode!r}")
    return content_mode, generation_mode
