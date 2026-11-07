"""Media-duration probing and audio-upload validation."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.ffmpeg import FfmpegUnavailableError
from lib.infra.media_probe import MediaProbe, MediaProbeError, probe_media
from lib.infra.path_safety import safe_resolve

logger = logging.getLogger(__name__)

# Character reference audio (uploads and TTS samples): wav/mp3, 2-10 seconds, at most 15MB.
# Sources are referenced in lib/backends/audio_backends/dashscope.py and server/routers/files.py.
AUDIO_REFERENCE_MAX_BYTES = 15 * 1024 * 1024
AUDIO_REFERENCE_MIN_SECONDS = 2.0
AUDIO_REFERENCE_MAX_SECONDS = 10.0

# Upload validation runs synchronously within the request; invalid files must not occupy it indefinitely.
_UPLOAD_PROBE_DEADLINE_SECONDS = 10.0

# Probed formats are candidate demuxer names (m4a gives mov,mp4,m4a,3gp,3g2,mj2);
# require the token associated with the extension,
# rejecting non-wav/mp3 containers with audio tracks, such as m4a renamed to .wav.
_CONTAINER_FORMAT_TOKENS = {
    ".wav": {"wav"},
    ".mp3": {"mp3"},
}


def resolve_audio_ref_path(project_dir: Path, audio_refs_dir: Path, rel_path: str | None) -> Path | None:
    """Resolve reference_audio only when it lies in characters/refs_audio.

    Asset PATCH can write any project-relative string to this field because
    extra_string_fields checks only types. safe_resolve prevents escaping the project
    but cannot prevent deletion of another project file such as project.json.
    Therefore also verify the parent is refs_audio. Upload replacement (files.py)
    and confirmation of TTS-generated samples (generate.py) share this check.
    """
    resolved = safe_resolve(project_dir, rel_path)
    if resolved is None:
        return None
    if os.path.realpath(resolved.parent) != os.path.realpath(audio_refs_dir):
        return None
    return resolved


def resolve_stale_reference_audio(
    project_dir: Path, audio_refs_dir: Path, old_audio: str | None, new_path: Path
) -> Path | None:
    """Identify an old reference-audio file left unreferenced after replacement.

    Audio does not use the fixed extension of reference images, so replacement may
    change extensions and must explicitly clean up old files to avoid orphans.
    None means no cleanup: the old pointer is empty, outside refs_audio (see
    :func:`resolve_audio_ref_path`), or differs only in case while naming the same
    inode on a case-insensitive filesystem (such as ``Alice.WAV`` and ``Alice.wav``).
    In that last case the new sample overwrites the file in place; deleting it as an
    orphan would also delete the newly written sample.
    """
    if not isinstance(old_audio, str) or not old_audio:
        return None
    resolved_old = resolve_audio_ref_path(project_dir, audio_refs_dir, old_audio)
    if resolved_old is None:
        return None
    if new_path.exists() and resolved_old.samefile(new_path):
        return None
    return resolved_old


async def _probe_or_none(path: Path) -> MediaProbe | None:
    """Probe an existing file; return None if bundled ffmpeg is unavailable or parsing fails."""
    try:
        return await probe_media(path)
    except (FfmpegUnavailableError, MediaProbeError, OSError):
        return None


def _write_probe_temp_file(content: bytes, suffix: str, created: list[Path]) -> Path:
    """Write bytes to the system temporary directory; register the file in ``created`` immediately for caller cleanup."""
    with tempfile.NamedTemporaryFile(dir=tempfile.gettempdir(), suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        created.append(tmp_path)
        tmp.write(content)
    return tmp_path


async def probe_audio_duration_seconds(content: bytes, suffix: str) -> float | None:
    """Probe audio bytes for duration in seconds and verify a decodable audio stream.

    If bundled ffmpeg is unavailable, return None so callers skip duration
    validation without blocking the upload.

    Raises:
        ValueError: Parsing fails, probing times out, no audio stream exists (such
            as video renamed to .wav/.mp3), or the container mismatches the extension
            (such as m4a/aac renamed to .wav).
    """
    # Cancellation during the temporary-file write loses the return value; registered paths enable cleanup.
    created: list[Path] = []
    try:
        tmp_path = await run_sync_transaction(_write_probe_temp_file, content, suffix, created)
        try:
            probe = await probe_media(tmp_path, deadline_seconds=_UPLOAD_PROBE_DEADLINE_SECONDS)
        except (FfmpegUnavailableError, OSError):
            logger.info("随包 ffmpeg 不可用，跳过音频时长探测", exc_info=True)
            return None
        except MediaProbeError:
            raise ValueError("音频文件无法解析") from None
    finally:
        for path in created:
            await run_noninterruptible_sync(
                path.unlink, missing_ok=True
            )  # hard-delete: temp -- temporary file used for duration probing

    if probe.first_stream("audio") is None:
        raise ValueError("音频文件无法解析")
    expected_tokens = _CONTAINER_FORMAT_TOKENS.get(suffix.lower())
    if expected_tokens is not None and not probe.container_formats & expected_tokens:
        raise ValueError("音频文件无法解析")
    if probe.duration_seconds is None:
        raise ValueError("音频文件无法解析")
    return probe.duration_seconds


async def probe_existing_media_duration_seconds(path: Path) -> float | None:
    """Probe the container duration of an existing media file in seconds.

    Unlike :func:`probe_audio_duration_seconds`, probe the existing file directly,
    without temporary files or stream-type validation. Callers needing playable
    video boundaries should use :func:`probe_existing_video_duration_seconds`.
    Return None if bundled ffmpeg is unavailable or probing fails; callers decide
    whether their business rules allow proceeding.
    """
    probe = await _probe_or_none(path)
    return probe.duration_seconds if probe is not None else None


async def probe_existing_video_duration_seconds(path: Path) -> float | None:
    """Probe the first video stream's playable duration; return None if absent or probing fails.

    An audio tail may make the container longer than its video stream. Editors bound
    source ranges by video duration, so the container tail is not usable footage.
    """
    probe = await _probe_or_none(path)
    video = probe.first_stream("video") if probe is not None else None
    return video.duration_seconds if video is not None else None


async def probe_existing_audio_duration_seconds(path: Path) -> float | None:
    """Probe formal audio duration, retaining a semantic entry point for audio callers."""

    return await probe_existing_media_duration_seconds(path)


async def probe_reference_audio_total_seconds(paths: list[Path]) -> float | None:
    """Sum reference-audio durations in seconds for request-time total-duration checks.

    If any file cannot be probed (bundled ffmpeg unavailable or invalid file), return
    None rather than a partial total: a partial sum could incorrectly admit an
    over-limit request, which is more dangerous than skipping validation.
    """
    total = 0.0
    for path in paths:
        duration = await probe_existing_audio_duration_seconds(path)
        if duration is None:
            return None
        total += duration
    return total
