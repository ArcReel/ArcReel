"""Host-neutral tool for storyboard image generation (narration / drama)."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.generation.generation_result import (
    GenerationResultBuilder,
    normalize_requested_ids,
    record_batch_outcomes,
)
from lib.infra.async_thread import run_sync_transaction
from lib.project.resource_paths import resource_relative_path
from lib.script.script_models import resolve_content_mode
from lib.script.script_skeleton import ensure_route_skeleton
from server.media_tools.context import (
    GenerationToolValue,
    RequestedIds,
    ScriptFilename,
    generation_batch_submission_outcome,
    generation_result_outcome,
    tool_error,
)
from server.services.admission.storyboard_batch import (
    STORYBOARD_BATCH_OPERATION,
    StoryboardImageBatchPlan,
    plan_storyboard_image_batch,
)
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest, submit_media_generation

_OPERATION = STORYBOARD_BATCH_OPERATION


class _FailureRecorder:
    """Records storyboard failures to ``storyboards/generation_failures.json``."""

    def __init__(self, output_dir: Path) -> None:
        self.output_path = output_dir / "generation_failures.json"
        self.failures: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, resource_id: str, resource_type: str, error: str, attempts: int = 3) -> None:
        """Append a failure entry. ``resource_type`` is ``segment`` (narration)
        or ``scene`` (drama) — driven by the script's ``id_field``."""
        with self._lock:
            self.failures.append(
                {
                    "resource_id": resource_id,
                    "type": resource_type,
                    "error": error,
                    "attempts": attempts,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

    def save(self) -> None:
        if not self.failures:
            return
        with self._lock:
            data = {
                "generated_at": datetime.now(UTC).isoformat(),
                "total_failures": len(self.failures),
                "failures": self.failures,
            }
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class GenerateStoryboardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="Script filename only (no directory), for example episode_1.json")
    segment_ids: RequestedIds | SkipJsonSchema[None] = Field(
        default=None,
        description="Storyboard IDs (segment_id / scene_id) to generate or regenerate; omit to select only storyboards missing images",
    )


async def generate_storyboards(
    request: ToolRequest[GenerateStoryboardsRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GenerationToolValue]:
    try:
        script_filename = request.value.script
        segment_ids = normalize_requested_ids(request.value.segment_ids, field="segment_ids")

        def plan_batch() -> tuple[Path, ArtifactCurrencyResolver, StoryboardImageBatchPlan]:
            script = services.projects.load_script(scope.project_name, script_filename)
            project_dir = services.projects.get_project_path(scope.project_name)

            try:
                project_data = services.projects.load_project(scope.project_name)
            except FileNotFoundError:
                # Missing project.json may degrade to an empty dict, using default style;
                # let outer tool_error expose corrupt JSON, permission errors and other exceptions,
                # otherwise enqueueing silently continues with empty style and loses configuration.
                project_data = {}

            if project_data:
                # Reject mismatched scripts here: missing required storyboard-to-video arrays would otherwise
                # yield false success with empty results, hiding the cause. With no project.json there is no generation mode,
                # so retain the fallback above.
                ensure_route_skeleton(
                    script, resolve_content_mode(script, project_data), project_data.get("generation_mode")
                )

            resolver = active_artifact_currency_resolver(project_dir, project_data)
            episode = (
                resolve_artifact_episode(
                    project=project_data,
                    script=script,
                    script_filename=script_filename,
                )
                or 1
            )
            plan = plan_storyboard_image_batch(
                project=project_data,
                script=script,
                script_file=script_filename,
                episode=episode,
                resolver=resolver,
                requested_ids=segment_ids,
            )
            return project_dir, resolver, plan

        # Project/script reads, currency-resolver construction and content-based currency checks use synchronous file I/O and hashing.
        project_dir, resolver, plan = await run_sync_transaction(plan_batch)
        by_id = plan.states
        specs = plan.task_specs(source=caller.source)

        submitted = await submit_media_generation(
            scope=scope,
            caller=caller,
            services=services,
            operation=_OPERATION,
            preflight=plan.preflight,
            pending_ids=plan.target_ids,
            specs=specs,
            states=by_id,
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_outcome(submitted.batch)
        if specs:
            recorder = _FailureRecorder(project_dir / "storyboards")
            # narration → segment_id / drama → scene_id: ``id_field`` is the canonical
            # script field name; ``"segment"`` / ``"scene"`` are the corresponding resource types.
            resource_type = "segment" if plan.id_field == "segment_id" else "scene"
            for br in submitted.failures:
                recorder.record(br.resource_id, resource_type, br.error or "unknown")
            await run_sync_transaction(recorder.save)

            builder = GenerationResultBuilder.from_preflight(plan.preflight)
            await asyncio.to_thread(
                record_batch_outcomes,
                builder,
                successes=submitted.successes,
                failures=submitted.failures,
                states=by_id,
                resolver=resolver,
                fallback_path=lambda rid: resource_relative_path("storyboards", rid),
            )

            return generation_result_outcome(builder.build(), batch_id=submitted.batch.batch_id)
        return generation_result_outcome(plan.preflight, batch_id=submitted.batch.batch_id)
    except Exception as exc:
        return tool_error(_OPERATION, exc)


__all__ = ["GenerateStoryboardsRequest", "generate_storyboards"]
