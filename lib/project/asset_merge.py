"""Pure asset-merge layer: redirect references, plan derivative retention, and report impact.

Merge two same-type assets registered as one identity (``docs/adr/0057``, ``docs/adr/0092``), redirecting
all source references to retained targets. Preserve targets, appending only aliases/derivatives. This
is not renaming into existing names: renames always reject collisions.

Two forms:

- **Merge as base**: append source names/aliases to target aliases; ``被并方/衍生`` → ``保留方/衍生``.
- **Merge as derivative**, characters only: source becomes a target derivative named after source,
  without adding aliases. Visuals reference ``保留方/被并方``; speakers carry base names only
  (``docs/adr/0072``), redirecting to targets.

In both forms, derivatives matching existing target names via :func:`find_derivative_name` (including
case-only variants) merge there, rewriting references to retained names.

Only side-effect-free payload rewrites/counts/plans live here, used by
:meth:`lib.project.project_manager.ProjectManager.merge_asset` for scan-then-persist under rename-equivalent locks."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactKind, ArtifactManifestError, ArtifactStatus
from lib.project.asset_derivatives import derivative_sheet_relative_path, derivative_table, find_derivative_name
from lib.project.asset_rename import asset_files_named
from lib.project.asset_types import ALIASES_FIELD, ASSET_SPECS, asset_name_comparison_key, resolve_asset_key
from lib.prompts.reference_image_numbering import reference_image_label
from lib.references.reference_catalog import build_reference_catalog, derivative_reference, split_derivative_reference
from lib.script.reference_video.text_parser import extract_mentions, remap_mentions, render_matched_mentions
from lib.script.script_editor import ScriptEditError, resolve_items
from lib.script.script_references import iter_reference_lists, iter_reference_sites
from lib.speech.speech_artifact_provenance import build_video_speech_basis
from lib.speech.speech_composition import admit_script_unit

#: Mergeable alias-bearing types: characters/scenes/props. Products lack aliases to retain removed names.
MERGEABLE_ASSET_TYPES: tuple[str, ...] = tuple(
    asset_type for asset_type, spec in ASSET_SPECS.items() if ALIASES_FIELD in spec.extra_list_fields
)

MergeRejection = Literal["type_not_mergeable", "same_asset", "derivative_needs_character"]


class AssetMergeNotFoundError(KeyError):
    """Source or target is absent from asset tables."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


class AssetMergeRejectedError(ValueError):
    """Invalid merge (unsupported type, self-merge, noncharacter derivative merge): reject wholly without persistence."""

    def __init__(self, reason: MergeRejection, detail: str):
        super().__init__(detail)
        self.reason: MergeRejection = reason


@dataclass(frozen=True, slots=True)
class ReferenceChanges:
    """Rewritten-reference counts per payload, split by arrays, speakers, and body/visual markers."""

    names: int = 0
    speakers: int = 0
    mentions: int = 0

    @property
    def total(self) -> int:
        return self.names + self.speakers + self.mentions


@dataclass(frozen=True, slots=True)
class AssetMergeEpisodeImpact:
    """Episode merge impact: rewrite counts and current storyboard/video counts becoming stale.

    ``script_plan`` / ``script`` / ``draft`` count arrays by file only. Speakers/markers aggregate across
    files into ``speaker`` / ``prompt_text``; all fields sum to total episode rewrites."""

    episode: int
    script_plan: int = 0
    script: int = 0
    draft: int = 0
    prompt_text: int = 0
    speaker: int = 0
    storyboards: int = 0
    videos: int = 0

    @property
    def references(self) -> int:
        return self.script_plan + self.script + self.draft + self.prompt_text + self.speaker


@dataclass(frozen=True, slots=True)
class AssetMergeReport:
    """Merge impact report; dry-run/execution share one scan and necessarily identical counts."""

    table: str
    source: str
    target: str
    as_derivative: bool
    #: Names appended to target aliases; empty for derivative merges.
    aliases_added: tuple[str, ...]
    #: New derivative name, or ``None`` when merging a same-name/case-only existing target derivative.
    derivative_created: str | None
    #: Source derivatives carried to targets.
    derivatives_moved: tuple[str, ...]
    #: Source derivatives merging same-name/case-only target derivatives; their descriptions/sheets are not retained.
    derivatives_folded: tuple[str, ...]
    episodes: tuple[AssetMergeEpisodeImpact, ...]
    dry_run: bool

    @property
    def references(self) -> int:
        return sum(item.references for item in self.episodes)


