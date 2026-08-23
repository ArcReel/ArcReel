"""Shared MiniMax H3 prompt optimization operation used by Web and Agent."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.asset_types import ASSET_SPECS, normalize_asset_bucket
from lib.db import async_session_factory
from lib.minimax_h3_prompt import (
    H3_MAX_PROMPT_CHARS,
    H3PromptArtifact,
    H3PromptReference,
    H3PromptSections,
    H3PromptState,
    H3PromptTooLongError,
    canonical_basis_digest,
    confirm_h3_prompt_artifact,
    file_sha256,
    is_minimax_h3_model,
    load_h3_prompt_artifact,
    load_h3_system_prompt,
    parse_h3_prompt,
    save_h3_prompt_artifact,
)
from lib.narration_delivery import POST_PRODUCTION, NarrationDelivery
from lib.project_manager import ProjectManager, get_project_manager
from lib.reference_video.prompt_render import render_video_unit_prompt, resolve_reference_audio_paths
from lib.reference_video.request_projection import (
    ReferenceRequestOptions,
    ReferenceUnitRequestProjection,
    project_reference_unit_request,
)
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.text_backends.base import ImageInput, TextGenerationRequest, TextGenerationResult, TextTaskType
from lib.text_generator import TextGenerator
from lib.video_style import UnifiedVideoStyle
from lib.video_visual_provenance import resolve_video_aspect_ratio
from server.services.effective_global_assets import resolve_linked_global_reference_audio_paths
from server.services.narration_delivery_tasks import prepare_current_reference_video_request_options
from server.services.video_style import VideoStyleService

logger = logging.getLogger(__name__)

_H3_OPTIMIZATION_MAX_ATTEMPTS = 3


class H3PromptOptimizationError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class H3PromptContext:
    episode: int
    unit: dict[str, Any]
    projection: ReferenceUnitRequestProjection
    narration_delivery: str
    aspect_ratio: str
    image_references: tuple[H3PromptReference, ...]
    image_paths: tuple[Path, ...]
    audio_references: tuple[H3PromptReference, ...]
    audio_paths: tuple[Path, ...]
    basis_digest: str
    user_prompt: str
    video_style: UnifiedVideoStyle | None = None


GeneratorFactory = Callable[[str], Awaitable[TextGenerator]]


async def _default_generator_factory(project_name: str) -> TextGenerator:
    return await TextGenerator.create(TextTaskType.H3_PROMPT_OPTIMIZATION, project_name)


def _script_file(project: dict[str, Any], episode: int) -> str:
    for item in project.get("episodes") or []:
        if not isinstance(item, dict) or item.get("episode") != episode:
            continue
        raw = item.get("script_file")
        if isinstance(raw, str) and raw.strip():
            return raw.removeprefix("scripts/")
    return f"episode_{episode}.json"


def _relative_path(path: Path, project_path: Path) -> str | None:
    try:
        return str(path.relative_to(project_path))
    except ValueError:
        return None


def _find_units(script: dict[str, Any], unit_ids: Sequence[str] | None) -> list[dict[str, Any]]:
    raw_units = script.get("video_units")
    if not isinstance(raw_units, list):
        raise H3PromptOptimizationError("h3_prompt_script_invalid", "video_units must be a list")
    units = [unit for unit in raw_units if isinstance(unit, dict)]
    if unit_ids is None:
        return units
    requested = list(dict.fromkeys(unit_ids))
    by_id = {str(unit.get("unit_id") or ""): unit for unit in units}
    missing = [unit_id for unit_id in requested if unit_id not in by_id]
    if missing:
        raise H3PromptOptimizationError("h3_prompt_unit_not_found", ", ".join(missing))
    return [by_id[unit_id] for unit_id in requested]


def _asset_summary(project: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    spec = ASSET_SPECS.get(kind)
    if spec is None:
        return {}
    entry = normalize_asset_bucket(project.get(spec.bucket_key)).get(name)
    if not isinstance(entry, dict):
        return {}
    allowed = (
        "description",
        "appearance",
        "visual_description",
        "personality",
        "costume",
        "voice_style",
        "prompt",
    )
    return {key: entry[key] for key in allowed if entry.get(key)}


def _basis_payload(
    *,
    project: dict[str, Any],
    unit: dict[str, Any],
    projection: ReferenceUnitRequestProjection,
    narration_delivery: str,
    aspect_ratio: str,
    images: Sequence[H3PromptReference],
    image_paths: Sequence[Path],
    audios: Sequence[H3PromptReference],
    audio_paths: Sequence[Path],
) -> dict[str, Any]:
    candidate = projection.provider_candidate
    if candidate is None or projection.request_duration is None:
        raise H3PromptOptimizationError("h3_prompt_projection_incomplete")
    return {
        "schema": "minimax-h3-ref2va/v1",
        "unit": {
            "unit_id": unit.get("unit_id"),
            "text": unit.get("text"),
            "duration_seconds": unit.get("duration_seconds"),
        },
        "request": {
            "provider_id": candidate.provider_id,
            "model_id": candidate.model_id,
            "duration_seconds": projection.request_duration.seconds,
            "resolution": candidate.resolution,
            "aspect_ratio": aspect_ratio,
            "narration_delivery": narration_delivery,
            "generate_audio": candidate.requested_generate_audio,
        },
        "project": {
            "style": project.get("style"),
            "style_description": project.get("style_description"),
            "video_style": project.get("video_style"),
            "source_language": project.get("source_language"),
        },
        "reference_images": [
            {
                **reference.model_dump(mode="json"),
                "sha256": file_sha256(path),
                "asset": _asset_summary(project, reference.kind, reference.name),
            }
            for reference, path in zip(images, image_paths, strict=True)
        ],
        "reference_audio": [
            {**reference.model_dump(mode="json"), "sha256": file_sha256(path)}
            for reference, path in zip(audios, audio_paths, strict=True)
        ],
    }


def _optimizer_user_prompt(payload: dict[str, Any]) -> str:
    """Runtime facts are isolated in the user turn; the pinned system prompt stays byte-identical."""

    return (
        "Rewrite the following ArcReel video unit for the configured MiniMax H3 request. "
        "The attached images appear in the same order as reference_images and map to <Picture N>. "
        "Use only the references listed below, preserve all dialogue verbatim in its original language, and "
        "treat project.video_style.prompt as the authoritative project-wide direction for visual treatment, "
        "camera, pacing and sound. Treat every explicit prohibition and requirement in that prompt as a hard "
        "constraint: for example, when it forbids background music, write non_diegetic_music as N/A; when it "
        "requests ASMR, foreground the specified close physical sounds in overall_soundscape. "
        "Keep every timestamp within request.duration_seconds. Return only the six required sections. "
        f"The complete response, including all section headers, must not exceed {H3_MAX_PROMPT_CHARS} "
        "characters.\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _optimizer_retry_prompt(user_prompt: str, error: H3PromptTooLongError) -> str:
    return (
        f"{user_prompt}\n\n"
        f"Your previous response rendered to {error.actual_chars} characters, exceeding the "
        f"{error.max_chars}-character provider limit. Regenerate all six required sections and compress "
        f"the wording so the complete response, including all section headers, does not exceed "
        f"{error.max_chars} characters. Preserve every other instruction and required fact."
    )


async def _generate_valid_h3_prompt(
    generator: TextGenerator,
    *,
    context: H3PromptContext,
    system_prompt: str,
    project_name: str,
) -> tuple[TextGenerationResult, H3PromptSections]:
    """Generate once normally, retrying only provider-length validation failures."""

    user_prompt = context.user_prompt
    duration = context.projection.request_duration
    assert duration is not None
    for attempt in range(1, _H3_OPTIMIZATION_MAX_ATTEMPTS + 1):
        result = await generator.generate(
            TextGenerationRequest(
                prompt=user_prompt,
                system_prompt=system_prompt,
                images=[ImageInput(path=path) for path in context.image_paths] or None,
                max_output_tokens=8192,
            ),
            project_name=project_name,
        )
        try:
            sections = parse_h3_prompt(
                result.text,
                duration_seconds=duration.seconds,
                picture_count=len(context.image_paths),
                audio_count=len(context.audio_paths),
            )
        except H3PromptTooLongError as exc:
            if attempt >= _H3_OPTIMIZATION_MAX_ATTEMPTS:
                raise
            logger.warning(
                "H3 prompt optimization attempt %d/%d exceeded the provider limit (%d > %d characters); retrying",
                attempt,
                _H3_OPTIMIZATION_MAX_ATTEMPTS,
                exc.actual_chars,
                exc.max_chars,
            )
            user_prompt = _optimizer_retry_prompt(context.user_prompt, exc)
            continue
        return result, sections
    raise AssertionError("H3 prompt optimization retry loop did not return")


class H3PromptOptimizationService:
    """Own request projection, optimization, persistence, review and currentness checks."""

    def __init__(
        self,
        project_manager: ProjectManager | None = None,
        *,
        generator_factory: GeneratorFactory = _default_generator_factory,
        video_style_service: VideoStyleService | None = None,
    ) -> None:
        self._pm = project_manager or get_project_manager()
        self._generator_factory = generator_factory
        self._video_style_service = video_style_service or VideoStyleService(self._pm)

    def _load(self, project_name: str, episode: int) -> tuple[dict[str, Any], Path, dict[str, Any], str]:
        project = self._pm.load_project_readonly(project_name)
        project_path = self._pm.get_project_path(project_name)
        script_file = _script_file(project, episode)
        script = self._pm.load_script_readonly(project_name, script_file)
        return project, project_path, script, script_file

    async def ensure_video_style(self, project_name: str, episode: int) -> tuple[UnifiedVideoStyle, bool]:
        """Resolve the project-wide style immediately before H3 optimization."""

        return await self._video_style_service.ensure(project_name, preferred_episode=episode)

    async def context_from_projection(
        self,
        *,
        episode: int,
        project: dict[str, Any],
        project_path: Path,
        unit: dict[str, Any],
        narration_delivery: NarrationDelivery,
        projection: ReferenceUnitRequestProjection,
        audio_map: Mapping[str, Path] | None = None,
    ) -> H3PromptContext:
        if projection.blocking_problems:
            raise H3PromptOptimizationError(
                projection.blocking_problems[0].code,
                projection.blocking_problems[0].reason or projection.blocking_problems[0].code,
            )
        candidate = projection.provider_candidate
        if candidate is None or projection.request_duration is None:
            raise H3PromptOptimizationError("h3_prompt_projection_incomplete")
        if not is_minimax_h3_model(candidate.model_id):
            raise H3PromptOptimizationError("h3_prompt_not_applicable")

        image_refs = tuple(
            H3PromptReference(
                label=f"Picture {index}",
                kind=entry.reference.type,
                name=entry.reference.name,
                path=_relative_path(entry.path, project_path),
            )
            for index, entry in enumerate(projection.request_assets, start=1)
        )
        image_paths = tuple(entry.path for entry in projection.request_assets)

        if audio_map is None:
            resolved_audio = await asyncio.to_thread(resolve_reference_audio_paths, project, project_path)
            resolved_audio.update(
                await resolve_linked_global_reference_audio_paths(
                    project,
                    project_path.parent,
                    session_factory=async_session_factory,
                )
            )
        else:
            resolved_audio = dict(audio_map)
        voice_settings = VoiceRenderSettings(
            voice_consistency=candidate.voice_consistency,
            requested_generate_audio=candidate.requested_generate_audio,
            max_reference_audio=candidate.max_reference_audio_count,
            model_id=candidate.model_id,
            audio_ready=resolved_audio,
            requires_reference_image=candidate.reference_audio_per_image,
        )
        rendered = render_video_unit_prompt(
            unit,
            project,
            voice_settings,
            request_references=[entry.reference for entry in projection.request_assets],
        )
        audio_names = rendered.audio_speakers
        audio_paths = tuple(resolved_audio[name] for name in audio_names)
        audio_refs = tuple(
            H3PromptReference(
                label=f"Audio {index}",
                kind="speaker",
                name=name,
                path=_relative_path(path, project_path),
            )
            for index, (name, path) in enumerate(zip(audio_names, audio_paths, strict=True), start=1)
        )
        aspect_ratio = resolve_video_aspect_ratio(project)
        payload = _basis_payload(
            project=project,
            unit=unit,
            projection=projection,
            narration_delivery=narration_delivery,
            aspect_ratio=aspect_ratio,
            images=image_refs,
            image_paths=image_paths,
            audios=audio_refs,
            audio_paths=audio_paths,
        )
        video_style = (
            UnifiedVideoStyle.model_validate(project["video_style"]) if project.get("video_style") is not None else None
        )
        return H3PromptContext(
            episode=episode,
            unit=unit,
            projection=projection,
            narration_delivery=narration_delivery,
            aspect_ratio=aspect_ratio,
            image_references=image_refs,
            image_paths=image_paths,
            audio_references=audio_refs,
            audio_paths=audio_paths,
            basis_digest=canonical_basis_digest(payload),
            user_prompt=_optimizer_user_prompt(payload),
            video_style=video_style,
        )

    async def _context(
        self,
        *,
        project_name: str,
        episode: int,
        project: dict[str, Any],
        project_path: Path,
        script: dict[str, Any],
        script_file: str,
        unit: dict[str, Any],
        narration_delivery: NarrationDelivery,
        confirmed_duration: int | None,
    ) -> H3PromptContext:
        options = ReferenceRequestOptions(
            narration_delivery=narration_delivery,
            confirmed_request_duration_seconds=confirmed_duration,
        )
        current_options = await prepare_current_reference_video_request_options(
            project=project,
            script=script,
            script_file=script_file,
            unit=unit,
            project_path=project_path,
            options=options,
            project_name=project_name,
            tts_in_progress=False,
        )
        projection = await project_reference_unit_request(
            project=project,
            script=script,
            unit=unit,
            project_path=project_path,
            options=current_options,
            current_options_materialized=True,
        )
        return await self.context_from_projection(
            episode=episode,
            project=project,
            project_path=project_path,
            unit=unit,
            narration_delivery=narration_delivery,
            projection=projection,
        )

    async def _contexts(
        self,
        project_name: str,
        episode: int,
        *,
        unit_ids: Sequence[str] | None,
        narration_delivery: NarrationDelivery,
        confirmed_request_durations: Mapping[str, int] | None,
        ensure_video_style: bool = False,
    ) -> tuple[Path, list[H3PromptContext]]:
        project, project_path, script, script_file = await asyncio.to_thread(self._load, project_name, episode)
        if project.get("generation_mode") != "reference_video":
            units = _find_units(script, unit_ids)
            raise H3PromptOptimizationError(
                "h3_prompt_not_applicable",
                ",".join(str(unit.get("unit_id") or "") for unit in units),
            )
        if ensure_video_style and project.get("video_style") is None:
            style, _created = await self.ensure_video_style(project_name, episode)
            project["video_style"] = style.model_dump(mode="json")
        units = _find_units(script, unit_ids)
        contexts: list[H3PromptContext] = []
        for unit in units:
            unit_id = str(unit.get("unit_id") or "")
            contexts.append(
                await self._context(
                    project_name=project_name,
                    episode=episode,
                    project=project,
                    project_path=project_path,
                    script=script,
                    script_file=script_file,
                    unit=unit,
                    narration_delivery=narration_delivery,
                    confirmed_duration=(confirmed_request_durations or {}).get(unit_id),
                )
            )
        return project_path, contexts

    async def states(
        self,
        project_name: str,
        episode: int,
        *,
        unit_ids: Sequence[str] | None = None,
        narration_delivery: NarrationDelivery = POST_PRODUCTION,
        confirmed_request_durations: Mapping[str, int] | None = None,
    ) -> list[H3PromptState]:
        try:
            project_path, contexts = await self._contexts(
                project_name,
                episode,
                unit_ids=unit_ids,
                narration_delivery=narration_delivery,
                confirmed_request_durations=confirmed_request_durations,
            )
        except H3PromptOptimizationError as exc:
            if exc.code != "h3_prompt_not_applicable":
                raise
            _project, _path, script, _file = await asyncio.to_thread(self._load, project_name, episode)
            return [
                H3PromptState(unit_id=str(unit.get("unit_id") or ""), state="not_applicable")
                for unit in _find_units(script, unit_ids)
            ]
        states: list[H3PromptState] = []
        for context in contexts:
            unit_id = str(context.unit.get("unit_id") or "")
            artifact = load_h3_prompt_artifact(project_path, episode, unit_id)
            if artifact is None:
                state = "missing"
            elif artifact.basis_digest != context.basis_digest:
                state = "stale"
            else:
                state = artifact.status
            states.append(H3PromptState(unit_id=unit_id, state=state, artifact=artifact))
        return states

    def state_for_context(self, project_path: Path, context: H3PromptContext) -> H3PromptState:
        unit_id = str(context.unit.get("unit_id") or "")
        artifact = load_h3_prompt_artifact(project_path, context.episode, unit_id)
        if artifact is None:
            state = "missing"
        elif artifact.basis_digest != context.basis_digest:
            state = "stale"
        else:
            state = artifact.status
        return H3PromptState(unit_id=unit_id, state=state, artifact=artifact)

    async def optimize(
        self,
        project_name: str,
        episode: int,
        *,
        unit_ids: Sequence[str] | None = None,
        narration_delivery: NarrationDelivery = POST_PRODUCTION,
        confirmed_request_durations: Mapping[str, int] | None = None,
    ) -> list[H3PromptArtifact]:
        project_path, contexts = await self._contexts(
            project_name,
            episode,
            unit_ids=unit_ids,
            narration_delivery=narration_delivery,
            confirmed_request_durations=confirmed_request_durations,
            ensure_video_style=True,
        )
        return await self._optimize_contexts(project_name, project_path, contexts)

    async def _optimize_contexts(
        self,
        project_name: str,
        project_path: Path,
        contexts: Sequence[H3PromptContext],
    ) -> list[H3PromptArtifact]:
        """Optimize already-projected request contexts and persist their artifacts."""

        if not contexts:
            return []
        generator = await self._generator_factory(project_name)
        system_prompt = load_h3_system_prompt()
        artifacts: list[H3PromptArtifact] = []
        for context in contexts:
            result, sections = await _generate_valid_h3_prompt(
                generator,
                context=context,
                system_prompt=system_prompt,
                project_name=project_name,
            )
            duration = context.projection.request_duration
            candidate = context.projection.provider_candidate
            assert duration is not None and candidate is not None
            unit_id = str(context.unit.get("unit_id") or "")
            artifact = H3PromptArtifact(
                episode=context.episode,
                unit_id=unit_id,
                sections=sections,
                rendered_prompt=sections.render(),
                basis_digest=context.basis_digest,
                model_id=candidate.model_id,
                optimizer_provider=result.provider,
                optimizer_model=result.model,
                request_duration_seconds=duration.seconds,
                resolution=candidate.resolution,
                aspect_ratio=context.aspect_ratio,
                narration_delivery=context.narration_delivery,
                reference_images=list(context.image_references),
                reference_audio=list(context.audio_references),
                optimized_at=datetime.now(UTC).isoformat(),
            )
            await asyncio.to_thread(save_h3_prompt_artifact, project_path, artifact)
            artifacts.append(artifact)
        return artifacts

    async def optimized_prompt_for_context(
        self,
        project_name: str,
        project_path: Path,
        context: H3PromptContext,
    ) -> str:
        """Return the current prompt, optimizing automatically when absent or stale."""

        unit_id = str(context.unit.get("unit_id") or "")
        artifact = load_h3_prompt_artifact(project_path, context.episode, unit_id)
        if artifact is not None and artifact.basis_digest == context.basis_digest:
            return artifact.rendered_prompt
        artifacts = await self._optimize_contexts(project_name, project_path, [context])
        if not artifacts:
            raise H3PromptOptimizationError("h3_prompt_missing", unit_id)
        return artifacts[0].rendered_prompt

    async def confirm(
        self,
        project_name: str,
        episode: int,
        *,
        unit_ids: Sequence[str] | None = None,
        narration_delivery: NarrationDelivery = POST_PRODUCTION,
        confirmed_request_durations: Mapping[str, int] | None = None,
    ) -> list[H3PromptArtifact]:
        project_path, contexts = await self._contexts(
            project_name,
            episode,
            unit_ids=unit_ids,
            narration_delivery=narration_delivery,
            confirmed_request_durations=confirmed_request_durations,
        )
        return [
            await asyncio.to_thread(
                confirm_h3_prompt_artifact,
                project_path,
                episode,
                str(context.unit.get("unit_id") or ""),
                expected_basis_digest=context.basis_digest,
            )
            for context in contexts
        ]

    async def confirmed_prompt_for_context(
        self,
        project_path: Path,
        context: H3PromptContext,
    ) -> str:
        unit_id = str(context.unit.get("unit_id") or "")
        artifact = load_h3_prompt_artifact(project_path, context.episode, unit_id)
        if artifact is None:
            raise H3PromptOptimizationError("h3_prompt_missing", unit_id)
        if artifact.basis_digest != context.basis_digest:
            raise H3PromptOptimizationError("h3_prompt_stale", unit_id)
        if artifact.status != "confirmed":
            raise H3PromptOptimizationError("h3_prompt_pending_review", unit_id)
        return artifact.rendered_prompt


__all__ = [
    "H3PromptContext",
    "H3PromptOptimizationError",
    "H3PromptOptimizationService",
]
