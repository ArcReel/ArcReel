"""Project-state adapter for typed video Artifact Manifest currency."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lib.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.asset_types import asset_name_comparison_key
from lib.generation_queue import CompensableGenerationResult
from lib.json_io import atomic_write_bytes
from lib.project_manager import ProjectManager, find_episode
from lib.reference_video.duration_slots import resolve_duration_slot
from lib.reference_video.prompt_render import resolve_reference_audio_paths
from lib.reference_video.request_projection import (
    FilesystemReferenceAssets,
    canonicalize_references,
    clamp_reference_assets,
    hydrate_reference_assets,
    resolve_reference_assets,
)
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.speech_artifact_provenance import (
    build_video_duration_basis,
    build_video_speech_basis,
    project_character_voice_evidence,
)
from lib.speech_composition import admit_script_unit
from lib.version_manager import PaidVersionCommit, VersionManager
from lib.video_artifact_commit import commit_paid_video_artifact
from lib.video_visual_provenance import resolve_video_aspect_ratio
from lib.visual_artifact_provenance import (
    build_reference_video_artifact_visual_basis,
    build_storyboard_video_artifact_visual_basis,
)
from server.services.narration_delivery_tasks import (
    resolve_storyboard_video_inputs,
    validate_generated_video_covers_current_tts,
)


class VideoArtifactCommitter:
    """Callable formal-output hook shared by normal and resume execution."""

    def __init__(
        self,
        *,
        project_manager: ProjectManager,
        project_name: str,
        project_path: Path,
        versions: VersionManager,
        resource_type: str,
        resource_id: str,
        prompt: str,
    ) -> None:
        if resource_type not in {"videos", "reference_videos"}:
            raise ValueError(f"unsupported video artifact resource type: {resource_type!r}")
        self._project_manager = project_manager
        self._project_name = project_name
        self._project_path = project_path
        self._versions = versions
        self._resource_type = resource_type
        self._resource_id = resource_id
        self._prompt = prompt
        self.outcome: PaidVersionCommit | None = None
        self.selection_error: BaseException | None = None
        self._current_file: Path | None = None
        self._selected_episode: int | None = None
        self._selected_script_file: str | None = None
        self._selected_basis: ArtifactBasisDescriptor | None = None
        self._selected_artifact_path: str | None = None
        self._prior_manifest_entry: ArtifactManifestEntry | None = None
        self._prior_assets: dict[str, tuple[bool, Any]] | None = None
        self._prior_thumbnail: tuple[Path, bool, bytes | None] | None = None

    async def prepare_selection(
        self,
        staged_file: Path,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
    ) -> None:
        """Validate paid bytes before the synchronous lock-held selection decision.

        Validation failures are retained instead of raised here.  The ensuing
        formal callback can then archive the paid bytes history-only, after
        which the executor re-raises the stored failure without ever exposing
        the invalid media as current.
        """

        narration = version_metadata.get("execution_narration")
        if not isinstance(narration, Mapping) or narration.get("delivery") != "use_tts":
            return
        script_file = version_metadata.get("execution_script_file")
        if not isinstance(script_file, str) or not script_file:
            return
        try:
            await validate_generated_video_covers_current_tts(
                project_name=self._project_name,
                script_file=script_file,
                request_duration_seconds=duration_seconds,
                output_path=staged_file,
                resource_type=self._resource_type,
                resource_id=self._resource_id,
            )
        except BaseException as exc:
            self.selection_error = exc

    def __call__(
        self,
        staged_file: Path,
        current_file: Path,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
    ) -> PaidVersionCommit:
        snapshot: dict[str, dict[str, Any] | None] = {"project": None, "script": None}
        script_file = version_metadata.get("execution_script_file")

        @contextmanager
        def _selection_guard():
            if not isinstance(script_file, str) or not script_file:
                yield
                return
            with self._project_manager.locked_project_script_snapshot(
                self._project_name,
                script_file,
            ) as (project, script):
                snapshot["project"] = project
                snapshot["script"] = script
                self._capture_prior_assets(script)
                yield

        def _current_basis(metadata: Mapping[str, Any]) -> ArtifactBasisDescriptor | None:
            if self.selection_error is not None:
                return None
            project = snapshot["project"]
            script = snapshot["script"]
            if project is None or script is None:
                return None
            return build_current_video_artifact_basis(
                project_path=self._project_path,
                project=project,
                script=script,
                resource_type=self._resource_type,
                resource_id=self._resource_id,
                versions=self._versions,
                version_metadata=metadata,
            )

        self._current_file = current_file
        episode = version_metadata.get("artifact_episode")
        raw_basis = version_metadata.get("artifact_video_basis")
        self._selected_episode = episode if type(episode) is int and episode > 0 else None
        self._selected_script_file = script_file if isinstance(script_file, str) and script_file else None
        try:
            self._selected_basis = ArtifactBasisDescriptor.from_dict(raw_basis)
        except (TypeError, ValueError):
            self._selected_basis = None
        try:
            self._selected_artifact_path = (
                current_file.resolve(strict=False).relative_to(self._project_path.resolve(strict=True)).as_posix()
            )
        except (FileNotFoundError, ValueError):
            self._selected_artifact_path = None

        outcome = commit_paid_video_artifact(
            project_path=self._project_path,
            versions=self._versions,
            resource_type=self._resource_type,
            resource_id=self._resource_id,
            prompt=self._prompt,
            staged_file=staged_file,
            current_file=current_file,
            duration_seconds=duration_seconds,
            version_metadata=version_metadata,
            resolve_current_basis=_current_basis,
            selection_guard=_selection_guard,
            capture_prior_manifest=self._capture_prior_manifest,
        )
        self.outcome = outcome
        return outcome

    def compensate_selection(self) -> bool:
        """Undo this committer's selected formal version after task failure/cancellation."""

        outcome = self.outcome
        episode = self._selected_episode
        script_file = self._selected_script_file
        basis = self._selected_basis
        current_file = self._current_file
        artifact_path = self._selected_artifact_path
        prior_assets = self._prior_assets
        prior_thumbnail = self._prior_thumbnail
        if (
            outcome is None
            or not outcome.selected
            or episode is None
            or script_file is None
            or basis is None
            or current_file is None
            or artifact_path is None
            or prior_assets is None
            or prior_thumbnail is None
        ):
            return False

        class _SelectionChanged(RuntimeError):
            pass

        def _same_script(project: dict[str, Any]) -> str:
            entry = find_episode(project, episode)
            current_binding = entry.get("script_file") if isinstance(entry, dict) else None
            if not isinstance(current_binding, str) or (
                ProjectManager.normalize_script_filename(current_binding)
                != ProjectManager.normalize_script_filename(script_file)
            ):
                raise _SelectionChanged("episode script binding changed before video compensation")
            return current_binding

        def _restore_manifest_and_thumbnail() -> None:
            adapter = ProjectArtifactManifestAdapter(self._project_path)
            key = ArtifactKey.episode_video(episode, self._resource_id)
            expected = ArtifactManifestEntry(
                artifact_path=artifact_path,
                basis_digest=basis.digest,
            )
            if adapter.get_entry(key) != expected:
                raise _SelectionChanged("video artifact selection changed before compensation")
            thumbnail_path, prior_thumbnail_present, prior_thumbnail_bytes = prior_thumbnail
            selected_thumbnail_present, selected_thumbnail_bytes = _snapshot_file(thumbnail_path)
            try:
                _restore_file(thumbnail_path, prior_thumbnail_present, prior_thumbnail_bytes)
                if self._prior_manifest_entry is None:
                    adapter.delete_entry(key)
                else:
                    adapter.put_entry(key, self._prior_manifest_entry)
            except BaseException:
                rollback_failures: list[BaseException] = []
                try:
                    _restore_file(thumbnail_path, selected_thumbnail_present, selected_thumbnail_bytes)
                except BaseException as exc:
                    rollback_failures.append(exc)
                try:
                    if adapter.get_entry(key) != expected:
                        adapter.put_entry(key, expected)
                except BaseException as exc:
                    rollback_failures.append(exc)
                if rollback_failures:
                    raise RuntimeError(
                        "video compensation failed and thumbnail/Manifest rollback was incomplete"
                    ) from rollback_failures[0]
                raise

        def _reject(_script_path: Path) -> None:
            restored = self._versions.reject_current_version(
                self._resource_type,
                self._resource_id,
                rejected_version=outcome.version,
                current_file=current_file,
                on_reject=_restore_manifest_and_thumbnail,
            )
            if not restored:
                raise _SelectionChanged("video version selection changed before compensation")

        try:
            with self._project_manager.locked_episode_script(
                self._project_name,
                _same_script,
                validate=False,
                on_commit=_reject,
            ) as script:
                item = _find_script_item(script, self._resource_id)
                assets = item.get("generated_assets")
                if not isinstance(assets, dict):
                    assets = {}
                    item["generated_assets"] = assets
                for field, (present, value) in prior_assets.items():
                    if present:
                        assets[field] = copy.deepcopy(value)
                    else:
                        assets.pop(field, None)
        except _SelectionChanged:
            return False
        return True

    def _capture_prior_manifest(self, entry: ArtifactManifestEntry | None) -> None:
        self._prior_manifest_entry = entry

    def _capture_prior_assets(self, script: dict[str, Any]) -> None:
        thumbnail = (
            self._project_path / "thumbnails" / f"scene_{self._resource_id}.jpg"
            if self._resource_type == "videos"
            else self._project_path / "reference_videos" / "thumbnails" / f"{self._resource_id}.jpg"
        )
        present, content = _snapshot_file(thumbnail)
        self._prior_thumbnail = (thumbnail, present, content)
        try:
            item = _find_script_item(script, self._resource_id)
        except (KeyError, TypeError, ValueError):
            self._prior_assets = None
            return
        assets = item.get("generated_assets")
        if not isinstance(assets, dict):
            assets = {}
        self._prior_assets = {
            field: (field in assets, copy.deepcopy(assets.get(field)))
            for field in ("video_clip", "video_uri", "video_thumbnail", "video_generated_at", "status")
        }