@dataclass(frozen=True, slots=True)
class DerivativeMergePlan:
    """Source-character derivative handling: create, carry, or merge into existing target derivatives, with resulting reference names."""

    created: str | None = None
    moved: tuple[str, ...] = ()
    folded: tuple[str, ...] = ()
    #: Source-base derivative name under target (new/existing), in comparison coordinates.
    source_derivative: str | None = None
    #: Source derivative → target derivative comparison names, differing for case-only existing matches.
    derivative_names: Mapping[str, str] = field(default_factory=dict)

    def kept_derivative(self, derivative: str) -> str:
        """Resulting target derivative name for source comparison-coordinate names; unchanged if not source derivatives."""
        return self.derivative_names.get(derivative, derivative)

    def carried_references(self, source: str, target: str) -> dict[str, str]:
        """Carried derivatives: after-name → before-name in comparison coordinates.

        They carry images and only rename references. Derivatives merging into existing ones change image content and are excluded."""
        return {
            derivative_reference(asset_name_comparison_key(target), asset_name_comparison_key(name)): (
                derivative_reference(asset_name_comparison_key(source), asset_name_comparison_key(name))
            )
            for name in self.moved
        }


def merged_reference(
    name: str,
    source: str,
    target: str,
    *,
    as_derivative: bool,
    speaker: bool,
    derivatives: DerivativeMergePlan,
) -> str | None:
    """Rewrite one reference for a merge, returning ``None`` if unrelated to source.

    *source* / *target* are table names. ``被并方/衍生`` always becomes ``保留方/衍生``, carrying
    base ownership. Source bases merged as derivatives become ``保留方/被并方`` except speakers,
    which still use target bases. *derivatives* supplies resulting names, retaining existing target
    spellings when merging case-only matches."""
    key = asset_name_comparison_key(name)
    source_key = asset_name_comparison_key(source)
    base, derivative = split_derivative_reference(key)
    if derivative:
        return derivative_reference(target, derivatives.kept_derivative(derivative)) if base == source_key else None
    if key != source_key:
        return None
    if as_derivative and not speaker:
        return derivative_reference(target, derivatives.source_derivative or source_key)
    return target


def merge_payload_references(
    payload: dict[str, Any],
    asset_type: str,
    source: str,
    target: str,
    *,
    as_derivative: bool = False,
    derivatives: DerivativeMergePlan,
) -> ReferenceChanges:
    """Redirect script/draft source references to targets in place, returning counts by site.

    Share rename sites (:func:`lib.script.script_references.iter_reference_sites`). Targets may already
    appear in arrays, so deduplicate by comparison coordinates after rewriting, retaining first occurrences.
    Delete source entries from embedded legacy top-level ``characters`` mirrors, counting as array rewrites."""
    spec = ASSET_SPECS[asset_type]
    names = speakers = mentions = 0
    written: set[str] = set()

    def remap_for(kind: str) -> tuple[Callable[[str, bool], str | None], list[int]]:
        hits = [0]

        def remap(name: str, is_speaker: bool) -> str | None:
            merged = merged_reference(
                name,
                source,
                target,
                as_derivative=as_derivative,
                speaker=is_speaker and kind == "text",
                derivatives=derivatives,
            )
            if merged is None:
                return None
            hits[0] += 1
            return f"@[{merged}]"

        return remap, hits

    for kind, value, write in iter_reference_sites(
        payload, frozenset(spec.reference_list_fields), with_speaker=asset_type == "character"
    ):
        if kind in ("text", "scene"):
            remap, hits = remap_for(kind)
            rewritten = remap_mentions(value, remap)
            if hits[0]:
                write(rewritten)
                mentions += hits[0]
            continue
        # New-asset targets, like speakers, carry bases only; derivative merges point to retained bases, never ``保留方/被并方``.
        merged = merged_reference(
            value,
            source,
            target,
            as_derivative=as_derivative,
            speaker=kind in ("speaker", "target"),
            derivatives=derivatives,
        )
        if merged is None:
            continue
        write(merged)
        if kind == "speaker":
            speakers += 1
        else:
            names += 1
            written.add(asset_name_comparison_key(merged))

    for items in iter_reference_lists(payload, frozenset(spec.reference_list_fields)):
        seen: set[str] = set()
        kept: list[Any] = []
        for item in items:
            if isinstance(item, str):
                key = asset_name_comparison_key(item)
                if key in written and key in seen:
                    continue
                seen.add(key)
            kept.append(item)
        if len(kept) != len(items):
            items[:] = kept

    if asset_type == "character":
        embedded = payload.get("characters")
        embedded_key = resolve_asset_key(embedded, source) if isinstance(embedded, dict) else None
        if isinstance(embedded, dict) and embedded_key is not None:
            del embedded[embedded_key]
            names += 1

    return ReferenceChanges(names=names, speakers=speakers, mentions=mentions)


