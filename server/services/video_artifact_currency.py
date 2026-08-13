"""Project-state adapter for typed video Artifact Manifest currency."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.asset_types import asset_name_comparison_key
from lib.async_thread import EventLoopBridge, run_noninterruptible_async
from lib.generation_admission import generation_admission_lock, generation_admission_lock_sync
from lib.generation_queue import CompensableGenerationResult
from lib.json_io import atomic_write_bytes
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis
from lib.project_manager import ProjectManager, resolve_episode_script_binding
from lib.reference_video.duration_slots import resolve_duration_slot
from lib.reference_video.execution_checkpoint import NarrationExecutionFacts
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
from lib.speech_composition import SpeechMode, SpeechPreparation, admit_script_unit
from lib.version_manager import PaidVersionCommit, VersionManager
from lib.video_artifact_commit import commit_paid_video_artifact
from lib.video_artifact_facts import VIDEO_ARTIFACT_RESTORE_BLOCKER_FIELD, VideoArtifactCurrencyFacts
from lib.video_visual_provenance import resolve_video_aspect_ratio
from lib.visual_artifact_provenance import (
    build_reference_video_artifact_visual_basis,
    build_storyboard_video_artifact_visual_basis,
)
from server.services.narration_delivery_tasks import (
    CurrentTtsSettingsResolver,
    resolve_storyboard_video_inputs,
    validate_generated_video_covers_tts_duration,
)


@dataclass(frozen=True, slots=True)
class FrozenVideoSpeechFacts:
    """Canonical speech component and the execution-local voice dependencies it used."""

    basis: ArtifactBasis
    voice_style_speakers: tuple[str, ...]


def freeze_video_speech_facts(
    preparation: SpeechPreparation,
    *,
    characters: object,
    include_voice_styles: bool,
    reference_audio_paths: Mapping[str, Path] | None = None,
) -> FrozenVideoSpeechFacts:
    """Freeze route-independent character speech evidence for a paid video request."""

    speakers = (
        tuple(
            dict.fromkeys(
                asset_name_comparison_key(str(utterance.speaker))
                for utterance in preparation.utterances
                if utterance.speaker
            )
        )
        if preparation.mode is SpeechMode.CHARACTER_SPEECH and include_voice_styles
        else ()
    )
    basis = build_video_speech_basis(
        preparation,
        voices=project_character_voice_evidence(
            preparation,
            characters=characters,
            voice_style_speakers=speakers,
            reference_audio_paths=reference_audio_paths,
        ),
    )
    return FrozenVideoSpeechFacts(
        basis=basis,
        voice_style_speakers=speakers,
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
        self._current_tts_settings: TtsSynthesisSettings | None = None
        self._current_tts_basis_resolved = False
        self._tts_settings_bridge: EventLoopBridge | None = None
        self._restore_blocker: str | None = None
        self._admission_guard: AbstractAsyncContextManager[None] | None = None

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

        script_file = version_metadata.get("execution_script_file")
        if isinstance(script_file, str) and script_file:
            if self._admission_guard is not None:
                raise RuntimeError("video artifact selection guard was already acquired")
            guard = generation_admission_lock(
                project_name=self._project_name,
                script_file=script_file,
                resource_id=self._resource_id,
            )
            await run_noninterruptible_async(guard.__aenter__())
            self._admission_guard = guard

        raw_narration = version_metadata.get("execution_narration")
        if not isinstance(raw_narration, Mapping) or raw_narration.get("delivery") != "use_tts":
            return
        self._tts_settings_bridge = EventLoopBridge.capture()
        try:
            narration = NarrationExecutionFacts.from_dict(dict(raw_narration))
            if narration.actual_duration_seconds is None:
                raise ValueError("use_tts execution facts are missing actual duration")
            await validate_generated_video_covers_tts_duration(
                resource_id=self._resource_id,
                request_duration_seconds=duration_seconds,
                output_path=staged_file,
                tts_actual_duration_seconds=narration.actual_duration_seconds,
            )
        except (Exception, asyncio.CancelledError) as exc:
            self.selection_error = exc
            code = getattr(exc, "code", None)
            self._restore_blocker = code if isinstance(code, str) and code else "output_duration_unverified"
            return

        try:
            VideoArtifactCurrencyFacts.from_dict(version_metadata.get("artifact_video_currency"))
        except (TypeError, ValueError):
            return

    async def release_admission_guard(self) -> None:
        """Release the selection/finalization guard, if formal preparation acquired it."""

        guard = self._admission_guard
        if guard is None:
            return
        self._admission_guard = None
        await guard.__aexit__(None, None, None)

    def __call__(
        self,
        staged_file: Path,
        current_file: Path,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
    ) -> PaidVersionCommit:
        snapshot: dict[str, dict[str, Any] | None] = {"project": None, "script": None}
        metadata = dict(version_metadata)
        if self._restore_blocker is not None:
            metadata[VIDEO_ARTIFACT_RESTORE_BLOCKER_FIELD] = self._restore_blocker
        script_file = metadata.get("execution_script_file")

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
            narration = metadata.get("execution_narration")
            project = snapshot["project"]
            script = snapshot["script"]
            if project is None or script is None:
                return None
            if (
                isinstance(narration, Mapping)
                and narration.get("delivery") == "use_tts"
                and not self._current_tts_basis_resolved
            ):
                bridge = self._tts_settings_bridge
                if bridge is None:
                    self.selection_error = RuntimeError("current TTS selection was not prepared on an event loop")
                    return None
                try:
                    self._current_tts_settings = bridge.run(
                        CurrentTtsSettingsResolver(
                            self._project_name,
                            project_path=self._project_path,
                        ).resolve_tts_synthesis_settings(project)
                    )
                except ValueError:
                    # No configured current TTS means there is no fresh duration
                    # that can invalidate the execution-frozen video tier.
                    self._current_tts_settings = None
                except (Exception, asyncio.CancelledError) as exc:
                    self.selection_error = exc
                    return None
                self._current_tts_basis_resolved = True
            return build_current_video_artifact_basis(
                project_path=self._project_path,
                project=project,
                script=script,
                resource_type=self._resource_type,
                resource_id=self._resource_id,
                versions=self._versions,
                version_metadata=metadata,
                current_tts_settings=self._current_tts_settings,
            )

        self._current_file = current_file
        try:
            artifact_currency = VideoArtifactCurrencyFacts.from_dict(metadata.get("artifact_video_currency"))
        except (TypeError, ValueError):
            artifact_currency = None
        self._selected_episode = artifact_currency.episode if artifact_currency is not None else None
        self._selected_script_file = script_file if isinstance(script_file, str) and script_file else None
        self._selected_basis = artifact_currency.video_descriptor if artifact_currency is not None else None
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
            version_metadata=metadata,
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

        class _ScriptBindingChanged(_SelectionChanged):
            pass

        def _same_script(project: dict[str, Any]) -> str:
            current_binding = resolve_episode_script_binding(project, episode, script_file)
            if current_binding is None:
                raise _ScriptBindingChanged("episode script binding changed before video compensation")
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
            except BaseException as original_error:
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
                    rollback_failures[0].__cause__ = original_error
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

        admission_guard = (
            nullcontext()
            if self._admission_guard is not None
            else generation_admission_lock_sync(
                project_name=self._project_name,
                script_file=script_file,
                resource_id=self._resource_id,
            )
        )
        with admission_guard:
            try:
                with self._project_manager.locked_episode_script(
                    self._project_name,
                    _same_script,
                    validate=False,
                    on_commit=_reject,
                ) as script:
                    try:
                        item = _find_script_item(script, self._resource_id)
                    except KeyError:
                        pass
                    else:
                        assets = item.get("generated_assets")
                        if not isinstance(assets, dict):
                            assets = {}
                            item["generated_assets"] = assets
                        for field, (present, value) in prior_assets.items():
                            if present:
                                assets[field] = copy.deepcopy(value)
                            else:
                                assets.pop(field, None)
            except _ScriptBindingChanged:
                restored = self._versions.reject_current_version(
                    self._resource_type,
                    self._resource_id,
                    rejected_version=outcome.version,
                    current_file=current_file,
                    on_reject=_restore_manifest_and_thumbnail,
                )
                return (
                    restored
                    or self._versions.get_current_version(self._resource_type, self._resource_id) != outcome.version
                )
            except _SelectionChanged:
                return self._versions.get_current_version(self._resource_type, self._resource_id) != outcome.version
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
            _require_video_selection_compensation(committer)
        except BaseException as compensation_failure:
            failure.add_note(f"video selection compensation also failed: {compensation_failure}")
        raise

    def _compensate_cancelled() -> None:
        _require_video_selection_compensation(committer)

    return CompensableGenerationResult(result, cancel_compensation=_compensate_cancelled)


def _require_video_selection_compensation(committer: VideoArtifactCommitter) -> None:
    if not committer.compensate_selection():
        raise RuntimeError("video artifact remains selected after compensation")


def build_current_video_artifact_basis(
    *,
    project_path: Path,
    project: dict[str, Any],
    script: dict[str, Any],
    resource_type: str,
    resource_id: str,
    versions: VersionManager,
    version_metadata: Mapping[str, Any],
    current_tts_settings: TtsSynthesisSettings | None = None,
) -> ArtifactBasisDescriptor | None:
    """Rebuild current input basis using only frozen execution dependency shape."""

    try:
        artifact_currency = VideoArtifactCurrencyFacts.from_dict(version_metadata.get("artifact_video_currency"))
    except (TypeError, ValueError):
        return None
    episode = artifact_currency.episode
    script_file = version_metadata.get("execution_script_file")
    if not isinstance(script_file, str) or not script_file:
        return None
    try:
        current_episode = ProjectManager.resolve_episode_from_script(script, script_file)
    except ValueError:
        return None
    if current_episode != episode:
        return None
    if resolve_episode_script_binding(project, episode, script_file) is None:
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

    style_speakers = artifact_currency.voice_style_speakers
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
        limit = artifact_currency.reference_image_limit
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
        artifact_currency=artifact_currency,
        preparation=admission.preparation,
        current_tts_settings=current_tts_settings,
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
    artifact_currency: VideoArtifactCurrencyFacts,
    preparation: SpeechPreparation,
    current_tts_settings: TtsSynthesisSettings | None,
):
    tiers = artifact_currency.duration_tiers
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
            preparation=preparation,
            current_tts_settings=current_tts_settings,
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
    preparation: SpeechPreparation,
    current_tts_settings: TtsSynthesisSettings | None,
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
    if current_tts_settings is None:
        return None
    try:
        expected = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis(preparation, current_tts_settings))
    except (TypeError, ValueError):
        return None
    if descriptor != expected:
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


async def complete_video_artifact_commit(
    *,
    committer: VideoArtifactCommitter | None,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    version: int,
    video_uri: str | None,
    finalize: Callable[[], Awaitable[dict[str, Any]]],
    warnings: Sequence[dict[str, Any]] = (),
    on_completed: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Finish normal and resumed video generation through one selection seam."""

    async def _finalize_and_complete() -> dict[str, Any]:
        result = await finalize()
        if on_completed is not None:
            on_completed()
        return result

    if committer is None:
        return await _finalize_and_complete()
    try:
        if committer.outcome is None:
            raise RuntimeError("formal video generator returned without invoking the artifact commit callback")
        if committer.selection_error is not None:
            raise committer.selection_error
        if not committer.outcome.selected:
            result = await asyncio.to_thread(
                paid_video_history_result,
                versions=versions,
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
                video_uri=video_uri,
                warnings=warnings,
            )
            if on_completed is not None:
                on_completed()
            return result
        return await finalize_selected_video_result(committer=committer, finalize=_finalize_and_complete)
    finally:
        await committer.release_admission_guard()


__all__ = [
    "VideoArtifactCommitter",
    "FrozenVideoSpeechFacts",
    "build_current_video_artifact_basis",
    "complete_video_artifact_commit",
    "finalize_selected_video_result",
    "freeze_video_speech_facts",
    "paid_video_history_result",
]