async def finalize_selected_video_result(
    *,
    committer: VideoArtifactCommitter,
    finalize: Callable[[], Awaitable[dict[str, Any]]],
) -> CompensableGenerationResult:
    """Finalize a selected video and span the task terminal-update window.

    Selection precedes script/thumbnails finalization because paid media must be
    committed through the version lock first.  Any failure in that remaining
    work compensates the selection synchronously before it is re-raised.  A
    successful result carries the same idempotent compensation into
    ``GenerationQueue.mark_task_succeeded`` so an already-cancelled row cannot
    leave the media selected.
    """

    outcome = committer.outcome
    if outcome is None or not outcome.selected:
        raise RuntimeError("selected video finalization requires a selected artifact commit")
    try:
        result = await finalize()
    except BaseException as failure:
        try:
            committer.compensate_selection()
        except BaseException as compensation_failure:
            failure.add_note(f"video selection compensation also failed: {compensation_failure}")
        raise

    def _compensate_cancelled() -> None:
        committer.compensate_selection()

    return CompensableGenerationResult(result, cancel_compensation=_compensate_cancelled)


def build_current_video_artifact_basis(
    *,
    project_path: Path,
    project: dict[str, Any],
    script: dict[str, Any],
    resource_type: str,
    resource_id: str,
    versions: VersionManager,
    version_metadata: Mapping[str, Any],
) -> ArtifactBasisDescriptor | None:
    """Rebuild current input basis using only frozen execution dependency shape."""

    episode = version_metadata.get("artifact_episode")
    if type(episode) is not int or episode < 1:
        return None
    script_file = version_metadata.get("execution_script_file")
    if not isinstance(script_file, str) or not script_file:
        return None
    try:
        current_episode = ProjectManager.resolve_episode_from_script(script, script_file)
    except ValueError:
        return None
    if current_episode != episode:
        return None

    items, id_field, kind = resolve_items(script)
    item = next(
        (
            candidate
            for candidate in items
            if isinstance(candidate, dict) and str(candidate.get(id_field)) == resource_id
        ),
        None,
    )
    if item is None:
        return None
    admission = admit_script_unit(kind, item)
    if not admission.allowed:
        return None

    style_speakers = _string_sequence(version_metadata.get("artifact_voice_style_speakers"))
    if style_speakers is None:
        return None
    audio_speakers = _execution_reference_audio_speakers(version_metadata.get("execution_provider_media"))
    if audio_speakers is None:
        return None
    available_audio = resolve_reference_audio_paths(project, project_path)
    selected_audio = {speaker: available_audio[speaker] for speaker in audio_speakers if speaker in available_audio}
    speech = build_video_speech_basis(
        admission.preparation,
        voices=project_character_voice_evidence(
            admission.preparation,
            characters=project.get("characters"),
            voice_style_speakers=style_speakers,
            reference_audio_paths=selected_audio,
        ),
    )

    if resource_type == "videos":
        prompt = item.get("video_prompt")
        storyboard, end_frame = resolve_storyboard_video_inputs(
            project_path=project_path,
            resource_id=resource_id,
            item=item,
        )
        visual = build_storyboard_video_artifact_visual_basis(
            resource_id=resource_id,
            visual_prompt=prompt,
            storyboard_image=storyboard,
            end_frame_image=end_frame,
            aspect_ratio=resolve_video_aspect_ratio(project),
        )
    elif resource_type == "reference_videos":
        limit = version_metadata.get("artifact_reference_image_limit")
        if limit is not None and (type(limit) is not int or limit < 0):
            return None
        declared = canonicalize_references(item.get("references"))
        resolved = resolve_reference_assets(project, project_path, item)
        hydration = hydrate_reference_assets(declared, resolved, FilesystemReferenceAssets(project_path))
        if hydration.missing:
            return None
        visual = build_reference_video_artifact_visual_basis(
            unit=item,
            request_assets=clamp_reference_assets(hydration.available, limit),
            style=project.get("style") if isinstance(project.get("style"), str) else None,
            aspect_ratio=resolve_video_aspect_ratio(project),
        )
    else:
        return None

    duration = _current_duration_tier_basis(
        project_path=project_path,
        project=project,
        item=item,
        resource_id=resource_id,
        episode=episode,
        versions=versions,
        version_metadata=version_metadata,
    )
    if duration is None:
        return None
    return ArtifactBasisDescriptor.from_basis(
        compose_video_artifact_basis(visual=visual, speech=speech, duration=duration)
    )