def visual_reference_signature(item: Mapping[str, Any], asset_type: str) -> tuple[object, ...]:
    """Visual artifact references: arrays, visual descriptions, and visual body markers, excluding speakers.

    Changed signatures stale storyboard/reference-video registrations through mismatched basis. Speakers
    and speech-marker speaker positions do not enter visual basis; speaker-only changes do not count."""
    spec = ASSET_SPECS[asset_type]
    lists = tuple(
        tuple(item.get(name) or ()) for name in spec.reference_list_fields if isinstance(item.get(name), list)
    )
    image_prompt = item.get("image_prompt")
    scene = image_prompt.get("scene") if isinstance(image_prompt, Mapping) else image_prompt
    text = item.get("text")
    text_mentions = tuple(extract_mentions(text)) if isinstance(text, str) else ()
    return (lists, scene if isinstance(scene, str) else None, text_mentions)


def storyboard_reference_signature(
    item: Mapping[str, Any], asset_type: str, identities: Mapping[str, str]
) -> tuple[object, ...]:
    """Storyboard references compared by referenced image content.

    Basis omits asset names (``without_asset_names``): replace usable image names with content identities
    from *identities*. Missing-image gaps remain name-distinct. In bodies, replace only references actually selected by arrays."""
    remapped = dict(item)
    spec = ASSET_SPECS[asset_type]
    selected: dict[str, str] = {}
    for name in spec.reference_list_fields:
        names = item.get(name)
        if isinstance(names, list):
            selected.update(
                (key, identities[key])
                for value in names
                if isinstance(value, str) and (key := asset_name_comparison_key(value)) in identities
            )
            remapped[name] = [
                identities.get(asset_name_comparison_key(value), value) if isinstance(value, str) else value
                for value in names
            ]
    image_prompt = item.get("image_prompt")
    scene = image_prompt.get("scene") if isinstance(image_prompt, Mapping) else image_prompt
    if isinstance(scene, str):
        remapped["image_prompt"] = remap_mentions(
            scene, lambda name, _speaker: f"@[{selected[name]}]" if name in selected else None
        )
    return visual_reference_signature(remapped, asset_type)


def storyboard_reference_identities(project_dir: Path, project: dict[str, Any], asset_type: str) -> dict[str, str]:
    """Registered usable asset images: reference name → content identity; omit references without images."""
    # Generation inputs depend on ProjectManager through planners; lazy import avoids cycles.
    from lib.artifacts.generation_input import project_input_observation
    from lib.artifacts.visual_artifact_provenance import visual_file_digest

    observation = project_input_observation(project_dir)
    catalog = build_reference_catalog(project)
    identities: dict[str, str] = {}
    for name in catalog.reference_names(asset_type):
        reference = catalog.lookup(asset_type, name)
        if reference is None or not isinstance(reference.asset, Mapping):
            continue
        sheet = reference.asset.get(reference.spec.sheet_field)
        registered = observation.registered(ArtifactKey.asset_sheet(asset_type, name))
        if not isinstance(sheet, str) or registered is None or registered.artifact_path != sheet:
            continue
        observed = observation.observe(sheet)
        if observed is not None:
            try:
                identities[name] = f"sha256:{visual_file_digest(observed.path)}"
            except OSError:
                continue
    return identities


#: Whether artifacts are current and reference-dependent: ``(清单键, 产物路径字段值) → bool``.
CurrentArtifactProbe = Callable[[ArtifactKey, object], bool]


