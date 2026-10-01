"""剪映草稿产物的身份、落盘路径与生成依据（``docs/adr/0090``、``docs/adr/0095``）。

产物身份是「集 + 剪辑时间线 + 旁白版本」。生成依据只收录草稿实际消费的内容：剪辑时间线的修订号与修订里
参与渲染的部分、画幅，以及每个被引用视频单元作为素材层的呈现模型依据（其中已含视频版本、字幕草稿，
带旁白版本还含旁白配音）。修订号标识本次剪辑决策快照，任何新修订都让旧修订导出的草稿过期；剪辑理由不单独进入依据。
本机草稿目录与剪映版本在下载时才代入，不进依据。登记与比对都经 :func:`build_jianying_draft_basis` 构造依据。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.artifacts.rendered_artifact import timeline_renders_dir
from lib.edit_timeline.model import EditTimelineContent, TimelineRevision
from lib.infra.content_digest import canonical_json_bytes, prefixed
from lib.speech.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.speech.speech_artifact_provenance import RenditionVariant
from lib.speech.speech_composition import SpeechMode

JIANYING_DRAFT_BASIS_KIND = "artifact-jianying-draft"
JIANYING_DRAFT_BASIS_VERSION = 1

type DraftNarration = Literal["without_narration", "with_narration"]
"""剪映草稿的旁白版本：带旁白版本多一条旁白轨，只对 TTS 配音项目开放。"""

WITHOUT_NARRATION: DraftNarration = "without_narration"
WITH_NARRATION: DraftNarration = "with_narration"


def jianying_draft_key(episode: int, timeline_id: str, narration: DraftNarration) -> ArtifactKey:
    return ArtifactKey.episode_jianying_draft(episode, timeline_id, narration)


def jianying_draft_artifact_path(episode: int, timeline_id: str, narration: DraftNarration) -> str:
    """剪映草稿产物的正式路径；每个产物身份只保留最新一份。"""
    return f"{timeline_renders_dir(episode, timeline_id)}/jianying_draft.{narration}.zip"


def timeline_render_fingerprint(content: EditTimelineContent) -> str:
    """剪辑时间线修订里参与渲染部分的指纹；剪辑理由渲染时忽略，不计入。"""
    payload = content.model_dump(mode="json")
    for clip in payload["clips"]:
        clip.pop("reason", None)
    return prefixed(hashlib.sha256(canonical_json_bytes(payload, allow_nan=False)).hexdigest())


def draft_unit_ids(content: EditTimelineContent, script_unit_ids: Iterable[str]) -> tuple[str, ...]:
    """草稿引用的视频单元，按首次出现的顺序；已从脚本删除的单元渲染时跳过，不计入。"""
    in_script = set(script_unit_ids)
    return tuple(dict.fromkeys(clip.unit_id for clip in content.clips if clip.unit_id in in_script))


def effective_unit_variant(
    narration: DraftNarration, speech_mode: SpeechMode | None, *, has_narration_audio: bool
) -> RenditionVariant:
    """视频单元实际取用的呈现版本：带旁白版本只作用于已有旁白配音的画外音单元，其余单元保持不带旁白。"""
    if narration == WITH_NARRATION and speech_mode is SpeechMode.NARRATOR_VOICEOVER and has_narration_audio:
        return USE_TTS
    return POST_PRODUCTION


@dataclass(frozen=True, slots=True)
class DraftUnitBasis:
    """一个被引用视频单元的素材层指纹。

    有类型化来源的视频取所用呈现模型的依据摘要；手动上传的视频没有呈现模型依据，取选中版本号与内容摘要。
    比对时当前素材层投影不出（视频已换版本而尚未物化呈现模型、视频不在等）记为不可用：这样的依据不会
    与任何登记相等，草稿读作过期而不是失去登记。
    """

    unit_id: str
    presentation_digest: str | None = None
    manual_upload: tuple[int, str] | None = None
    unavailable: bool = False

    def __post_init__(self) -> None:
        forms = (self.presentation_digest is not None, self.manual_upload is not None, self.unavailable)
        if sum(forms) != 1:
            raise ValueError(
                "a draft unit basis needs exactly one of presentation_digest, manual_upload or unavailable"
            )

    def to_input(self) -> dict[str, object]:
        if self.presentation_digest is not None:
            return {"presentation": self.presentation_digest}
        if self.manual_upload is not None:
            version, content_digest = self.manual_upload
            return {"manual_upload": {"version": version, "content_digest": content_digest}}
        return {"unavailable": True}


def build_jianying_draft_basis(
    *,
    timeline_id: str,
    revision: TimelineRevision,
    narration: DraftNarration,
    aspect_ratio: str,
    units: Sequence[DraftUnitBasis],
) -> ArtifactBasis:
    return ArtifactBasis.build(
        JIANYING_DRAFT_BASIS_KIND,
        kind_version=JIANYING_DRAFT_BASIS_VERSION,
        inputs={
            "timeline": {
                "id": timeline_id,
                "revision": revision.number,
                "content": timeline_render_fingerprint(revision.content),
            },
            "narration": narration,
            "aspect_ratio": aspect_ratio,
            "units": {unit.unit_id: unit.to_input() for unit in units},
        },
    )


__all__ = [
    "JIANYING_DRAFT_BASIS_KIND",
    "JIANYING_DRAFT_BASIS_VERSION",
    "WITHOUT_NARRATION",
    "WITH_NARRATION",
    "DraftNarration",
    "DraftUnitBasis",
    "build_jianying_draft_basis",
    "draft_unit_ids",
    "effective_unit_variant",
    "jianying_draft_artifact_path",
    "jianying_draft_key",
    "timeline_render_fingerprint",
]
