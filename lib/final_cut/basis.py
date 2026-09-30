"""成片的产物身份与生成依据。

产物身份是「集 + 剪辑时间线 + 旁白版本 + 是否烧入字幕」。生成依据只收录渲染实际消费的内容：
剪辑时间线修订里影响画面与声音的部分（片段顺序、生效的截取、原声音量、定格延长、转场），
各片段所用视频单元 current 视频的版本与内容指纹，以及输出画布。修订号标识本次剪辑决策快照；剪辑理由不单独进入依据；
截取所依据的版本已不是 current 时截取被忽略，依据里也记为整段使用。

渲染任务开始时按指定修订取依据快照，产物时效判定按最新修订重建依据，两处共用
:func:`resolve_final_cut_inputs` 与 :func:`final_cut_basis`。
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.artifacts.rendered_artifact import RENDERS_DIRNAME
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.edit_timeline.model import EditClip, EditTimelineDocument, TimelineRevision
from lib.final_cut.render_plan import OutputProfile, output_profile_for_aspect_ratio
from lib.project.resource_paths import resource_relative_path

FINAL_CUT_BASIS_KIND = "final-cut/episode"
FINAL_CUT_BASIS_VERSION = 1

WITHOUT_NARRATION = "without_narration"
NO_SUBTITLES = "no_subtitles"


@dataclass(frozen=True, slots=True)
class FinalCutVariant:
    """成片的旁白版本与字幕烧入方式。"""

    narration: str = WITHOUT_NARRATION
    subtitles: str = NO_SUBTITLES

    @property
    def slug(self) -> str:
        return f"{self.narration}.{self.subtitles}"


DEFAULT_VARIANT = FinalCutVariant()

SUPPORTED_VARIANTS = frozenset({DEFAULT_VARIANT})
"""当前能渲染的组合：不带旁白、不烧入字幕。"""


def final_cut_key(episode: int, timeline_id: str, variant: FinalCutVariant) -> ArtifactKey:
    return ArtifactKey.episode_final_cut(episode, timeline_id, variant.narration, variant.subtitles)


def final_cut_artifact_path(episode: int, timeline_id: str, variant: FinalCutVariant) -> str:
    """成片的正式路径；每个产物身份只保留这一份最新文件。"""
    return f"{RENDERS_DIRNAME}/episode_{episode}/{timeline_id}/final_cut.{variant.slug}.mp4"


def video_resource_type_for(script_kind: str) -> str:
    return "reference_videos" if script_kind == "video_units" else "videos"


def output_profile_for_project(project: Mapping[str, Any], script_kind: str) -> OutputProfile:
    return output_profile_for_aspect_ratio(resolve_video_aspect_ratio(project, video_resource_type_for(script_kind)))


@dataclass(frozen=True, slots=True)
class CurrentVideo:
    """视频单元 current 视频的正式文件与内容指纹。"""

    resource_type: str
    unit_id: str
    version: int
    artifact_path: str
    content_digest: str


def current_video(
    project_dir: Path,
    versions: VersionManager,
    resource_type: str,
    unit_id: str,
    digest: Callable[[str], str],
) -> CurrentVideo | None:
    """可用视频：current 版本 > 0 且正式文件在场；stale 视频同样可用。"""
    version = versions.get_current_version(resource_type, unit_id)
    artifact_path = resource_relative_path(resource_type, unit_id)
    if version <= 0 or not (project_dir / artifact_path).is_file():
        return None
    return CurrentVideo(
        resource_type=resource_type,
        unit_id=unit_id,
        version=version,
        artifact_path=artifact_path,
        content_digest=digest(artifact_path),
    )


@dataclass(frozen=True, slots=True)
class ConsumedClip:
    clip: EditClip
    video: CurrentVideo


@dataclass(frozen=True, slots=True)
class FinalCutInputs:
    """一次渲染消费的全部输入；``missing_video_units`` 非空时渲染不成立。"""

    episode: int
    timeline_id: str
    revision: int
    variant: FinalCutVariant
    profile: OutputProfile
    clips: tuple[ConsumedClip, ...]
    missing_video_units: tuple[str, ...]


def resolve_final_cut_inputs(
    *,
    document: EditTimelineDocument,
    revision: TimelineRevision,
    variant: FinalCutVariant,
    profile: OutputProfile,
    script_unit_ids: Collection[str],
    video_of: Callable[[str], CurrentVideo | None],
) -> FinalCutInputs:
    """按修订的片段顺序解析每个剪辑片段用到的视频；视频单元已从脚本删除的片段跳过。"""
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
        },
        "trim": {"in_us": trim.in_us, "out_us": trim.out_us} if trim is not None else None,
        "source_volume": clip.source_volume,
        "hold_us": clip.hold_us,
        "transition_to_next": (
            {"type": transition.type.value, "duration_us": transition.duration_us} if transition is not None else None
        ),
    }


def final_cut_basis(inputs: FinalCutInputs) -> ArtifactBasis:
    """成片的生成依据；登记与时效比对都经这里构造。"""
    if inputs.missing_video_units:
        raise ValueError(f"final cut inputs lack usable videos: {', '.join(inputs.missing_video_units)}")
    return ArtifactBasis.build(
        FINAL_CUT_BASIS_KIND,
        kind_version=FINAL_CUT_BASIS_VERSION,
        inputs={
            "timeline_id": inputs.timeline_id,
            "revision": inputs.revision,
            "narration": inputs.variant.narration,
            "subtitles": inputs.variant.subtitles,
            "output": {"width": inputs.profile.width, "height": inputs.profile.height, "fps": inputs.profile.fps},
            "clips": [_clip_input(item) for item in inputs.clips],
        },
    )


__all__ = [
    "DEFAULT_VARIANT",
    "FINAL_CUT_BASIS_KIND",
    "FINAL_CUT_BASIS_VERSION",
    "NO_SUBTITLES",
    "SUPPORTED_VARIANTS",
    "WITHOUT_NARRATION",
    "ConsumedClip",
    "CurrentVideo",
    "FinalCutInputs",
    "FinalCutVariant",
    "current_video",
    "final_cut_artifact_path",
    "final_cut_basis",
    "final_cut_key",
    "output_profile_for_project",
    "resolve_final_cut_inputs",
    "video_resource_type_for",
]