def current_artifact_probe(project_dir: Path) -> CurrentArtifactProbe:
    """Manifest-currentness probe; always false without usable manifests (unupgraded/corrupt).

    Selected uploads judge currency by uploaded bytes, not script references/speakers/grid images, so
    merges cannot stale them; return false too. Planners determine upload basis (selected upload matching
    formal bytes), without separate inference here."""
    # artifact_currency depends on project_manager through planning; top-level imports would cycle.
    from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
    from lib.artifacts.visual_artifact_provenance import (
        UPLOADED_STORYBOARD_BASIS_KIND,
        UPLOADED_VIDEO_BASIS_KIND,
    )

    uploaded_basis_kinds = {
        ArtifactKind.EPISODE_STORYBOARD: UPLOADED_STORYBOARD_BASIS_KIND,
        ArtifactKind.EPISODE_VIDEO: UPLOADED_VIDEO_BASIS_KIND,
    }

    try:
        resolver = ArtifactCurrencyResolver(project_dir)
    except (ArtifactManifestError, OSError, ValueError):
        return lambda _key, _path: False

    def is_current(key: ArtifactKey, artifact_path: object) -> bool:
        if not isinstance(artifact_path, str) or not artifact_path:
            return False
        try:
            if resolver.compare(key, artifact_path=artifact_path).status is not ArtifactStatus.CURRENT:
                return False
            uploaded_kind = uploaded_basis_kinds.get(key.kind)
            if uploaded_kind is not None:
                basis = resolver.resolve_basis(key)
                return basis is None or basis.kind != uploaded_kind
            return True
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError):
            return False

    return is_current


def speech_signature(kind: str, item: Mapping[str, Any]) -> str | None:
    """Item components entering video voice basis (dialogue speakers/text); ``None`` when unsynthesizable.

    Exclude voice settings: merges only change speakers; without speaker changes neither side's settings enter this item's basis."""
    try:
        admission = admit_script_unit(kind, item)
        if not admission.allowed:
            return None
        return build_video_speech_basis(admission.preparation).digest
    except (TypeError, ValueError):
        return None


def grid_merge_references(
    project_dir: Path, asset_type: str, source: str, folded_derivatives: Sequence[str]
) -> tuple[set[str], dict[str, tuple[str, ...]]]:
    """Grid IDs losing shared references plus currently available frozen reference names for each grid."""
    from lib.artifacts.artifact_planner import TargetStatePlanner

    removed = [
        *asset_files_named(project_dir, ASSET_SPECS[asset_type], source),
        *(project_dir / derivative_sheet_relative_path(source, name) for name in folded_derivatives),
    ]
    removed_paths = {
        unicodedata.normalize("NFC", path.relative_to(project_dir).as_posix())
        for path in removed
        if path.is_file() and not path.is_symlink()
    }
    try:
        grids = TargetStatePlanner(project_dir).load_grid_records()
    except (ArtifactManifestError, OSError, ValueError):
        # Like current_artifact_probe, unreadable basis never counts as current-to-stale.
        return set(), {}
    outdated = {
        grid.id
        for grid in grids
        if any(
            unicodedata.normalize("NFC", reference.path) in removed_paths for reference in grid.reference_images or []
        )
    }
    names = {
        grid.id: tuple(
            reference.name for reference in grid.reference_images or [] if (project_dir / reference.path).is_file()
        )
        for grid in grids
    }
    return outdated, names


def grid_storyboard_reference_signature(item: Mapping[str, Any], reference_names: Sequence[str]) -> str | None:
    """Merge changes this cell's visual mentions only; number against whole-grid frozen references, not cell arrays."""
    image_prompt = item.get("image_prompt")
    scene = image_prompt.get("scene") if isinstance(image_prompt, Mapping) else image_prompt
    if not isinstance(scene, str):
        return None
    labels: dict[str, str] = {}
    for position, name in enumerate(reference_names):
        labels.setdefault(asset_name_comparison_key(name), reference_image_label(position))
    return render_matched_mentions(scene, labels.get)


