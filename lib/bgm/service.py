"""BGM 命令：上传登记与列出；HTTP 与 Agent 工具共用。

上传时先把字节写进 ``bgm/`` 下的隐藏暂存文件，探测出音频时长、实测积分响度后，在 ``project.json`` 的同一次
原子更新里落正式文件、写元数据并按字节登记产物；任何一步失败，正式文件、元数据与登记都不留下。
上传后的 BGM 不能替换或删除。
"""

from __future__ import annotations

import asyncio
import secrets
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.artifact_registration import register_current_artifact
from lib.bgm.library import (
    BGM_DIR,
    BGM_EXTENSIONS,
    BGM_MAX_BYTES,
    BGM_NAME_MAX_LENGTH,
    PROJECT_BGM_FIELD,
    SILENCE_LOUDNESS_LUFS,
    BgmTrack,
    bgm_basis,
    bgm_entry,
    bgm_file_path,
    bgm_key,
    loudness_gain_db,
    read_bgm_library,
)
from lib.bgm.loudness import LoudnessMeasurementError, measure_integrated_loudness
from lib.infra.content_digest import sha256_file
from lib.infra.ffmpeg import FfmpegUnavailableError, ffmpeg_executable
from lib.infra.media_probe import MediaProbeError, probe_media
from lib.infra.subprocess_deadline import Spawner
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager

type BgmErrorCode = Literal[
    "project_not_found",
    "bgm_unsupported_type",
    "bgm_too_large",
    "bgm_invalid_audio",
    "bgm_silent",
    "bgm_ffmpeg_unavailable",
]


class BgmError(Exception):
    """BGM 上传无法完成；``code`` 供适配器映射，``params`` 携带定位信息。"""

    def __init__(self, code: BgmErrorCode, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code: BgmErrorCode = code
        self.params = params


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def bgm_display_name(filename: str) -> str:
    """BGM 的显示名取上传文件名去掉扩展名，过长截断；取不出时为「BGM」。"""
    stem = unicodedata.normalize("NFC", Path(filename).stem).strip()
    return stem[:BGM_NAME_MAX_LENGTH].strip() or "BGM"


class BgmLibraryService:
    def __init__(self, projects: ProjectManager, *, spawn: Spawner | None = None) -> None:
        self._projects = projects
        self._spawn = spawn

    def _project_dir(self, project_name: str) -> Path:
        if not self._projects.project_exists(project_name):
            raise BgmError("project_not_found", f"项目「{project_name}」不存在", project=project_name)
        return self._projects.get_project_path(project_name)

    async def list(self, project_name: str) -> tuple[BgmTrack, ...]:
        """项目里已上传的 BGM，按上传先后排列。"""
        self._project_dir(project_name)
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        return tuple(read_bgm_library(project).values())

    async def upload(self, project_name: str, *, filename: str, content: bytes) -> BgmTrack:
        """登记一首 BGM：校验格式与大小，探测时长，实测积分响度并折算增益，原子落盘并按字节登记。"""
        project_dir = self._project_dir(project_name)
        extension = Path(filename).suffix.lower()
        if extension not in BGM_EXTENSIONS:
            raise BgmError(
                "bgm_unsupported_type",
                f"BGM 只支持 {', '.join(BGM_EXTENSIONS)}，收到 {extension or '无扩展名'}",
                ext=extension,
                allowed=", ".join(BGM_EXTENSIONS),
            )
        if len(content) > BGM_MAX_BYTES:
            raise BgmError("bgm_too_large", "BGM 文件过大", max_mb=BGM_MAX_BYTES // (1024 * 1024))
        try:
            ffmpeg = await asyncio.to_thread(ffmpeg_executable)
        except FfmpegUnavailableError as exc:
            raise BgmError("bgm_ffmpeg_unavailable", f"随包 ffmpeg 不可用，无法测量 BGM 响度：{exc}") from exc
        bgm_dir = project_dir / BGM_DIR
        bgm_id = f"bgm-{secrets.token_hex(4)}"
        staged = bgm_dir / f".{bgm_id}.{secrets.token_hex(4)}.upload{extension}"
        await asyncio.to_thread(bgm_dir.mkdir, exist_ok=True)
        try:
            await asyncio.to_thread(staged.write_bytes, content)
            duration_us = await self._duration_us(staged)
            integrated = await self._loudness(ffmpeg, staged, duration_us)
            track = BgmTrack(
                id=bgm_id,
                name=bgm_display_name(filename),
                file=bgm_file_path(bgm_id, extension),
                duration_us=duration_us,
                integrated_lufs=round(integrated, 2),
                gain_db=loudness_gain_db(integrated),
                content_digest=await asyncio.to_thread(sha256_file, staged),
                uploaded_at=_utc_now(),
            )
            await asyncio.to_thread(self._commit, project_name, project_dir, staged, track)
        finally:
            await asyncio.to_thread(staged.unlink, missing_ok=True)
        return track

    async def _duration_us(self, path: Path) -> int:
        try:
            probe = await probe_media(path, spawn=self._spawn)
        except MediaProbeError as exc:
            raise BgmError("bgm_invalid_audio", f"BGM 文件无法解析：{exc}") from exc
        audio = probe.first_stream("audio")
        duration = audio.duration_seconds if audio is not None else None
        if duration is None or duration <= 0:
            raise BgmError("bgm_invalid_audio", "BGM 文件里没有可用的音频")
        return round(duration * 1_000_000)

    async def _loudness(self, ffmpeg: str, path: Path, duration_us: int) -> float:
        try:
            integrated = await measure_integrated_loudness(
                ffmpeg, path, duration_seconds=duration_us / 1_000_000, spawn=self._spawn
            )
        except LoudnessMeasurementError as exc:
            raise BgmError("bgm_invalid_audio", f"BGM 的响度测不出来：{exc}") from exc
        if integrated <= SILENCE_LOUDNESS_LUFS:
            raise BgmError("bgm_silent", "BGM 几乎没有声音，测不出可用的响度")
        return integrated

    def _commit(self, project_name: str, project_dir: Path, staged: Path, track: BgmTrack) -> None:
        target = project_dir / track.file

        def mutate(project: dict) -> None:
            bucket = project.get(PROJECT_BGM_FIELD)
            if not isinstance(bucket, dict):
                bucket = project[PROJECT_BGM_FIELD] = {}
            if track.id in bucket:
                raise RuntimeError(f"BGM ID 已被占用：{track.id}")
            bucket[track.id] = bgm_entry(track)

        def install(_project_file: Path) -> None:
            staged.replace(target)
            register_current_artifact(
                project_dir, bgm_key(track.id), artifact_path=track.file, basis=bgm_basis(track.content_digest)
            )

        with project_change_source("webui"):
            self._projects.update_project(project_name, mutate, on_commit=install, formal_paths=(target,))


__all__ = ["BgmError", "BgmErrorCode", "BgmLibraryService", "bgm_display_name"]
