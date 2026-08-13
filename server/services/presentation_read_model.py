"""Project/version adapter for the unified speech presentation read model."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.audio_utils import probe_existing_media_duration_seconds, probe_existing_video_duration_seconds
from lib.generation_admission import generation_admission_lock
from lib.json_io import atomic_write_bytes, atomic_write_json
from lib.narration_delivery import (
    POST_PRODUCTION,
    USE_TTS,
    TtsSettingsResolver,
    TtsSynthesisSettings,
    build_narration_audio_basis,
)
from lib.path_safety import safe_join
from lib.project_manager import ProjectManager
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.speech_artifact_provenance import RenditionVariant, SelectedMediaEvidence, media_content_digest
from lib.speech_composition import SpeechMode, admit_script_unit
from lib.speech_presentation import (
    MediaCurrency,
    MediaSelection,
    PresentationMedia,
    PresentationValue,
    RawPresentationMedia,
    SpeechPresentation,
    materialize_raw_video_presentation,
    materialize_speech_presentation,
)
from lib.version_manager import VersionManager
from server.services.artifact_version_restore import (
    TypedMediaRestoreTarget,
    parse_typed_media_version_record,
)
from server.services.narration_delivery_tasks import CurrentTtsSettingsResolver
from server.services.video_artifact_currency import build_current_video_artifact_basis

DurationProbe = Callable[[Path], Awaitable[float | None]]
ContentDigest = Callable[[Path], str]
SettingsResolverFactory = Callable[[str, Path], TtsSettingsResolver]


class PresentationUnavailableError(ValueError):
    """Selected media cannot safely form a presentation."""


class _PresentationSelectionChanged(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializedPresentation:
    """Project coordinates plus the transport-neutral presentation value."""

    episode: int
    resource_type: str
    script_file: str
    transition_to_next: str
    presentation: PresentationValue
    subtitle_artifact_path: str | None
    presentation_artifact_path: str | None

    @property
    def persisted(self) -> bool:
        return self.presentation_artifact_path is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "episode": self.episode,
            "resource_type": self.resource_type,
            "script_file": self.script_file,
            "transition_to_next": self.transition_to_next,
            "subtitle_artifact_path": self.subtitle_artifact_path,
            "presentation_artifact_path": self.presentation_artifact_path,
            "persisted": self.persisted,
            **self.presentation.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _SelectedVersion:
    record: Mapping[str, Any]
    target: TypedMediaRestoreTarget | None
    path: Path
    relative_path: str
    version: int
    selection: MediaSelection


class PresentationReadModelService:
    """Materialize current or historical presentations through one project seam."""

    def __init__(
        self,
        project_manager: ProjectManager,
        *,
        settings_resolver_factory: SettingsResolverFactory | None = None,
        duration_probe: DurationProbe | None = None,
        video_duration_probe: DurationProbe = probe_existing_video_duration_seconds,
        audio_duration_probe: DurationProbe = probe_existing_media_duration_seconds,
        content_digest: ContentDigest = media_content_digest,
    ) -> None:
        self._project_manager = project_manager
        self._settings_resolver_factory = settings_resolver_factory or (
            lambda project_name, project_path: CurrentTtsSettingsResolver(
                project_name,
                project_path=project_path,
            )
        )
        self._video_duration_probe = duration_probe or video_duration_probe
        self._audio_duration_probe = duration_probe or audio_duration_probe
        self._content_digest = content_digest

    async def materialize_unit(
        self,
        *,
        project_name: str,
        resource_type: str,
        resource_id: str,
        variant: RenditionVariant,
        video_version: int | None = None,
        audio_version: int | None = None,
    ) -> MaterializedPresentation:
        for attempt in range(2):
            async with generation_admission_lock(
                project_name=project_name,
                script_file="",
                resource_id=resource_id,
            ):
                try:
                    return await self._materialize_unit_once(
                        project_name=project_name,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        variant=variant,
                        video_version=video_version,
                        audio_version=audio_version,
                    )
                except _PresentationSelectionChanged:
                    if attempt > 0:
                        raise PresentationUnavailableError(
                            "media selection kept changing while the presentation was materialized"
                        ) from None
        raise AssertionError("presentation selection retry loop did not return")

    async def _materialize_unit_once(
        self,
        *,
        project_name: str,
        resource_type: str,
        resource_id: str,
        variant: RenditionVariant,
        video_version: int | None = None,
        audio_version: int | None = None,
    ) -> MaterializedPresentation:
        if resource_type not in {"videos", "reference_videos"}:
            raise PresentationUnavailableError(f"unsupported presentation resource type: {resource_type!r}")
        if variant not in {POST_PRODUCTION, USE_TTS}:
            raise PresentationUnavailableError(f"unsupported rendition variant: {variant!r}")
        if variant == POST_PRODUCTION and audio_version is not None:
            raise PresentationUnavailableError("post_production cannot select a narration-audio version")

        project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        project, versions, selected_video, video_content_digest = await asyncio.to_thread(
            self._load_video_selection,
            project_name=project_name,
            project_path=project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            version=video_version,
        )
        if selected_video.target is None:
            return await self._materialize_manual_upload(
                project_name=project_name,
                project=project,
                project_path=project_path,
                versions=versions,
                resource_type=resource_type,
                resource_id=resource_id,
                selected=selected_video,
            )
        script_file = self._current_episode_script(project, selected_video.target.episode)
        script = await asyncio.to_thread(self._project_manager.load_script, project_name, script_file)
        item, kind = self._find_item(script, resource_id)
        admission = admit_script_unit(kind, item)
        if not admission.allowed or admission.mode is None:
            raise PresentationUnavailableError(f"unit speech is not presentable: {resource_id}")
        if variant == USE_TTS and admission.mode is not SpeechMode.NARRATOR_VOICEOVER:
            raise PresentationUnavailableError("use_tts presentation requires narrator voiceover")

        settings = await self._resolve_settings(project_name, project_path, project)
        video_currency = await asyncio.to_thread(
            self._video_currency,
            project_path=project_path,
            project=project,
            script=script,
            resource_type=resource_type,
            resource_id=resource_id,
            versions=versions,
            selected=selected_video,
            settings=settings,
        )
        provider_audio_enabled = selected_video.record.get("execution_generate_audio")
        if not isinstance(provider_audio_enabled, bool):
            raise PresentationUnavailableError("video version does not record its provider-audio setting")
        video_media = await self._materialize_media(
            selected_video,
            currency=video_currency,
            duration_probe=self._video_duration_probe,
            content_digest=video_content_digest,
        )

        selected_audio: _SelectedVersion | None = None
        audio_media: PresentationMedia | None = None
        if variant == USE_TTS:
            selected_audio = await asyncio.to_thread(
                self._load_selection,
                project_path=project_path,
                versions=versions,
                resource_type="audio",
                resource_id=resource_id,
                version=audio_version,
            )
            if selected_audio.target is None:
                raise PresentationUnavailableError("narration audio lacks typed presentation provenance")
            if selected_audio.target.episode != selected_video.target.episode:
                raise PresentationUnavailableError("video and narration audio belong to different episodes")
            audio_currency = self._audio_currency(
                preparation=admission.preparation,
                selected=selected_audio,
                settings=settings,
            )
            audio_content_digest = await asyncio.to_thread(
                self._require_current_manifest_selection,
                project_path=project_path,
                resource_type="audio",
                resource_id=resource_id,
                selected=selected_audio,
            )
            audio_media = await self._materialize_media(
                selected_audio,
                currency=audio_currency,
                duration_probe=self._audio_duration_probe,
                content_digest=audio_content_digest,
            )

        try:
            presentation = materialize_speech_presentation(
                admission.preparation,
                variant=variant,
                video=video_media,
                narration_audio=audio_media,
                provider_audio_enabled=provider_audio_enabled,
            )
        except (TypeError, ValueError) as exc:
            raise PresentationUnavailableError("selected media cannot form the requested presentation") from exc
        transition = item.get("transition_to_next")
        result = MaterializedPresentation(
            episode=selected_video.target.episode,
            resource_type=resource_type,
            script_file=script_file,
            transition_to_next=transition if isinstance(transition, str) else "cut",
            presentation=presentation,
            subtitle_artifact_path=None,
            presentation_artifact_path=None,
        )
        if presentation.selection == "current":
            result = await asyncio.to_thread(
                self._persist_current,
                project_name=project_name,
                project_path=project_path,
                versions=versions,
                resource_id=resource_id,
                result=result,
                project_snapshot=project,
                script_snapshot=script,
                selected_video=selected_video,
                selected_audio=selected_audio,
            )
        return result

    async def materialize_episode(
        self,
        *,
        project_name: str,
        episode: int,
        variant: RenditionVariant,
    ) -> tuple[MaterializedPresentation, ...]:
        """Materialize every selected video in canonical script order.

        The requested rendition applies to narrator units. Character and silent
        units have no narrator TTS and therefore retain their post-production
        presentation while preserving the provider track.
        """

        project = await asyncio.to_thread(self._project_manager.load_project, project_name)
        script_file = self._current_episode_script(project, episode)
        script = await asyncio.to_thread(self._project_manager.load_script, project_name, script_file)
        items, id_field, kind = resolve_items(script)
        resource_type = "reference_videos" if kind == "video_units" else "videos"
        project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        versions = VersionManager(project_path)
        results: list[MaterializedPresentation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            resource_id = item.get(id_field)
            if not isinstance(resource_id, str) or not resource_id:
                continue
            admission = admit_script_unit(kind, item)
            audio_version = await asyncio.to_thread(versions.get_current_version, "audio", resource_id)
            effective_variant = (
                USE_TTS
                if variant == USE_TTS and admission.mode is SpeechMode.NARRATOR_VOICEOVER and audio_version > 0
                else POST_PRODUCTION
            )
            version_info = await asyncio.to_thread(versions.get_versions, resource_type, resource_id)
            current_version = version_info.get("current_version")
            if type(current_version) is not int or current_version <= 0:
                continue
            results.append(
                await self.materialize_unit(
                    project_name=project_name,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    variant=effective_variant,
                )
            )
        return tuple(results)

    def _load_video_selection(
        self,
        *,
        project_name: str,
        project_path: Path,
        resource_type: str,
        resource_id: str,
        version: int | None,
    ) -> tuple[dict[str, Any], VersionManager, _SelectedVersion, str | None]:
        project = self._project_manager.load_project(project_name)
        versions = VersionManager(project_path)
        selected = self._load_selection(
            project_path=project_path,
            versions=versions,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
        )
        content_digest = self._require_current_manifest_selection(
            project_path=project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            selected=selected,
        )
        return project, versions, selected, content_digest

    @staticmethod
    def _load_selection(
        *,
        project_path: Path,
        versions: VersionManager,
        resource_type: str,
        resource_id: str,
        version: int | None,
    ) -> _SelectedVersion:
        info = versions.get_versions(resource_type, resource_id)
        records = info.get("versions")
        candidates = records if isinstance(records, list) else []
        selected_record = next(
            (
                record
                for record in candidates
                if isinstance(record, Mapping)
                and (record.get("version") == version if version is not None else record.get("is_current") is True)
            ),
            None,
        )
        if selected_record is None:
            raise PresentationUnavailableError(f"media version is unavailable: {resource_type}/{resource_id}")
        try:
            target = _presentation_restore_target(resource_type, selected_record)
        except (TypeError, ValueError) as exc:
            raise PresentationUnavailableError("media version lacks typed presentation provenance") from exc
        raw_path = selected_record.get("file")
        raw_version = selected_record.get("version")
        if not isinstance(raw_path, str) or type(raw_version) is not int or raw_version <= 0:
            raise PresentationUnavailableError("media version record has an invalid file identity")
        try:
            path = safe_join(project_path, raw_path, require_file=True)
        except (FileNotFoundError, ValueError) as exc:
            raise PresentationUnavailableError("selected media file is unavailable") from exc
        return _SelectedVersion(
            record=selected_record,
            target=target,
            path=path,
            relative_path=raw_path,
            version=raw_version,
            selection="current" if selected_record.get("is_current") is True else "history",
        )

    def _require_current_manifest_selection(
        self,
        *,
        project_path: Path,
        resource_type: str,
        resource_id: str,
        selected: _SelectedVersion,
    ) -> str | None:
        artifact_path = self._require_current_manifest_pointer(
            project_path=project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            selected=selected,
        )
        if artifact_path is None:
            return None
        try:
            canonical = safe_join(project_path, artifact_path, require_file=True)
        except (FileNotFoundError, ValueError) as exc:
            raise PresentationUnavailableError("current media file is unavailable") from exc
        try:
            canonical_digest = self._content_digest(canonical)
            selected_digest = self._content_digest(selected.path)
        except (OSError, TypeError, ValueError) as exc:
            raise PresentationUnavailableError("current media selection cannot be verified") from exc
        if canonical_digest != selected_digest:
            raise PresentationUnavailableError("current media and selected version snapshot differ")
        return selected_digest

    @staticmethod
    def _require_current_manifest_pointer(
        *,
        project_path: Path,
        resource_type: str,
        resource_id: str,
        selected: _SelectedVersion,
    ) -> str | None:
        if selected.selection != "current" or selected.target is None:
            return None
        artifact_path = resource_relative_path(resource_type, resource_id)
        adapter = ProjectArtifactManifestAdapter(project_path)
        try:
            observation = adapter.inspect_artifact(artifact_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise PresentationUnavailableError("current media manifest cannot be inspected") from exc
        key = (
            ArtifactKey.episode_audio(selected.target.episode, resource_id)
            if resource_type == "audio"
            else ArtifactKey.episode_video(selected.target.episode, resource_id)
        )
        expected = ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=selected.target.basis.digest)
        if observation.blocker is not None or not observation.present or adapter.get_entry(key) != expected:
            raise PresentationUnavailableError("current media selection is not backed by its typed manifest entry")
        return artifact_path

    @staticmethod
    def _current_episode_script(project: Mapping[str, Any], episode: int) -> str:
        entries = project.get("episodes")
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, Mapping) and entry.get("episode") == episode:
                script_file = entry.get("script_file")
                if isinstance(script_file, str) and script_file:
                    return ProjectManager.normalize_script_filename(script_file)
        raise PresentationUnavailableError(f"episode script is unavailable: {episode}")

    @staticmethod
    def _find_item(script: dict[str, Any], resource_id: str) -> tuple[dict[str, Any], str]:
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
            raise PresentationUnavailableError(f"script unit is unavailable: {resource_id}")
        return item, kind

    async def _resolve_settings(
        self,
        project_name: str,
        project_path: Path,
        project: dict[str, Any],
    ) -> TtsSynthesisSettings | None:
        resolver = self._settings_resolver_factory(project_name, project_path)
        try:
            return await resolver.resolve_tts_synthesis_settings(project)
        except ValueError:
            return None

    @staticmethod
    def _video_currency(
        *,
        project_path: Path,
        project: dict[str, Any],
        script: dict[str, Any],
        resource_type: str,
        resource_id: str,
        versions: VersionManager,
        selected: _SelectedVersion,
        settings: TtsSynthesisSettings | None,
    ) -> MediaCurrency:
        if selected.target is None:
            raise PresentationUnavailableError("video currency requires typed presentation provenance")
        current = build_current_video_artifact_basis(
            project_path=project_path,
            project=project,
            script=script,
            resource_type=resource_type,
            resource_id=resource_id,
            versions=versions,
            version_metadata=selected.record,
            current_tts_settings=settings,
        )
        return "current" if current == selected.target.basis else "stale"

    @staticmethod
    def _audio_currency(
        *,
        preparation: Any,
        selected: _SelectedVersion,
        settings: TtsSynthesisSettings | None,
    ) -> MediaCurrency:
        if selected.target is None:
            raise PresentationUnavailableError("audio currency requires typed presentation provenance")
        if settings is None:
            return "stale"
        expected = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis(preparation, settings))
        return "current" if expected == selected.target.basis else "stale"

    async def _materialize_media(
        self,
        selected: _SelectedVersion,
        *,
        currency: MediaCurrency,
        duration_probe: DurationProbe,
        content_digest: str | None,
    ) -> PresentationMedia:
        if selected.target is None:
            raise PresentationUnavailableError("verified presentation media requires typed provenance")
        duration = await duration_probe(selected.path)
        if duration is None:
            raise PresentationUnavailableError(f"selected media duration is unavailable: {selected.relative_path}")
        try:
            observed_digest = content_digest or await asyncio.to_thread(self._content_digest, selected.path)
            evidence = SelectedMediaEvidence(
                basis=selected.target.basis,
                content_digest=observed_digest,
                actual_duration_seconds=round(duration * 1_000_000) / 1_000_000,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise PresentationUnavailableError(f"selected media cannot be inspected: {selected.relative_path}") from exc
        return PresentationMedia(
            artifact_path=selected.relative_path,
            version=selected.version,
            selection=selected.selection,
            currency=currency,
            evidence=evidence,
        )

    async def _materialize_manual_upload(
        self,
        *,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        versions: VersionManager,
        resource_type: str,
        resource_id: str,
        selected: _SelectedVersion,
    ) -> MaterializedPresentation:
        episode, script_file, item = await self._locate_unverified_video_unit(
            project_name=project_name,
            project=project,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        duration = await self._video_duration_probe(selected.path)
        if duration is None:
            raise PresentationUnavailableError(f"selected media duration is unavailable: {selected.relative_path}")
        try:
            media = RawPresentationMedia(
                artifact_path=selected.relative_path,
                version=selected.version,
                selection=selected.selection,
                content_digest=await asyncio.to_thread(self._content_digest, selected.path),
                actual_duration_seconds=round(duration * 1_000_000) / 1_000_000,
            )
            presentation = materialize_raw_video_presentation(unit_id=resource_id, video=media)
        except (OSError, TypeError, ValueError) as exc:
            raise PresentationUnavailableError(f"selected media cannot be inspected: {selected.relative_path}") from exc
        transition = item.get("transition_to_next")
        await asyncio.to_thread(
            self._require_selection_unchanged,
            project_path=project_path,
            versions=versions,
            resource_type=resource_type,
            resource_id=resource_id,
            selected=selected,
        )
        return MaterializedPresentation(
            episode=episode,
            resource_type=resource_type,
            script_file=script_file,
            transition_to_next=transition if isinstance(transition, str) else "cut",
            presentation=presentation,
            subtitle_artifact_path=None,
            presentation_artifact_path=None,
        )

    async def _locate_unverified_video_unit(
        self,
        *,
        project_name: str,
        project: Mapping[str, Any],
        resource_type: str,
        resource_id: str,
    ) -> tuple[int, str, dict[str, Any]]:
        matches: list[tuple[int, str, dict[str, Any]]] = []
        entries = project.get("episodes")
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, Mapping):
                continue
            episode = entry.get("episode")
            raw_script_file = entry.get("script_file")
            if type(episode) is not int or episode <= 0 or not isinstance(raw_script_file, str):
                continue
            script_file = ProjectManager.normalize_script_filename(raw_script_file)
            script = await asyncio.to_thread(self._project_manager.load_script, project_name, script_file)
            items, id_field, kind = resolve_items(script)
            expected_type = "reference_videos" if kind == "video_units" else "videos"
            if expected_type != resource_type:
                continue
            item = next(
                (
                    candidate
                    for candidate in items
                    if isinstance(candidate, dict) and str(candidate.get(id_field)) == resource_id
                ),
                None,
            )
            if item is not None:
                matches.append((episode, script_file, item))
        if len(matches) != 1:
            raise PresentationUnavailableError(f"manual-upload unit identity is ambiguous: {resource_id}")
        return matches[0]

    def _require_selection_unchanged(
        self,
        *,
        project_path: Path,
        versions: VersionManager,
        resource_type: str,
        resource_id: str,
        selected: _SelectedVersion,
    ) -> None:
        with versions.locked_version_snapshot(resource_type, resource_id):
            current = self._load_selection(
                project_path=project_path,
                versions=versions,
                resource_type=resource_type,
                resource_id=resource_id,
                version=selected.version,
            )
            if current != selected:
                raise _PresentationSelectionChanged

    def _persist_current(
        self,
        *,
        project_name: str,
        project_path: Path,
        versions: VersionManager,
        resource_id: str,
        result: MaterializedPresentation,
        project_snapshot: dict[str, Any],
        script_snapshot: dict[str, Any],
        selected_video: _SelectedVersion,
        selected_audio: _SelectedVersion | None,
    ) -> MaterializedPresentation:
        presentation = result.presentation
        if not isinstance(presentation, SpeechPresentation):
            raise TypeError("only verified speech presentations may be persisted")
        with self._project_manager.locked_project_script_snapshot(
            project_name,
            result.script_file,
        ) as (current_project, current_script):
            if current_project != project_snapshot or current_script != script_snapshot:
                raise _PresentationSelectionChanged
            with versions.locked_version_snapshot(result.resource_type, resource_id):
                current_video = self._load_selection(
                    project_path=project_path,
                    versions=versions,
                    resource_type=result.resource_type,
                    resource_id=resource_id,
                    version=None,
                )
                if current_video != selected_video:
                    raise _PresentationSelectionChanged
                # The outer admission guard keeps the hashed immutable selection stable. Under
                # the version lock only the current manifest pointer needs to be revalidated.
                self._require_current_manifest_pointer(
                    project_path=project_path,
                    resource_type=result.resource_type,
                    resource_id=resource_id,
                    selected=current_video,
                )
                if selected_audio is not None:
                    current_audio = self._load_selection(
                        project_path=project_path,
                        versions=versions,
                        resource_type="audio",
                        resource_id=resource_id,
                        version=None,
                    )
                    if current_audio != selected_audio:
                        raise _PresentationSelectionChanged
                    self._require_current_manifest_pointer(
                        project_path=project_path,
                        resource_type="audio",
                        resource_id=resource_id,
                        selected=current_audio,
                    )
                return self._persist_current_under_locks(
                    project_path=project_path,
                    resource_id=resource_id,
                    result=result,
                    presentation=presentation,
                )

    @staticmethod
    def _persist_current_under_locks(
        *,
        project_path: Path,
        resource_id: str,
        result: MaterializedPresentation,
        presentation: SpeechPresentation,
    ) -> MaterializedPresentation:
        subtitle_path, presentation_path = presentation_artifact_paths(
            result.episode,
            resource_id,
            presentation.variant,
        )
        subtitle_file = safe_join(project_path, subtitle_path)
        presentation_file = safe_join(project_path, presentation_path)
        subtitle_file.parent.mkdir(parents=True, exist_ok=True)
        presentation_file.parent.mkdir(parents=True, exist_ok=True)
        subtitle_snapshot = subtitle_file.read_bytes() if subtitle_file.is_file() else None
        presentation_snapshot = presentation_file.read_bytes() if presentation_file.is_file() else None
        adapter = ProjectArtifactManifestAdapter(project_path)
        subtitle_key = ArtifactKey.episode_subtitle(result.episode, resource_id, presentation.variant)
        presentation_key = ArtifactKey.episode_presentation(result.episode, resource_id, presentation.variant)
        prior_subtitle_entry = adapter.get_entry(subtitle_key)
        prior_presentation_entry = adapter.get_entry(presentation_key)
        committed = MaterializedPresentation(
            episode=result.episode,
            resource_type=result.resource_type,
            script_file=result.script_file,
            transition_to_next=result.transition_to_next,
            presentation=presentation,
            subtitle_artifact_path=subtitle_path,
            presentation_artifact_path=presentation_path,
        )
        try:
            atomic_write_json(
                subtitle_file,
                presentation.subtitle_artifact_dict(),
            )
            atomic_write_json(presentation_file, committed.to_dict())
            manifest = ArtifactManifest(adapter)
            manifest.register(
                subtitle_key,
                artifact_path=subtitle_path,
                basis=presentation.subtitle_basis,
            )
            manifest.register(
                presentation_key,
                artifact_path=presentation_path,
                basis=presentation.presentation_basis,
            )
        except BaseException:
            try:
                _restore_file(subtitle_file, subtitle_snapshot)
                _restore_file(presentation_file, presentation_snapshot)
                _restore_entry(adapter, subtitle_key, prior_subtitle_entry)
                _restore_entry(adapter, presentation_key, prior_presentation_entry)
            except BaseException as rollback_error:
                raise RuntimeError(
                    "presentation materialization failed and rollback was incomplete"
                ) from rollback_error
            raise
        return committed


def presentation_artifact_paths(episode: int, resource_id: str, variant: RenditionVariant) -> tuple[str, str]:
    if type(episode) is not int or episode <= 0:
        raise ValueError("episode must be a positive integer")
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("resource_id must be a non-empty string")
    if variant not in {POST_PRODUCTION, USE_TTS}:
        raise ValueError(f"unsupported rendition variant: {variant!r}")
    token = base64.urlsafe_b64encode(resource_id.encode("utf-8")).decode("ascii").rstrip("=")
    return (
        f"subtitles/episode_{episode}/{token}.{variant}.json",
        f"presentations/episode_{episode}/{token}.{variant}.json",
    )


def is_presentation_version_available(resource_type: str, record: Mapping[str, Any]) -> bool:
    """Report whether the shared reader can present a video version without restoring it."""

    if resource_type not in {"videos", "reference_videos"}:
        return False
    try:
        _presentation_restore_target(resource_type, record)
    except (TypeError, ValueError):
        return False
    return True


def _presentation_restore_target(
    resource_type: str,
    record: Mapping[str, Any],
) -> TypedMediaRestoreTarget | None:
    try:
        return parse_typed_media_version_record(resource_type, record)
    except (TypeError, ValueError):
        if resource_type in {"videos", "reference_videos"} and record.get("source") == "manual_upload":
            return None
        raise


def _restore_file(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write_bytes(path, snapshot)


def _restore_entry(
    adapter: ProjectArtifactManifestAdapter,
    key: ArtifactKey,
    entry: ArtifactManifestEntry | None,
) -> None:
    if entry is None:
        adapter.delete_entry(key)
    else:
        adapter.put_entry(key, entry)


__all__ = [
    "MaterializedPresentation",
    "PresentationReadModelService",
    "PresentationUnavailableError",
    "is_presentation_version_available",
    "presentation_artifact_paths",
]
