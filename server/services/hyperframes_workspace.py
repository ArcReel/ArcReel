"""Project-scoped HyperFrames authoring workspaces and Studio processes."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import shlex
import shutil
import socket
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit, urlunsplit

from lib.json_io import atomic_write_json
from lib.narration_delivery import POST_PRODUCTION
from lib.path_safety import safe_join
from lib.project_manager import ProjectManager
from lib.speech_artifact_provenance import RenditionVariant
from server.services.presentation_read_model import MaterializedEpisode, PresentationReadModelService

logger = logging.getLogger(__name__)

HYPERFRAMES_VERSION = "0.8.10"
WORKSPACE_SCHEMA_VERSION = 1
_READY_TIMEOUT_SECONDS = 60.0
_DEFAULT_PORT_START = 12500
_DEFAULT_PORT_COUNT = 50


class HyperframesWorkspaceUnavailable(ValueError):
    """The episode cannot currently be materialized into an editing workspace."""


class HyperframesStudioUnavailable(RuntimeError):
    """The official HyperFrames Studio process could not be started."""


@dataclass(frozen=True, slots=True)
class HyperframesWorkspace:
    project_name: str
    episode: int
    path: Path
    relative_path: str
    composition_path: str
    manifest_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "project_name": self.project_name,
            "episode": self.episode,
            "exists": True,
            "workspace_path": self.relative_path,
            "composition_path": self.composition_path,
            "manifest_path": self.manifest_path,
        }


@dataclass(slots=True)
class _StudioProcess:
    workspace: Path
    port: int
    process: asyncio.subprocess.Process
    log_handle: BinaryIO


def _episode_dir_name(episode: int) -> str:
    if isinstance(episode, bool) or not isinstance(episode, int) or episode <= 0:
        raise ValueError("episode must be a positive integer")
    return f"episode_{episode:02d}"


def _workspace(project_dir: Path, project_name: str, episode: int) -> HyperframesWorkspace:
    relative = Path("hyperframes") / _episode_dir_name(episode)
    path = safe_join(project_dir, relative)
    return HyperframesWorkspace(
        project_name=project_name,
        episode=episode,
        path=path,
        relative_path=relative.as_posix(),
        composition_path=(relative / "index.html").as_posix(),
        manifest_path=(relative / "manifest.json").as_posix(),
    )


def _canvas_size(project: object) -> tuple[int, int]:
    raw = project.get("aspect_ratio") if isinstance(project, Mapping) else None
    if isinstance(raw, Mapping):
        raw = raw.get("video") or raw.get("storyboards")
    if not isinstance(raw, str):
        raw = "9:16"
    try:
        width_part, height_part = raw.replace("*", ":").split(":", maxsplit=1)
        ratio = float(width_part) / float(height_part)
    except (TypeError, ValueError, ZeroDivisionError):
        ratio = 9 / 16
    if ratio > 1.1:
        return 1920, 1080
    if ratio < 0.9:
        return 1080, 1920
    return 1080, 1080


def _stage_media(source: Path, media_dir: Path, name: str) -> Path:
    suffix = source.suffix.lower() or ".bin"
    destination = safe_join(media_dir, f"{name}{suffix}")
    # A Studio edit must never mutate the paid source artifact through a shared inode.
    shutil.copy2(source, destination)
    return destination


def _attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def _composition_html(materialized: MaterializedEpisode, staged: list[dict[str, object]]) -> str:
    width, height = _canvas_size(materialized.project_snapshot)
    cursor = 0.0
    clips: list[str] = []
    captions: list[str] = []

    for index, (value, media) in enumerate(zip(materialized.presentations, staged, strict=True)):
        presentation = value.presentation
        duration = presentation.video.duration_microseconds / 1_000_000
        video_src = _attr(media["video"])
        unit_id = _attr(presentation.unit_id)
        clips.append(
            f'''    <video id="video-{index}" class="clip" data-unit-id="{unit_id}" data-start="{cursor:.6f}" data-duration="{duration:.6f}" data-track-index="0" src="{video_src}" muted playsinline></video>'''
        )
        if presentation.video.audio_enabled:
            clips.append(
                f'''    <audio id="provider-audio-{index}" data-start="{cursor:.6f}" data-duration="{duration:.6f}" data-track-index="1" data-volume="{presentation.video.gain:.3f}" src="{video_src}"></audio>'''
            )
        if presentation.narration_audio is not None and media.get("narration_audio"):
            narration = presentation.narration_audio
            narration_duration = narration.duration_microseconds / 1_000_000
            clips.append(
                f'''    <audio id="narration-{index}" data-audio-group="voiceover" data-start="{cursor:.6f}" data-duration="{narration_duration:.6f}" data-track-index="2" data-volume="{narration.gain:.3f}" src="{_attr(media["narration_audio"])}"></audio>'''
            )
        for cue_index, cue in enumerate(presentation.subtitles):
            cue_start = cursor + cue.start_microseconds / 1_000_000
            cue_duration = cue.duration_microseconds / 1_000_000
            captions.append(
                f'''    <div id="caption-{index}-{cue_index}" class="clip caption" data-start="{cue_start:.6f}" data-duration="{cue_duration:.6f}" data-track-index="3">{html.escape(cue.text)}</div>'''
            )
        cursor += duration

    total_duration = max(cursor, 0.001)
    title = html.escape(str(materialized.project_snapshot.get("title") or "ArcReel"))
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; overflow: hidden; background: #0b0f14; }}
    [data-composition-id="arcreel-episode"] {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: #0b0f14; color: #f4f7f8; font-family: sans-serif; }}
    video.clip {{ width: 100%; height: 100%; object-fit: cover; }}
    .caption {{ position: absolute; left: 9%; right: 9%; bottom: 8%; padding: 18px 28px; box-sizing: border-box; border-radius: 16px; background: rgba(4, 8, 12, 0.72); color: #f7faf9; font-size: 42px; font-weight: 650; line-height: 1.35; text-align: center; text-shadow: 0 2px 10px rgba(0, 0, 0, 0.65); backdrop-filter: blur(10px); }}
  </style>
</head>
<body>
  <div id="arcreel-episode" data-composition-id="arcreel-episode" data-no-timeline data-start="0" data-duration="{total_duration:.6f}" data-width="{width}" data-height="{height}">
{os.linesep.join(clips)}
{os.linesep.join(captions)}
  </div>
</body>
</html>
'''


class HyperframesWorkspaceService:
    """Build a deterministic first edit without writing outside the ArcReel project."""

    def __init__(
        self,
        project_manager: ProjectManager,
        *,
        presentation_reader: PresentationReadModelService | None = None,
    ) -> None:
        self._pm = project_manager
        self._reader = presentation_reader or PresentationReadModelService(project_manager)

    def status(self, project_name: str, episode: int) -> HyperframesWorkspace | None:
        project_dir = self._pm.get_project_path(project_name)
        workspace = _workspace(project_dir, project_name, episode)
        if not safe_join(workspace.path, "index.html").is_file():
            return None
        if not safe_join(workspace.path, "manifest.json").is_file():
            return None
        return workspace

    async def prepare(
        self,
        project_name: str,
        episode: int,
        *,
        variant: RenditionVariant = POST_PRODUCTION,
    ) -> HyperframesWorkspace:
        existing = await asyncio.to_thread(self.status, project_name, episode)
        if existing is not None:
            return existing

        materialized = await self._reader.materialize_episode(
            project_name=project_name,
            episode=episode,
            variant=variant,
        )
        if not materialized.presentations:
            raise HyperframesWorkspaceUnavailable("episode has no completed video presentations")
        return await asyncio.to_thread(
            self._write_workspace,
            project_name,
            episode,
            materialized,
            variant,
        )

    def _write_workspace(
        self,
        project_name: str,
        episode: int,
        materialized: MaterializedEpisode,
        variant: RenditionVariant,
    ) -> HyperframesWorkspace:
        project_dir = self._pm.get_project_path(project_name)
        workspace = _workspace(project_dir, project_name, episode)
        hyperframes_root = safe_join(project_dir, "hyperframes")
        hyperframes_root.mkdir(parents=True, exist_ok=True)
        with self._pm.file_lock(workspace.path):
            if workspace.path.exists():
                existing = self.status(project_name, episode)
                if existing is not None:
                    return existing
                raise HyperframesWorkspaceUnavailable(
                    f"incomplete HyperFrames workspace already exists: {workspace.relative_path}"
                )

            staging = safe_join(hyperframes_root, f".{workspace.path.name}.{uuid.uuid4().hex}.tmp")
            staging.mkdir(parents=False)
            try:
                media_dir = safe_join(staging, "media")
                media_dir.mkdir()
                safe_join(staging, "renders").mkdir()
                staged: list[dict[str, object]] = []
                manifest_units: list[dict[str, object]] = []
                for index, value in enumerate(materialized.presentations):
                    presentation = value.presentation
                    video_source = safe_join(
                        project_dir,
                        presentation.video.media.artifact_path,
                        require_file=True,
                    )
                    video_target = _stage_media(video_source, media_dir, f"{index:03d}-video")
                    narration_target: Path | None = None
                    if presentation.narration_audio is not None:
                        narration_source = safe_join(
                            project_dir,
                            presentation.narration_audio.media.artifact_path,
                            require_file=True,
                        )
                        narration_target = _stage_media(
                            narration_source,
                            media_dir,
                            f"{index:03d}-narration",
                        )
                    staged.append(
                        {
                            "video": video_target.relative_to(staging).as_posix(),
                            "narration_audio": (
                                narration_target.relative_to(staging).as_posix()
                                if narration_target is not None
                                else None
                            ),
                        }
                    )
                    manifest_units.append(
                        {
                            "unit_id": presentation.unit_id,
                            "transition_to_next": value.transition_to_next,
                            "video": {
                                "source": presentation.video.media.artifact_path,
                                "staged": video_target.relative_to(staging).as_posix(),
                                "version": presentation.video.media.version,
                                "content_digest": presentation.video.media.evidence.content_digest,
                                "duration_microseconds": presentation.video.duration_microseconds,
                                "audio_enabled": presentation.video.audio_enabled,
                            },
                            "narration_audio": (
                                {
                                    "source": presentation.narration_audio.media.artifact_path,
                                    "staged": narration_target.relative_to(staging).as_posix(),
                                    "version": presentation.narration_audio.media.version,
                                    "content_digest": presentation.narration_audio.media.evidence.content_digest,
                                    "duration_microseconds": presentation.narration_audio.duration_microseconds,
                                }
                                if presentation.narration_audio is not None and narration_target is not None
                                else None
                            ),
                            "subtitles": [cue.to_dict() for cue in presentation.subtitles],
                        }
                    )

                safe_join(staging, "index.html").write_text(
                    _composition_html(materialized, staged),
                    encoding="utf-8",
                )
                safe_join(staging, "DESIGN.md").write_text(
                    """# ArcReel Darkroom\n\n## Colors\n- Canvas: `#0B0F14`\n- Primary text: `#F4F7F8`\n- Accent: `#65E6B3`\n- Warm accent: `#E9B96E`\n\n## Typography\n- Cross-platform `sans-serif`; bundle a webfont before choosing a named family.\n\n## Motion\n- Preserve readable pacing and deterministic, seekable animation.\n\n## What NOT to Do\n- Do not use random or wall-clock animation.\n- Do not fetch project media from external URLs.\n- Do not write outside this episode workspace.\n""",
                    encoding="utf-8",
                )
                safe_join(staging, "EDITING_PLAN.md").write_text(
                    "# HyperFrames Editing Plan\n\n"
                    "This file is filled by the ArcReel HyperFrames auto-edit Agent before it changes the timeline.\n\n"
                    "## Source Facts\n\n"
                    "## User Overrides\n\n"
                    "## Route and Spec\n\n"
                    "## Rhythm and Beats\n\n"
                    "## Copy\n\n"
                    "## Technique\n\n"
                    "## Background Music\n\n"
                    "## Negative Constraints\n",
                    encoding="utf-8",
                )
                script_file = materialized.presentations[0].script_file
                atomic_write_json(
                    safe_join(staging, "manifest.json"),
                    {
                        "schema_version": WORKSPACE_SCHEMA_VERSION,
                        "hyperframes_version": HYPERFRAMES_VERSION,
                        "project_name": project_name,
                        "episode": episode,
                        "variant": variant,
                        "entry_file": "index.html",
                        "editing_plan_file": "EDITING_PLAN.md",
                        "script_file": script_file,
                        "total_duration_microseconds": sum(
                            value.presentation.video.duration_microseconds for value in materialized.presentations
                        ),
                        "project_context": {
                            key: materialized.project_snapshot.get(key)
                            for key in (
                                "title",
                                "content_mode",
                                "generation_mode",
                                "style",
                                "aspect_ratio",
                            )
                            if materialized.project_snapshot.get(key) is not None
                        },
                        "units": manifest_units,
                    },
                )
                staging.rename(workspace.path)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        return workspace


