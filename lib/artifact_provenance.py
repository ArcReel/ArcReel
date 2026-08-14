"""Canonical direct-input bases for structured content artifacts.

These builders intentionally accept the full project mapping but project only formal
content semantics. Execution configuration does not participate in the currency of an
existing structured artifact.
"""

from __future__ import annotations

from collections.abc import Mapping

from lib.artifact_manifest import ArtifactBasis
from lib.speech_rate import project_speech_rate_override

_STRUCTURED_CONTENT_MODES = frozenset({"narration", "drama"})
_GENERATION_MODES = frozenset({"storyboard", "reference_video"})
_SOURCE_KINDS = frozenset({"novel", "screenplay"})
_DEFAULT_SOURCE_LANGUAGE = "中文"
_AD_OVERVIEW_FIELDS = ("synopsis", "genre", "theme")


def build_step1_basis(source_content: object, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the formal source inputs consumed by one episode's step1 artifact."""

    content_mode, generation_mode = _content_axes(project)
    raw_source_kind = project.get("source_kind")
    source_kind = "novel" if raw_source_kind is None else raw_source_kind
    if not isinstance(source_kind, str) or source_kind not in _SOURCE_KINDS:
        raise ValueError(f"unsupported source_kind: {source_kind!r}")
    raw_source_language = project.get("source_language")
    source_language = raw_source_language or _DEFAULT_SOURCE_LANGUAGE
    if not isinstance(source_language, str):
        raise ValueError(f"source_language must be a non-empty string or null, got {source_language!r}")
    return ArtifactBasis.build(
        "structured-content/step1",
        kind_version=1,
        inputs={
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "source_content": source_content,
            "source_kind": source_kind,
            "source_language": source_language,
        },
    )


def build_episode_script_basis(step1_content: object, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe every durable prompt input consumed by an episode script."""

    content_mode, generation_mode = _content_axes(project)
    return ArtifactBasis.build(
        "structured-content/episode-script",
        kind_version=2,
        inputs={
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "step1_content": step1_content,
            "prompt_context": project_episode_script_prompt_inputs(project),
        },
    )


def project_episode_script_prompt_inputs(project: Mapping[str, object]) -> dict[str, object]:
    """Project the persisted project fields rendered into non-ad step2 prompts.

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
        "characters": _project_step2_assets(project.get("characters"), generation_mode=generation_mode),
        "scenes": _project_step2_assets(project.get("scenes"), generation_mode=generation_mode),
        "props": _project_step2_assets(project.get("props"), generation_mode=generation_mode),
    }


def build_ad_episode_script_basis(episode: int, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the persisted business inputs consumed by ad script generation."""

    return ArtifactBasis.build(
        "structured-content/ad-episode-script",
        kind_version=1,
        inputs=project_ad_episode_script_inputs(episode, project=project),
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


def _project_named_assets(value: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in _mapping_or_empty(value):
        if not isinstance(name, str):
            raise ValueError("ad asset names must be strings")
        result[name] = {}
    return result


def _project_step2_assets(value: object, *, generation_mode: str) -> list[dict[str, object]]:
    """Project the ordered name/description pairs rendered by step2 builders."""

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
