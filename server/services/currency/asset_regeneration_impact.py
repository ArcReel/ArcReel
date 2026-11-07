"""Impact of regenerating an asset image: counts of storyboard images, videos and derivative asset images that would become stale.

Regeneration changes asset-image bytes, invalidating the recorded basis of current artifacts using it as a reference. Count only artifacts
that are **currently current** and include this image in generation input; do not count already stale artifacts again:

- Storyboards: the name appears in reference fields (``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot``).
  A derivative reference ``本体/衍生`` uses its own asset image, not the base image.
- Videos: only reference-to-video directly references asset images. Storyboard-to-video uses storyboard images, so asset regeneration
  does not directly make those videos stale. Reference-video units use body ``@[名称]`` and reference fields.
- Derivative asset images: the base image is the sole input for generated derivatives; regenerating it makes all current generated derivatives stale.
  Uploaded or library-imported derivatives are finished images whose basis depends only on image bytes; base regeneration does not make them stale, so exclude them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.artifacts.visual_artifact_provenance import UPLOADED_ASSET_SHEET_BASIS_KIND
from lib.generation.generation_result import observe_artifact_status
from lib.project.asset_derivatives import derivative_artifact_id, derivative_artifact_key, derivative_table
from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key, resolve_asset_key
from lib.project.project_manager import ProjectManager
from lib.script.script_editor import ScriptEditError, resolve_items
from lib.script.script_references import payload_reference_names

_REFERENCE_LIST_FIELDS: frozenset[str] = frozenset(
    field for spec in ASSET_SPECS.values() for field in spec.reference_list_fields
)


@dataclass(frozen=True, slots=True)
class AssetRegenerationImpact:
    stale: bool
    storyboards: int
    videos: int
    derivatives: int


def _visual_reference_names(item: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for field_name in _REFERENCE_LIST_FIELDS:
        value = item.get(field_name)
        if isinstance(value, list):
            names.update(asset_name_comparison_key(name) for name in value if isinstance(name, str))
    return names


def _is_current(resolver: ArtifactCurrencyResolver, key: ArtifactKey, artifact_path: object) -> bool:
    status, _blocker = observe_artifact_status(resolver=resolver, key=key, artifact_path=artifact_path)
    return status is ArtifactStatus.CURRENT


def _follows_owner_sheet(resolver: ArtifactCurrencyResolver, key: ArtifactKey, artifact_path: object) -> bool:
    """Whether this derivative image is current with a basis including the base image; imported finished derivatives depend on image bytes and are unaffected by base regeneration."""
    if not _is_current(resolver, key, artifact_path):
        return False
    basis = resolver.resolve_basis(key)
    return basis is None or basis.kind != UPLOADED_ASSET_SHEET_BASIS_KIND


def _episode_scripts(
    projects: ProjectManager, project_name: str, project: Mapping[str, Any]
) -> Iterable[tuple[int, dict[str, Any]]]:
    for entry in project.get("episodes") or []:
        if not isinstance(entry, Mapping):
            continue
        episode = entry.get("episode")
        script_file = entry.get("script_file")
        if type(episode) is not int or not isinstance(script_file, str) or not script_file:
            continue
        try:
            yield episode, projects.load_script_readonly(project_name, script_file)
        except (FileNotFoundError, ValueError):
            continue


def asset_regeneration_impact(
    projects: ProjectManager,
    project_name: str,
    asset_type: str,
    name: str,
    *,
    derivative_name: str | None = None,
) -> AssetRegenerationImpact:
    """Whether this base or derivative image is stale now, and how many current artifacts regeneration would make stale; raise ``KeyError`` for a missing asset."""

    spec = ASSET_SPECS[asset_type]
    project = projects.load_project(project_name)
    bucket = project.get(spec.bucket_key)
    key = resolve_asset_key(bucket, name)
    entry = bucket.get(key) if isinstance(bucket, Mapping) and key is not None else None
    if key is None or not isinstance(entry, Mapping):
        raise KeyError(name)
    resolver = active_artifact_currency_resolver(projects.get_project_path(project_name), project)
    target = asset_name_comparison_key(key)
    sheet_key = ArtifactKey.asset_sheet(asset_type, target)
    sheet_path = entry.get(spec.sheet_field)
    if derivative_name is not None:
        derivatives_of_entry = derivative_table(entry)
        derivative_key = resolve_asset_key(derivatives_of_entry, derivative_name) if spec.supports_derivatives else None
        derivative = derivatives_of_entry.get(derivative_key) if derivative_key is not None else None
        if derivative_key is None or not isinstance(derivative, Mapping):
            raise KeyError(derivative_name)
        sheet_key = derivative_artifact_key(target, asset_name_comparison_key(derivative_key))
        sheet_path = derivative.get(spec.sheet_field)
        target = derivative_artifact_id(target, asset_name_comparison_key(derivative_key))
    stale, _blocker = observe_artifact_status(resolver=resolver, key=sheet_key, artifact_path=sheet_path)

    derivatives = 0
    if spec.supports_derivatives and derivative_name is None:
        for derivative_name, derivative in derivative_table(entry).items():
            if isinstance(derivative, Mapping) and _follows_owner_sheet(
                resolver,
                derivative_artifact_key(target, asset_name_comparison_key(derivative_name)),
                derivative.get(spec.sheet_field),
            ):
                derivatives += 1

    storyboards = 0
    videos = 0
    for episode, script in _episode_scripts(projects, project_name, project):
        try:
            items, id_field, kind = resolve_items(script)
        except ScriptEditError:
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get(id_field), str):
                continue
            resource_id = item[id_field]
            generated = item.get("generated_assets")
            generated = generated if isinstance(generated, Mapping) else {}
            if kind == "video_units":
                if target in payload_reference_names({"video_units": [item]}) and _is_current(
                    resolver, ArtifactKey.episode_video(episode, resource_id), generated.get("video_clip")
                ):
                    videos += 1
                continue
            if target in _visual_reference_names(item) and _is_current(
                resolver, ArtifactKey.episode_storyboard(episode, resource_id), generated.get("storyboard_image")
            ):
                storyboards += 1
    return AssetRegenerationImpact(
        stale=stale is ArtifactStatus.STALE, storyboards=storyboards, videos=videos, derivatives=derivatives
    )


__all__ = ["AssetRegenerationImpact", "asset_regeneration_impact"]