def _current_duration_tier_basis(
    *,
    project_path: Path,
    project: Mapping[str, Any],
    item: Mapping[str, Any],
    resource_id: str,
    episode: int,
    versions: VersionManager,
    version_metadata: Mapping[str, Any],
):
    tiers = _positive_integer_tiers(version_metadata.get("artifact_duration_tiers"))
    if tiers is None:
        return None
    planned = item.get("duration_seconds")
    if type(planned) is not int or planned <= 0:
        planned = project.get("default_duration")
    if type(planned) is not int or planned <= 0:
        return None
    duration_input: int | float = planned
    narration = version_metadata.get("execution_narration")
    if isinstance(narration, Mapping) and narration.get("delivery") == "use_tts":
        actual = _selected_current_tts_duration(
            project_path=project_path,
            versions=versions,
            episode=episode,
            resource_id=resource_id,
        )
        if actual is not None:
            duration_input = max(duration_input, actual)
    slot = resolve_duration_slot(duration_input, tiers)
    if slot.adjustment == "down" and duration_input > slot.seconds:
        return None
    return build_video_duration_basis(slot.seconds)


def _selected_current_tts_duration(
    *,
    project_path: Path,
    versions: VersionManager,
    episode: int,
    resource_id: str,
) -> float | None:
    history = versions.get_versions("audio", resource_id)
    selected = next((record for record in history["versions"] if record.get("is_current")), None)
    if not isinstance(selected, dict):
        return None
    raw_basis = selected.get("artifact_audio_basis")
    actual = selected.get("tts_actual_duration_seconds")
    if not isinstance(raw_basis, Mapping) or isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return None
    try:
        descriptor = ArtifactBasisDescriptor.from_dict(raw_basis)
    except ValueError:
        return None
    if descriptor.kind != "narration-delivery/tts-audio" or actual <= 0:
        return None
    entry = ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_audio(episode, resource_id))
    expected_path = resource_relative_path("audio", resource_id)
    if entry is None or entry.artifact_path != expected_path or entry.basis_digest != descriptor.digest:
        return None
    if not (project_path / expected_path).is_file():
        return None
    return float(actual)


