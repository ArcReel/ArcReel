"""成片的产物身份与生成依据。

产物身份是「集 + 剪辑时间线 + 旁白版本 + 是否烧入字幕」。生成依据只收录渲染实际消费的内容：
剪辑时间线修订里影响画面与声音的部分（片段顺序、生效的截取、原声音量、定格延长、转场），
各片段所用视频单元 current 视频的版本、内容指纹与供应商原声开关，以及输出画布。修订号标识本次剪辑决策快照；剪辑理由不单独进入依据；
截取所依据的版本已不是 current 时截取被忽略，依据里也记为整段使用。

剪辑时间线有 BGM 时，依据另收 BGM 片段与所引用每首 BGM 的内容指纹和静态增益；没有 BGM 的依据形态不变。

带旁白或烧入字幕的版本还消费各视频单元的素材层（与剪映草稿同源，见 :mod:`lib.jianying_draft.placement`），
依据另收各单元的素材层指纹：其中含字幕草稿，带旁白版本还含旁白配音。不带旁白、不烧入字幕的版本不收，
旁白配音与字幕草稿的变化不让它过期。

渲染任务开始时按指定修订取依据快照，产物时效判定按最新修订重建依据，两处共用
:func:`resolve_final_cut_inputs` 与 :func:`final_cut_basis`。
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.artifacts.rendered_artifact import timeline_renders_dir
from lib.artifacts.version_manager import UnmanagedSnapshotPathError, VersionManager
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
"""成片的字幕方式：烧入字幕，或不烧入、得到干净的画面。"""

WITHOUT_NARRATION: DraftNarration = "without_narration"
WITH_NARRATION: DraftNarration = "with_narration"
NO_SUBTITLES: SubtitleMode = "no_subtitles"
BURNED_SUBTITLES: SubtitleMode = "burned_subtitles"


@dataclass(frozen=True, slots=True)
class FinalCutVariant:
    """成片的旁白版本与字幕方式；旁白版本的取值与剪映草稿相同。"""

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
        """带旁白或烧入字幕的版本要取各视频单元的素材层（旁白配音与字幕）。"""
        return self.with_narration or self.burns_subtitles


FINAL_CUT_VARIANTS = tuple(
    FinalCutVariant(narration, subtitles)
    for narration in (WITHOUT_NARRATION, WITH_NARRATION)
    for subtitles in (BURNED_SUBTITLES, NO_SUBTITLES)
)
"""成片的全部产物身份组合；带旁白版本只对 TTS 配音项目开放。"""


def resolve_final_cut_variant(
    project: Mapping[str, Any], *, narration: DraftNarration | None = None, subtitles: SubtitleMode | None = None
) -> FinalCutVariant:
    """补齐省略的选项：旁白版本 TTS 配音项目默认带旁白、其余不带旁白（与剪映草稿相同），字幕默认烧入。"""
    return FinalCutVariant(narration or default_draft_narration(project), subtitles or BURNED_SUBTITLES)


def final_cut_key(episode: int, timeline_id: str, variant: FinalCutVariant) -> ArtifactKey:
    return ArtifactKey.episode_final_cut(episode, timeline_id, variant.narration, variant.subtitles)


def final_cut_artifact_path(episode: int, timeline_id: str, variant: FinalCutVariant) -> str:
    """成片的正式路径；每个产物身份只保留这一份最新文件。"""
    return f"{timeline_renders_dir(episode, timeline_id)}/final_cut.{variant.slug}.mp4"


def video_resource_type_for(script_kind: str) -> str:
    return "reference_videos" if script_kind == "video_units" else "videos"


def output_profile_for_project(project: Mapping[str, Any], script_kind: str) -> OutputProfile:
    return output_profile_for_aspect_ratio(resolve_video_aspect_ratio(project, video_resource_type_for(script_kind)))


@dataclass(frozen=True, slots=True)
class CurrentVideo:
    """视频单元 current 视频的正式文件，以及渲染读取的该版本快照文件与快照的内容指纹。

    ``provider_audio`` 取自该版本记录的供应商原声开关：记录为未生成原声时，渲染不使用快照里的音轨。
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
    """可用视频：current 版本 > 0、正式文件与该版本快照都在场；stale 视频同样可用。

    渲染读取版本快照而不是正式文件：正式文件会被新生成或版本恢复替换，快照不会。内容指纹也取自快照，
    依据描述的就是渲染实际读取的字节。
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
        content_digest=digest(snapshot.relative_to(project_dir).as_posix()),
        snapshot=snapshot,
        provider_audio=provider_audio_recorded(record),
    )


def provider_audio_recorded(record: Mapping[str, Any]) -> bool:
    """版本记录的供应商原声开关：只有明确记录为未生成原声才为 False，没有记录按带原声处理。"""
    return record.get("execution_generate_audio") is not False


def current_provider_audio(versions: VersionManager, resource_type: str, unit_id: str) -> bool:
    """视频单元 current 版本的供应商原声开关；没有版本记录时按带原声处理。"""
    info = versions.get_versions(resource_type, unit_id)
    current = info["current_version"]
    for record in info["versions"]:
        if record.get("version") == current:
            return provider_audio_recorded(record)
    return True


def version_snapshot(
    project_dir: Path, versions: VersionManager, resource_type: str, unit_id: str, version: int
) -> tuple[Path, Mapping[str, Any]] | None:
    """指定视频版本的快照文件与版本记录；没有该版本、快照路径不受管或文件不在时为 None。"""
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
    """一次渲染消费的全部输入；``missing_video_units`` 非空时渲染不成立。"""

    episode: int
    timeline_id: str
    revision: int
    variant: FinalCutVariant
    profile: OutputProfile
    clips: tuple[ConsumedClip, ...]
    missing_video_units: tuple[str, ...]
    units: tuple[DraftUnitBasis, ...] = ()
    """各视频单元的素材层指纹；只在 :attr:`FinalCutVariant.consumes_unit_materials` 时收录。"""
    bgm: tuple[BgmClip, ...] = ()
    bgm_sources: Mapping[str, BgmSource] = field(default_factory=dict)
    """BGM 轨所引用的 BGM；不在项目里或文件已不在的不在其中。"""


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
    """按修订的片段顺序解析每个剪辑片段用到的视频；视频单元已从脚本删除的片段跳过。

    ``bgm_sources`` 是 BGM 轨所引用的 BGM（:func:`~lib.bgm.library.resolve_bgm_sources`）。
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
    """成片的生成依据；登记与时效比对都经这里构造。"""
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