def count_outdated_artifacts(
    before: dict[str, Any],
    after: dict[str, Any],
    asset_type: str,
    episode: int,
    is_current: CurrentArtifactProbe,
    *,
    storyboard_identities: Mapping[str, str],
    carried_references: Mapping[str, str],
    outdated_grid_ids: Collection[str],
    grid_reference_names: Mapping[str, Sequence[str]],
) -> tuple[int, int]:
    """Current-to-stale ``(分镜图数, 视频数)`` after merging one formal script.

    Count only current artifacts with reference-dependent basis, excluding selected uploaded storyboards
    (:func:`current_artifact_probe`). Compare reference-related basis portions:

    - Storyboards: changed images (:func:`storyboard_reference_signature`); carried derivatives merely
      renamed with images do not count. Split cells use frozen grid references, where carried derivatives
      (*carried_references*, after→before reference names) only change names/paths. Lost shared images count all split cells.
    - Reference video: changed visuals or dialogue speakers (:func:`speech_signature`).
    - Storyboard video: changed speakers. Visual input is the storyboard itself, unaffected by reference
      changes until that image regenerates."""
    try:
        before_items, id_field, kind = resolve_items(before)
        after_items, _id_field, _kind = resolve_items(after)
    except ScriptEditError:
        return 0, 0
    grid_renames = {previous: current for current, previous in carried_references.items()}
    storyboards = videos = 0
    for old, new in zip(before_items, after_items, strict=True):
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        resource_id = old.get(id_field)
        if not isinstance(resource_id, str):
            continue
        visual_changed = visual_reference_signature(old, asset_type) != visual_reference_signature(new, asset_type)
        speech_changed = speech_signature(kind, old) != speech_signature(kind, new)
        generated = old.get("generated_assets")
        generated = generated if isinstance(generated, Mapping) else {}
        if generated.get("grid_id") is not None or generated.get("grid_cell_index") is not None:
            grid_id = generated.get("grid_id")
            names = grid_reference_names.get(grid_id, ()) if isinstance(grid_id, str) else ()
            renamed = tuple(grid_renames.get(asset_name_comparison_key(name), name) for name in names)
            storyboard_changed = (
                isinstance(grid_id, str) and grid_id in outdated_grid_ids
            ) or grid_storyboard_reference_signature(old, names) != grid_storyboard_reference_signature(new, renamed)
        else:
            storyboard_changed = storyboard_reference_signature(
                old, asset_type, storyboard_identities
            ) != storyboard_reference_signature(new, asset_type, storyboard_identities)
        if kind != "video_units" and storyboard_changed:
            storyboard_key = ArtifactKey.episode_storyboard(episode, resource_id)
            storyboards += is_current(storyboard_key, generated.get("storyboard_image"))
        if speech_changed or (kind == "video_units" and visual_changed):
            videos += is_current(ArtifactKey.episode_video(episode, resource_id), generated.get("video_clip"))
    return storyboards, videos


def plan_derivative_merge(
    source_entry: Mapping[str, Any] | None,
    target_entry: Mapping[str, Any] | None,
    source: str,
    *,
    as_derivative: bool,
) -> DerivativeMergePlan:
    """Plan source derivatives carried to targets or merged into same-name existing target derivatives.

    Use :func:`find_derivative_name`, including case-only collisions sharing file paths; references retain
    existing names. Merge-as-derivative first creates source-named derivatives, or merges existing matches
    without creation. Same-named derivatives already under source merge there too."""
    # Target derivatives: existing plus newly created/carried.
    kept: dict[str, None] = dict.fromkeys(derivative_table(target_entry))
    created: str | None = None
    source_derivative: str | None = None
    if as_derivative:
        source_key = asset_name_comparison_key(source)
        existing = find_derivative_name(kept, source_key)
        if existing is None:
            created = existing = source_key
            kept[source_key] = None
        source_derivative = asset_name_comparison_key(existing)
    moved: list[str] = []
    folded: list[str] = []
    names: dict[str, str] = {}
    for name in derivative_table(source_entry):
        existing = find_derivative_name(kept, name)
        if existing is None:
            moved.append(name)
            kept[name] = None
            existing = name
        else:
            folded.append(name)
        names[asset_name_comparison_key(name)] = asset_name_comparison_key(existing)
    return DerivativeMergePlan(
        created=created,
        moved=tuple(moved),
        folded=tuple(folded),
        source_derivative=source_derivative,
        derivative_names=names,
    )


__all__ = [
    "MERGEABLE_ASSET_TYPES",
    "AssetMergeEpisodeImpact",
    "AssetMergeNotFoundError",
    "AssetMergeRejectedError",
    "AssetMergeReport",
    "CurrentArtifactProbe",
    "DerivativeMergePlan",
    "MergeRejection",
    "ReferenceChanges",
    "count_outdated_artifacts",
    "current_artifact_probe",
    "grid_merge_references",
    "grid_storyboard_reference_signature",
    "merge_payload_references",
    "merged_reference",
    "plan_derivative_merge",
    "speech_signature",
    "storyboard_reference_identities",
    "storyboard_reference_signature",
    "visual_reference_signature",
]