def _execution_reference_audio_speakers(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    speakers: list[str] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            return None
        if raw.get("role") != "reference_audio":
            continue
        name = raw.get("logical_name")
        if not isinstance(name, str) or not name:
            return None
        canonical = asset_name_comparison_key(name)
        if canonical not in speakers:
            speakers.append(canonical)
    return tuple(speakers)


def _find_script_item(script: dict[str, Any], resource_id: str) -> dict[str, Any]:
    items, id_field, _kind = resolve_items(script)
    item = next(
        (
            candidate
            for candidate in items
            if isinstance(candidate, dict) and str(candidate.get(id_field)) == resource_id
        ),
        None,
    )
    if item is None:
        raise KeyError(f"script unit not found: {resource_id}")
    return item


def _snapshot_file(path: Path) -> tuple[bool, bytes | None]:
    if path.is_file():
        return True, path.read_bytes()
    if path.exists():
        raise OSError(f"expected a regular file: {path}")
    return False, None


def _restore_file(path: Path, present: bool, content: bytes | None) -> None:
    if present:
        if content is None:
            raise RuntimeError("present file snapshot is missing bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, content)
    else:
        path.unlink(missing_ok=True)


def _string_sequence(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return None
    canonical = tuple(asset_name_comparison_key(item) for item in value)
    if len(set(canonical)) != len(canonical):
        return None
    return canonical


def _positive_integer_tiers(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, list):
        return None
    tiers = tuple(value)
    if not tiers or any(type(tier) is not int or tier <= 0 for tier in tiers):
        return None
    if tuple(sorted(set(tiers))) != tiers:
        return None
    return tiers


def paid_video_history_result(
    *,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    version: int,
    video_uri: str | None,
    warnings: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Return a successful paid-history result without claiming a formal path."""

    records = versions.get_versions(resource_type, resource_id)["versions"]
    record = next((item for item in records if item.get("version") == version), None)
    if not isinstance(record, dict):
        raise RuntimeError("committed paid video version is missing from history")
    result: dict[str, Any] = {
        "version": version,
        "file_path": record.get("file"),
        "created_at": record.get("created_at"),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "video_uri": video_uri,
        "selected_current": False,
    }
    if resource_type == "reference_videos":
        result["warnings"] = list(warnings)
    return result


__all__ = [
    "VideoArtifactCommitter",
    "build_current_video_artifact_basis",
    "finalize_selected_video_result",
    "paid_video_history_result",
]
