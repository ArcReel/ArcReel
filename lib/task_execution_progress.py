"""Durable, user-visible execution progress projections."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

H3_PROGRESS_KIND = "minimax_h3"

H3ProgressPhase = Literal[
    "style_analyzing",
    "prompt_optimizing",
    "submitted",
    "queued",
    "preparing",
    "running",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
]


class H3ExecutionProgress(TypedDict):
    kind: Literal["minimax_h3"]
    phase: H3ProgressPhase
    provider_status: str | None
    stage: str | None
    progress: int | None
    can_cancel: bool
    queue_position: int | None
    queue_length: int | None
    queue_ahead: int | None


def h3_execution_progress(
    phase: H3ProgressPhase,
    *,
    provider_status: str | None = None,
    stage: str | None = None,
    progress: int | float | None = None,
    can_cancel: bool = False,
    queue_position: int | None = None,
    queue_length: int | None = None,
) -> H3ExecutionProgress:
    """Build the stable Web/API projection from Croco's provider response."""
    normalized_progress = None if progress is None else max(0, min(100, round(progress)))
    normalized_position = queue_position if isinstance(queue_position, int) and queue_position > 0 else None
    normalized_length = queue_length if isinstance(queue_length, int) and queue_length >= 0 else None
    return {
        "kind": H3_PROGRESS_KIND,
        "phase": phase,
        "provider_status": provider_status,
        "stage": stage,
        "progress": normalized_progress,
        "can_cancel": can_cancel,
        "queue_position": normalized_position,
        "queue_length": normalized_length,
        "queue_ahead": max(normalized_position - 1, 0) if normalized_position is not None else None,
    }


async def persist_h3_execution_progress(task_id: str | None, progress: H3ExecutionProgress) -> None:
    """Best-effort progress persistence; generation correctness never depends on UI metadata."""
    if task_id is None:
        return
    from lib.generation_queue import get_generation_queue

    try:
        raw = json.dumps(progress, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        await get_generation_queue().persist_execution_progress(task_id, raw)
    except Exception:
        logger.warning("H3 execution progress persistence failed task_id=%s", task_id, exc_info=True)


def h3_progress_from_provider(job: dict[str, Any], queue: dict[str, Any] | None = None) -> H3ExecutionProgress:
    """Map Croco lifecycle/status fields to ArcReel's H3-only phase contract."""
    status = str(job.get("status") or "unknown")
    phase_by_status: dict[str, H3ProgressPhase] = {
        "accepted": "submitted",
        "blocked": "failed",
        "queued": "queued",
        "preparing": "preparing",
        "running": "running",
        "canceling": "cancelling",
        "succeeded": "completed",
        "failed": "failed",
        "canceled": "cancelled",
    }
    phase = phase_by_status.get(status, "submitted")
    queue = queue or {}
    return h3_execution_progress(
        phase,
        provider_status=status,
        stage=job.get("stage") if isinstance(job.get("stage"), str) else None,
        progress=job.get("progress") if isinstance(job.get("progress"), (int, float)) else None,
        can_cancel=bool(job.get("can_cancel")),
        queue_position=queue.get("position") if isinstance(queue.get("position"), int) else None,
        queue_length=queue.get("queue_length") if isinstance(queue.get("queue_length"), int) else None,
    )
