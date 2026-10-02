"""项目级 BGM 库的存储形态、产物身份与生成依据（``docs/adr/0090``）。

每首 BGM 的元数据记在 ``project.json`` 的 ``bgm`` 下，键是 BGM ID，值是名称、文件、时长、实测响度与折算增益；
文件放在 ``bgm/<BGM ID><扩展名>``，上传后不再改写。产物身份是 BGM ID，生成依据只有文件字节的内容指纹：
字节不变就一直是 current，被替换了就读作过期。

响度按 EBU R128 积分响度在登记时实测一次，折算成把它统一到 :data:`TARGET_LOUDNESS_LUFS` 的静态增益缓存下来；
成片与剪映草稿里 BGM 片段的实际音量是这个增益乘以片段音量。
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
"""``project.json`` 里 BGM 库所在的字段。"""

BGM_ID_PATTERN = r"^bgm-[0-9a-f]{8}$"
BGM_EXTENSIONS = (".mp3", ".wav", ".m4a")
BGM_MAX_BYTES = 100 * 1024 * 1024
BGM_NAME_MAX_LENGTH = 80

TARGET_LOUDNESS_LUFS = -16.0
"""BGM 统一到的积分响度。"""

SILENCE_LOUDNESS_LUFS = -70.0
"""EBU R128 的绝对门限；积分响度不高于它说明整首几乎无声，测不出可用的增益。"""

BGM_BASIS_KIND = "project-bgm"
BGM_BASIS_VERSION = 1

_BGM_ID_RE = re.compile(BGM_ID_PATTERN)


class BgmTrack(BaseModel):
    """一首已登记的 BGM。``gain_db`` 是把实测积分响度统一到 −16 LUFS 的静态增益。"""

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
        """静态增益的线性倍数。"""
        return 10 ** (self.gain_db / 20)

    @property
    def duration(self) -> float:
        return round(self.duration_us / 1_000_000, 3)


def is_bgm_id(value: object) -> bool:
    return isinstance(value, str) and _BGM_ID_RE.fullmatch(value) is not None


def bgm_file_path(bgm_id: str, extension: str) -> str:
    return f"{BGM_DIR}/{bgm_id}{extension}"


def loudness_gain_db(integrated_lufs: float) -> float:
    """把实测积分响度统一到 :data:`TARGET_LOUDNESS_LUFS` 的增益（dB），保留两位小数。"""
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
    """``project.json`` 里登记的 BGM，按上传先后排列；形态不对的条目不计。"""
    bucket = project.get(PROJECT_BGM_FIELD)
    if not isinstance(bucket, Mapping):
        return {}
    tracks = (_parse_track(bgm_id, raw) for bgm_id, raw in bucket.items())
    return {track.id: track for track in sorted((t for t in tracks if t is not None), key=lambda t: t.uploaded_at)}


def bgm_entry(track: BgmTrack) -> dict[str, object]:
    """写进 ``project.json`` 的形态：ID 是键，不重复存。"""
    return track.model_dump(mode="json", exclude={"id"})


def bgm_key(bgm_id: str) -> ArtifactKey:
    return ArtifactKey.project_bgm(bgm_id)


def bgm_basis(content_digest: str) -> ArtifactBasis:
    """BGM 的生成依据只有上传字节的内容指纹；登记与比对都经这里构造。"""
    return ArtifactBasis.build(BGM_BASIS_KIND, kind_version=BGM_BASIS_VERSION, inputs={"content": content_digest})


@dataclass(frozen=True, slots=True)
class BgmSource:
    """渲染读取的一首 BGM：项目内路径、正式文件当前字节的内容指纹，以及登记时缓存的静态增益。"""

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
    """``bgm_ids`` 里仍在项目里、文件在场的 BGM；``digest`` 按项目内路径取正式文件的内容指纹。"""
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
