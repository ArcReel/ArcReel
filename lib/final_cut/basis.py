"""Final-cut artifact identities and generation bases.

Identity combines episode + editing timeline + narration variant + burned-in subtitles. Bases
contain only rendering inputs: revision content affecting pictures/sound (clip order, effective
trims, source volume, holds, transitions), current video versions/content fingerprints/provider-audio
flags for referenced units, and output canvas. Revision numbers identify editing-decision snapshots;
editing rationales do not separately enter bases. Trims whose underlying version is no longer current
are ignored, and bases record full-source usage.

With BGM, add its clips and each referenced track’s fingerprint/static gain; without BGM, basis
shape stays unchanged.

Narration or burned-subtitle variants additionally consume unit material layers shared with
Jianying drafts (:mod:`lib.jianying_draft.placement`), adding their fingerprints, which include
subtitle drafts and, for narration variants, voiceovers. Variants with neither narration nor
burned subtitles omit them, so voiceover/subtitle-draft changes do not expire those variants.

Snapshot the specified revision’s basis when rendering starts; rebuild from the latest revision
for currency checks. Both share :func:`resolve_final_cut_inputs` and :func:`final_cut_basis`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.artifacts.rendered_artifact import timeline_renders_dir
from lib.artifacts.version_manager import VERSION_CONTENT_DIGEST, UnmanagedSnapshotPathError, VersionManager
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.bgm.library import BgmSource
from lib.edit_timeline.bgm import bgm_sources_input
from lib.edit_timeline.model import BgmClip, EditClip, EditTimelineDocument, TimelineRevision
from lib.final_cut.render_plan import OutputProfile, output_profile_for_aspect_ratio
from lib.jianying_draft.basis import DraftNarration, DraftUnitBasis, default_draft_narration
from lib.project.resource_paths import resource_relative_path

FINAL_CUT_BASIS_KIND = "final-cut/episode"
FINAL_CUT_BASIS_VERSION = 1

type SubtitleMode = Literal["burned_subtitles", "no_subtitles"]
"""Final-cut subtitle mode: burn subtitles in, or omit them for clean footage."""

WITHOUT_NARRATION: DraftNarration = "without_narration"
WITH_NARRATION: DraftNarration = "with_narration"
NO_SUBTITLES: SubtitleMode = "no_subtitles"
BURNED_SUBTITLES: SubtitleMode = "burned_subtitles"


@dataclass(frozen=True, slots=True)
class FinalCutVariant:
    """Final-cut narration variant and subtitle mode; narration values match Jianying drafts."""

    narration: DraftNarration = WITHOUT_NARRATION
    subtitles: SubtitleMode = NO_SUBTITLES

    @property
    def slug(self) -> str:
        return f"{self.narration}.{self.subtitles}"

    @property
    def with_narration(self) -> bool:
        return self.narration == WITH_NARRATION

    @property
    def burns_subtitles(self) -> bool:
        return self.subtitles == BURNED_SUBTITLES

    @property
    def consumes_unit_materials(self) -> bool:
        """Narration/burned-subtitle variants consume unit material layers (voiceovers and subtitles)."""
        return self.with_narration or self.burns_subtitles


FINAL_CUT_VARIANTS = tuple(
    FinalCutVariant(narration, subtitles)
    for narration in (WITHOUT_NARRATION, WITH_NARRATION)
    for subtitles in (BURNED_SUBTITLES, NO_SUBTITLES)
)
"""All final-cut artifact identity combinations; narration variants are available only to TTS-dubbing projects."""


def resolve_final_cut_variant(
    project: Mapping[str, Any], *, narration: DraftNarration | None = None, subtitles: SubtitleMode | None = None
) -> FinalCutVariant:
    """Fill omitted options: TTS projects default to narration, others without it (matching Jianying drafts); subtitles default to burned-in."""
    return FinalCutVariant(narration or default_draft_narration(project), subtitles or BURNED_SUBTITLES)


def final_cut_key(episode: int, timeline_id: str, variant: FinalCutVariant) -> ArtifactKey:
    return ArtifactKey.episode_final_cut(episode, timeline_id, variant.narration, variant.subtitles)


def final_cut_artifact_path(episode: int, timeline_id: str, variant: FinalCutVariant) -> str:
    """Formal final-cut path; keep only this latest file per artifact identity."""
    return f"{timeline_renders_dir(episode, timeline_id)}/final_cut.{variant.slug}.mp4"


def video_resource_type_for(script_kind: str) -> str:
    return "reference_videos" if script_kind == "video_units" else "videos"


def output_profile_for_project(project: Mapping[str, Any], script_kind: str) -> OutputProfile:
    return output_profile_for_aspect_ratio(resolve_video_aspect_ratio(project, video_resource_type_for(script_kind)))


@dataclass(frozen=True, slots=True)
class CurrentVideo:
    """Formal current-video file, rendering snapshot file for that version, and snapshot content fingerprint.

    ``provider_audio`` comes from the version record: when it explicitly records no generated audio,
    rendering ignores audio tracks in the snapshot.
    """

    resource_type: str
    unit_id: str
    version: int
    artifact_path: str
    content_digest: str
    snapshot: Path
    provider_audio: bool


def current_video(
    project_dir: Path,
    versions: VersionManager,
    resource_type: str,
    unit_id: str,
    digest: Callable[[str], str],
) -> CurrentVideo | None:
    """Available video: current version > 0, with formal file and version snapshot present; stale videos are also usable.

    Render snapshots, not formal files: generation/restoration can replace formal files, never snapshots.
    Fingerprints describe snapshots. Use recorded snapshot fingerprints without reading snapshots
    (verified against records before rendering); only old versions without recorded fingerprints
    obtain them on demand through ``digest``.
    """
    version = versions.get_current_version(resource_type, unit_id)
    artifact_path = resource_relative_path(resource_type, unit_id)
    if version <= 0 or not (project_dir / artifact_path).is_file():
        return None
    found = version_snapshot(project_dir, versions, resource_type, unit_id, version)
    if found is None:
        return None
    snapshot, record = found
    return CurrentVideo(
        resource_type=resource_type,
        unit_id=unit_id,
        version=version,
        artifact_path=artifact_path,
        content_digest=record.get(VERSION_CONTENT_DIGEST) or digest(snapshot.relative_to(project_dir).as_posix()),
        snapshot=snapshot,
        provider_audio=provider_audio_recorded(record),
    )


def provider_audio_recorded(record: Mapping[str, Any]) -> bool:
    """Version-record provider-audio flag: False only when explicitly recorded as not generated; missing records imply audio."""
    return record.get("execution_generate_audio") is not False


def current_provider_audio(versions: VersionManager, resource_type: str, unit_id: str) -> bool:
    """Current video-unit version’s provider-audio flag; absent version records imply audio."""
    info = versions.get_versions(resource_type, unit_id)
    current = info["current_version"]
    for record in info["versions"]:
        if record.get("version") == current:
            return provider_audio_recorded(record)
    return True


def version_snapshot(
    project_dir: Path, versions: VersionManager, resource_type: str, unit_id: str, version: int
) -> tuple[Path, Mapping[str, Any]] | None:
    """Specified version’s snapshot file/record, or None if absent, snapshot path unmanaged, or file missing."""
    records = versions.get_versions(resource_type, unit_id).get("versions")
    for record in records if isinstance(records, list) else []:
        if isinstance(record, Mapping) and record.get("version") == version:
            try:
                path = VersionManager.resolve_snapshot_path(project_dir, resource_type, record.get("file"))
            except UnmanagedSnapshotPathError:
                return None
            return (path, record) if path.is_file() else None
    return None


@dataclass(frozen=True, slots=True)
class ConsumedClip:
    clip: EditClip
    video: CurrentVideo


@dataclass(frozen=True, slots=True)
class FinalCutInputs:
    """All inputs consumed by one render; nonempty ``missing_video_units`` prevents rendering."""

    episode: int
    timeline_id: str
    revision: int
    variant: FinalCutVariant
    profile: OutputProfile
    clips: tuple[ConsumedClip, ...]
    missing_video_units: tuple[str, ...]
    units: tuple[DraftUnitBasis, ...] = ()
    """Unit material-layer fingerprints, included only when :attr:`FinalCutVariant.consumes_unit_materials`."""
    bgm: tuple[BgmClip, ...] = ()
    bgm_sources: Mapping[str, BgmSource] = field(default_factory=dict)
    """BGM referenced by the BGM track; exclude tracks absent from the project or disk."""


def resolve_final_cut_inputs(
    *,
    document: EditTimelineDocument,
    revision: TimelineRevision,
    variant: FinalCutVariant,
    profile: OutputProfile,
    script_unit_ids: Collection[str],
    video_of: Callable[[str], CurrentVideo | None],
    bgm_sources: Mapping[str, BgmSource] | None = None,
) -> FinalCutInputs:
    """Resolve each clip’s video in revision order, skipping clips whose units were deleted from the script.

    ``bgm_sources`` contains BGM referenced by the BGM track (:func:`~lib.bgm.library.resolve_bgm_sources`).
    """
    consumed: list[ConsumedClip] = []
    missing: list[str] = []
    resolved: dict[str, CurrentVideo | None] = {}
    for clip in revision.content.clips:
        if clip.unit_id not in script_unit_ids:
            continue
        if clip.unit_id not in resolved:
            resolved[clip.unit_id] = video_of(clip.unit_id)
        video = resolved[clip.unit_id]
        if video is None:
            if clip.unit_id not in missing:
                missing.append(clip.unit_id)
            continue
        consumed.append(ConsumedClip(clip=clip, video=video))
    return FinalCutInputs(
        episode=document.episode,
        timeline_id=document.id,
        revision=revision.number,
        variant=variant,
        profile=profile,
        clips=tuple(consumed),
        missing_video_units=tuple(missing),
        bgm=revision.content.bgm,
        bgm_sources=dict(bgm_sources or {}),
    )


def _clip_input(item: ConsumedClip) -> dict[str, object]:
    clip = item.clip
    trim = clip.trim if clip.trim is not None and clip.trim.basis_version == item.video.version else None
    transition = clip.transition_to_next
    return {
        "unit_id": clip.unit_id,
        "video": {
            "resource_type": item.video.resource_type,
            "version": item.video.version,
            "content_digest": item.video.content_digest,
            "provider_audio": item.video.provider_audio,
        },
        "trim": {"in_us": trim.in_us, "out_us": trim.out_us} if trim is not None else None,
        "source_volume": clip.source_volume,
        "hold_us": clip.hold_us,
        "transition_to_next": (
            {"type": transition.type.value, "duration_us": transition.duration_us} if transition is not None else None
        ),
    }


def final_cut_basis(inputs: FinalCutInputs) -> ArtifactBasis:
    """Final-cut generation basis, built here for both registration and currency comparison."""
    if inputs.missing_video_units:
        raise ValueError(f"final cut inputs lack usable videos: {', '.join(inputs.missing_video_units)}")
    basis_inputs: dict[str, object] = {
        "timeline_id": inputs.timeline_id,
        "revision": inputs.revision,
        "narration": inputs.variant.narration,
        "subtitles": inputs.variant.subtitles,
        "output": {"width": inputs.profile.width, "height": inputs.profile.height, "fps": inputs.profile.fps},
        "clips": [_clip_input(item) for item in inputs.clips],
    }
    if inputs.variant.consumes_unit_materials:
        basis_inputs["units"] = {unit.unit_id: unit.to_input() for unit in inputs.units}
    if inputs.bgm:
        basis_inputs["bgm"] = {
            "clips": [clip.model_dump(mode="json") for clip in sorted(inputs.bgm, key=lambda item: item.id)],
            "sources": bgm_sources_input(inputs.bgm, inputs.bgm_sources),
        }
    return ArtifactBasis.build(FINAL_CUT_BASIS_KIND, kind_version=FINAL_CUT_BASIS_VERSION, inputs=basis_inputs)


__all__ = [
    "BURNED_SUBTITLES",
    "FINAL_CUT_BASIS_KIND",
    "FINAL_CUT_BASIS_VERSION",
    "FINAL_CUT_VARIANTS",
    "NO_SUBTITLES",
    "WITHOUT_NARRATION",
    "WITH_NARRATION",
    "ConsumedClip",
    "CurrentVideo",
    "FinalCutInputs",
    "FinalCutVariant",
    "SubtitleMode",
    "current_video",
    "final_cut_artifact_path",
    "final_cut_basis",
    "final_cut_key",
    "output_profile_for_project",
    "resolve_final_cut_inputs",
    "resolve_final_cut_variant",
    "version_snapshot",
    "video_resource_type_for",
]
