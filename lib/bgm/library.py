"""Project BGM storage, artifact identities, and generation bases (``docs/adr/0090``).

Each track’s metadata lives under ``project.json`` ``bgm``, keyed by BGM ID, storing name, file,
duration, measured loudness, and converted gain. Files live at ``bgm/<BGM ID><扩展名>`` and are never
rewritten after upload. Artifact identity is the BGM ID; its generation basis contains only the
file-byte content fingerprint: unchanged bytes stay current; replacements read as stale.

Measure EBU R128 integrated loudness once at registration, convert it into static gain targeting
:data:`TARGET_LOUDNESS_LUFS`, and cache it. BGM clip volume in final cuts and Jianying drafts
is that gain multiplied by clip volume.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey

BGM_DIR = "bgm"
PROJECT_BGM_FIELD = "bgm"
"""Field containing the BGM library in ``project.json``."""

BGM_ID_PATTERN = r"^bgm-[0-9a-f]{8}$"
BGM_EXTENSIONS = (".mp3", ".wav", ".m4a")
BGM_MAX_BYTES = 100 * 1024 * 1024
BGM_NAME_MAX_LENGTH = 80

TARGET_LOUDNESS_LUFS = -16.0
"""Target integrated loudness for BGM."""

SILENCE_LOUDNESS_LUFS = -70.0
"""EBU R128 absolute gate; integrated loudness at/below it means near-silence throughout, with no usable gain measurement."""

BGM_BASIS_KIND = "project-bgm"
BGM_BASIS_VERSION = 1

_BGM_ID_RE = re.compile(BGM_ID_PATTERN)


class BgmTrack(BaseModel):
    """One registered BGM track. ``gain_db`` is the static gain normalizing measured integrated loudness to −16 LUFS."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(pattern=BGM_ID_PATTERN)
    name: str = Field(min_length=1, max_length=BGM_NAME_MAX_LENGTH)
    file: str = Field(min_length=1)
    duration_us: int = Field(gt=0, strict=True)
    integrated_lufs: float
    gain_db: float
    content_digest: str = Field(min_length=1)
    uploaded_at: str

    @property
    def gain(self) -> float:
        """Static gain as a linear multiplier."""
        return 10 ** (self.gain_db / 20)

    @property
    def duration(self) -> float:
        return round(self.duration_us / 1_000_000, 3)


def is_bgm_id(value: object) -> bool:
    return isinstance(value, str) and _BGM_ID_RE.fullmatch(value) is not None


def bgm_file_path(bgm_id: str, extension: str) -> str:
    return f"{BGM_DIR}/{bgm_id}{extension}"


def loudness_gain_db(integrated_lufs: float) -> float:
    """Gain (dB) normalizing measured integrated loudness to :data:`TARGET_LOUDNESS_LUFS`, rounded to two decimals."""
    return round(TARGET_LOUDNESS_LUFS - integrated_lufs, 2)


def _parse_track(bgm_id: object, raw: object) -> BgmTrack | None:
    if not is_bgm_id(bgm_id) or not isinstance(raw, Mapping):
        return None
    try:
        track = BgmTrack.model_validate({**raw, "id": bgm_id})
    except ValidationError:
        return None
    if track.file not in {bgm_file_path(track.id, extension) for extension in BGM_EXTENSIONS}:
        return None
    return track


def read_bgm_library(project: Mapping[str, Any]) -> dict[str, BgmTrack]:
    """Registered ``project.json`` BGM in upload order; omit malformed entries."""
    bucket = project.get(PROJECT_BGM_FIELD)
    if not isinstance(bucket, Mapping):
        return {}
    tracks = (_parse_track(bgm_id, raw) for bgm_id, raw in bucket.items())
    return {track.id: track for track in sorted((t for t in tracks if t is not None), key=lambda t: t.uploaded_at)}


def bgm_entry(track: BgmTrack) -> dict[str, object]:
    """Shape written into ``project.json``: ID is the key, not stored redundantly."""
    return track.model_dump(mode="json", exclude={"id"})


def bgm_key(bgm_id: str) -> ArtifactKey:
    return ArtifactKey.project_bgm(bgm_id)


def bgm_basis(content_digest: str) -> ArtifactBasis:
    """BGM generation bases contain only uploaded-byte content fingerprints; registration/comparison both build them here."""
    return ArtifactBasis.build(BGM_BASIS_KIND, kind_version=BGM_BASIS_VERSION, inputs={"content": content_digest})


@dataclass(frozen=True, slots=True)
class BgmSource:
    """One rendering BGM source: project-relative path, current formal-file byte fingerprint, and registration-cached static gain."""

    bgm_id: str
    path: str
    content_digest: str
    gain_db: float

    @property
    def gain(self) -> float:
        return 10 ** (self.gain_db / 20)

    def to_input(self) -> dict[str, object]:
        return {"content_digest": self.content_digest, "gain_db": self.gain_db}


def resolve_bgm_sources(
    project_dir: Path, project: Mapping[str, Any], bgm_ids: Collection[str], digest: Callable[[str], str]
) -> dict[str, BgmSource]:
    """BGM in ``bgm_ids`` still present in the project/on disk; ``digest`` returns formal-file fingerprints by project-relative path."""
    library = read_bgm_library(project)
    sources: dict[str, BgmSource] = {}
    for bgm_id in bgm_ids:
        track = library.get(bgm_id)
        if track is None or not (project_dir / track.file).is_file():
            continue
        sources[bgm_id] = BgmSource(
            bgm_id=bgm_id, path=track.file, content_digest=digest(track.file), gain_db=track.gain_db
        )
    return sources


__all__ = [
    "BGM_BASIS_KIND",
    "BGM_BASIS_VERSION",
    "BGM_DIR",
    "BGM_EXTENSIONS",
    "BGM_ID_PATTERN",
    "BGM_MAX_BYTES",
    "BGM_NAME_MAX_LENGTH",
    "PROJECT_BGM_FIELD",
    "SILENCE_LOUDNESS_LUFS",
    "TARGET_LOUDNESS_LUFS",
    "BgmSource",
    "BgmTrack",
    "bgm_basis",
    "bgm_entry",
    "bgm_file_path",
    "bgm_key",
    "is_bgm_id",
    "loudness_gain_db",
    "read_bgm_library",
    "resolve_bgm_sources",
]