class HyperframesStudioManager:
    """Launch the unmodified official Studio with one process per active episode."""

    def __init__(self) -> None:
        self._processes: dict[Path, _StudioProcess] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _command() -> list[str]:
        raw = os.environ.get(
            "ARCREEL_HYPERFRAMES_COMMAND",
            f"npx --yes hyperframes@{HYPERFRAMES_VERSION}",
        )
        command = shlex.split(raw)
        if not command:
            raise HyperframesStudioUnavailable("ARCREEL_HYPERFRAMES_COMMAND is empty")
        return command

    @staticmethod
    def _free_port() -> int:
        try:
            start = int(os.environ.get("ARCREEL_HYPERFRAMES_PORT_START", _DEFAULT_PORT_START))
            count = int(os.environ.get("ARCREEL_HYPERFRAMES_PORT_COUNT", _DEFAULT_PORT_COUNT))
        except ValueError as exc:
            raise HyperframesStudioUnavailable("HyperFrames port settings must be integers") from exc
        if not 1024 <= start <= 65535 or count <= 0 or start + count > 65536:
            raise HyperframesStudioUnavailable("HyperFrames port range is invalid")
        for port in range(start, start + count):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
        raise HyperframesStudioUnavailable("No free HyperFrames Studio port is available")

    @staticmethod
    def public_url(port: int, request_origin: str) -> str:
        template = os.environ.get("ARCREEL_HYPERFRAMES_PUBLIC_URL_TEMPLATE", "").strip()
        if template:
            try:
                value = template.format(port=port).rstrip("/")
            except (KeyError, ValueError) as exc:
                raise HyperframesStudioUnavailable("ARCREEL_HYPERFRAMES_PUBLIC_URL_TEMPLATE is invalid") from exc
            public = urlsplit(value)
            if (
                public.scheme not in {"http", "https"}
                or not public.hostname
                or public.username is not None
                or public.password is not None
                or public.path not in {"", "/"}
                or public.query
                or public.fragment
            ):
                raise HyperframesStudioUnavailable("HyperFrames public URL must be an HTTP(S) origin without a path")
            return value
        parsed = urlsplit(request_origin)
        if parsed.scheme not in {"http", "https"}:
            raise HyperframesStudioUnavailable("Browser origin must use HTTP(S)")
        host = parsed.hostname or "127.0.0.1"
        if parsed.scheme == "https":
            raise HyperframesStudioUnavailable(
                "HTTPS deployments must configure ARCREEL_HYPERFRAMES_PUBLIC_URL_TEMPLATE"
            )
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        return urlunsplit((parsed.scheme or "http", netloc, "", "", "")).rstrip("/")

    async def ensure_started(self, workspace: Path) -> int:
        workspace = workspace.resolve(strict=True)
        async with self._lock:
            current = self._processes.get(workspace)
            if current is not None and current.process.returncode is None:
                return current.port
            if current is not None:
                current.log_handle.close()
                self._processes.pop(workspace, None)

            port = self._free_port()
            log_dir = safe_join(workspace, ".arcreel")
            log_dir.mkdir(exist_ok=True)
            log_handle = safe_join(log_dir, "studio.log").open("ab")
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._command(),
                    "preview",
                    str(workspace),
                    "--port",
                    str(port),
                    "--foreground",
                    "--no-open",
                    "--json",
                    cwd=workspace,
                    stdout=log_handle,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, "NO_COLOR": "1"},
                )
            except (FileNotFoundError, OSError) as exc:
                log_handle.close()
                raise HyperframesStudioUnavailable(str(exc)) from exc
            state = _StudioProcess(workspace=workspace, port=port, process=process, log_handle=log_handle)
            self._processes[workspace] = state

        deadline = asyncio.get_running_loop().time() + _READY_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                await self._discard(workspace, state)
                raise HyperframesStudioUnavailable(
                    f"HyperFrames Studio exited with code {process.returncode}; see .arcreel/studio.log"
                )
            try:
                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                return port
            except OSError:
                await asyncio.sleep(0.15)
        await self._discard(workspace, state)
        raise HyperframesStudioUnavailable("HyperFrames Studio did not become ready in time")

    async def _discard(self, workspace: Path, state: _StudioProcess) -> None:
        async with self._lock:
            if self._processes.get(workspace) is state:
                self._processes.pop(workspace, None)
        await self._stop_state(state)

    async def shutdown(self) -> None:
        async with self._lock:
            states = list(self._processes.values())
            self._processes.clear()
        await asyncio.gather(*(self._stop_state(state) for state in states), return_exceptions=True)

    @staticmethod
    async def _stop_state(state: _StudioProcess) -> None:
        if state.process.returncode is None:
            state.process.terminate()
            try:
                await asyncio.wait_for(state.process.wait(), timeout=5)
            except TimeoutError:
                state.process.kill()
                await state.process.wait()
        state.log_handle.close()


_studio_manager = HyperframesStudioManager()


def get_hyperframes_studio_manager() -> HyperframesStudioManager:
    return _studio_manager


__all__ = [
    "HYPERFRAMES_VERSION",
    "HyperframesStudioManager",
    "HyperframesStudioUnavailable",
    "HyperframesWorkspace",
    "HyperframesWorkspaceService",
    "HyperframesWorkspaceUnavailable",
    "get_hyperframes_studio_manager",
]
