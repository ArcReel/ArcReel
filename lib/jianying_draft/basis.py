"""Jianying draft identities, formal paths, and generation bases (``docs/adr/0090``, ``docs/adr/0095``).

Identity combines episode + timeline + narration variant. Bases contain only draft inputs:
revision number and rendering-relevant revision content, aspect ratio, and presentation-model
bases for referenced unit material layers (including video versions/subtitle drafts, plus voiceovers
for narration variants). With BGM, add each referenced track’s fingerprint/static gain. Revision
numbers identify editing-decision snapshots; every new revision expires drafts exported from old
ones. Editing rationales do not separately enter bases.
Local draft directories/Jianying versions substitute only at download, outside bases. Registration
and comparison both construct through :func:`build_jianying_draft_basis`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.artifacts.rendered_artifact import timeline_renders_dir
from lib.bgm.library import BgmSource
from lib.edit_timeline.bgm import bgm_sources_input
from lib.edit_timeline.model import EditTimelineContent, TimelineRevision
from lib.infra.content_digest import canonical_json_bytes, prefixed
from lib.speech.narration_config import project_narration_delivery
from lib.speech.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.speech.speech_artifact_provenance import RenditionVariant
from lib.speech.speech_composition import SpeechMode

JIANYING_DRAFT_BASIS_KIND = "artifact-jianying-draft"
JIANYING_DRAFT_BASIS_VERSION = 1

type DraftNarration = Literal["without_narration", "with_narration"]
"""Jianying draft narration variant: the narrated variant adds a narration track and is available only for TTS-dubbed projects."""

WITHOUT_NARRATION: DraftNarration = "without_narration"
WITH_NARRATION: DraftNarration = "with_narration"


def default_draft_narration(project: Mapping[str, Any]) -> DraftNarration:
    """Default omitted narration variant: include narration for TTS-dubbing projects, omit it for post-production dubbing."""
    return WITH_NARRATION if project_narration_delivery(project) == USE_TTS else WITHOUT_NARRATION


def jianying_draft_key(episode: int, timeline_id: str, narration: DraftNarration) -> ArtifactKey:
    return ArtifactKey.episode_jianying_draft(episode, timeline_id, narration)


def jianying_draft_artifact_path(episode: int, timeline_id: str, narration: DraftNarration) -> str:
    """Formal Jianying draft artifact path; retain only the latest per identity."""
    return f"{timeline_renders_dir(episode, timeline_id)}/jianying_draft.{narration}.zip"


def timeline_render_fingerprint(content: EditTimelineContent) -> str:
    """Fingerprint rendering-relevant timeline revision content; omit editing rationales ignored by rendering."""
    payload = content.model_dump(mode="json")
    for clip in payload["clips"]:
        clip.pop("reason", None)
    return prefixed(hashlib.sha256(canonical_json_bytes(payload, allow_nan=False)).hexdigest())


def draft_unit_ids(content: EditTimelineContent, script_unit_ids: Iterable[str]) -> tuple[str, ...]:
    """Draft-referenced units in first-occurrence order; omit script-deleted units skipped by rendering."""
    in_script = set(script_unit_ids)
    return tuple(dict.fromkeys(clip.unit_id for clip in content.clips if clip.unit_id in in_script))


def effective_unit_variant(
    narration: DraftNarration, speech_mode: SpeechMode | None, *, has_narration_audio: bool
) -> RenditionVariant:
    """Actual unit presentation variant: narration applies only to voiceover units with existing recordings; all others stay without narration."""
    if narration == WITH_NARRATION and speech_mode is SpeechMode.NARRATOR_VOICEOVER and has_narration_audio:
        return USE_TTS
    return POST_PRODUCTION


@dataclass(frozen=True, slots=True)
class DraftUnitBasis:
    """Material-layer fingerprint for one referenced video unit.

    Typed-source videos use consumed presentation-model basis digests. Manual uploads have no
    presentation-model basis and use selected version/content digest. At comparison, unavailable
    current projections (video versions changed without materialized presentations, missing videos,
    etc.) cannot equal any registration; drafts read stale rather than losing registration.
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
    bgm_sources: Mapping[str, BgmSource] | None = None,
) -> ArtifactBasis:
    """``bgm_sources`` contains referenced BGM; revisions without BGM omit this basis item, matching BGM-free draft shapes."""
    inputs: dict[str, object] = {
        "timeline": {
            "id": timeline_id,
            "revision": revision.number,
            "content": timeline_render_fingerprint(revision.content),
        },
        "narration": narration,
        "aspect_ratio": aspect_ratio,
        "units": {unit.unit_id: unit.to_input() for unit in units},
    }
    if revision.content.bgm:
        inputs["bgm"] = bgm_sources_input(revision.content.bgm, bgm_sources or {})
    return ArtifactBasis.build(JIANYING_DRAFT_BASIS_KIND, kind_version=JIANYING_DRAFT_BASIS_VERSION, inputs=inputs)


__all__ = [
    "JIANYING_DRAFT_BASIS_KIND",
    "JIANYING_DRAFT_BASIS_VERSION",
    "WITHOUT_NARRATION",
    "WITH_NARRATION",
    "DraftNarration",
    "DraftUnitBasis",
    "build_jianying_draft_basis",
    "default_draft_narration",
    "draft_unit_ids",
    "effective_unit_variant",
    "jianying_draft_artifact_path",
    "jianying_draft_key",
    "timeline_render_fingerprint",
]
