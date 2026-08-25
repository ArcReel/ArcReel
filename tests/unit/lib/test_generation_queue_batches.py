"""Real SQLite behavior for durable generation batches."""

from __future__ import annotations

import pytest

from lib.generation_batch import (
    GenerationBatchBlockedItem,
    GenerationBatchRequestedItem,
    GenerationBatchRequestSnapshot,
)
from lib.generation_queue import GenerationBatchNotFound, GenerationQueue
from lib.generation_result import (
    GenerationAction,
    GenerationItemResult,
    GenerationItemState,
    GenerationProblem,
    GenerationSelectionMode,
)


@pytest.fixture
async def batch_queue(db_factory):
    return GenerationQueue(session_factory=db_factory)


def _snapshot(*unit_ids: str) -> GenerationBatchRequestSnapshot:
    return GenerationBatchRequestSnapshot(
        selection=GenerationSelectionMode.EXPLICIT,
        requested=[GenerationBatchRequestedItem(unit_id=unit_id) for unit_id in unit_ids],
    )


def _blocked(unit_id: str) -> GenerationBatchBlockedItem:
    return GenerationBatchBlockedItem(
        item=GenerationItemResult(
            unit_id=unit_id,
            state=GenerationItemState.BLOCKED,
            problem=GenerationProblem(
                code="admission_blocked", detail="input unavailable", action=GenerationAction.FIX_INPUT
            ),
        ),
        admission={"unit_id": unit_id, "admitted": False, "projection": {"duration_seconds": 6}},
    )


async def _enqueue(queue: GenerationQueue, batch_id: str, unit_id: str) -> dict:
    return await queue.enqueue_task(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id=unit_id,
        script_file="episode_01.json",
        batch_id=batch_id,
        batch_unit_id=unit_id,
    )


async def test_deduped_task_keeps_original_owner_and_belongs_to_both_batches(
    batch_queue: GenerationQueue,
) -> None:
    first_batch = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=_snapshot("E1S01", "E1S02"),
        blocked=[_blocked("E1S02")],
        source="mcp",
    )
    first = await _enqueue(batch_queue, first_batch, "E1S01")

    second_batch = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=_snapshot("E1S01"),
        blocked=[],
        source="mcp",
    )
    second = await _enqueue(batch_queue, second_batch, "E1S01")

    assert second == {**first, "deduped": True, "existing_task_id": first["task_id"]}
    task = await batch_queue.get_task(first["task_id"])
    assert task is not None and task["batch_id"] == first_batch
    first_read = await batch_queue.get_generation_batch(project_name="demo", batch_id=first_batch)
    second_read = await batch_queue.get_generation_batch(project_name="demo", batch_id=second_batch)
    assert [(member.unit_id, member.deduped) for member in first_read.members] == [
        ("E1S01", False),
        ("E1S02", False),
    ]
    assert first_read.members[1].admission["projection"] == {"duration_seconds": 6}
    assert [(member.unit_id, member.deduped) for member in second_read.members] == [("E1S01", True)]
    with pytest.raises(GenerationBatchNotFound):
        await batch_queue.get_generation_batch(project_name="other", batch_id=first_batch)

    running = await batch_queue.claim_next_task("image")
    assert running is not None
    await batch_queue.mark_task_succeeded(first["task_id"], {"file_path": "storyboards/E1S01.png"})
    terminal = await batch_queue.get_generation_batch(project_name="demo", batch_id=first_batch)
    assert terminal.done is True
    assert terminal.generation_result is not None
    assert terminal.generation_result.requested == ["E1S01", "E1S02"]
    assert terminal.generation_result.succeeded == ["E1S01"]
    assert terminal.generation_result.blocked == ["E1S02"]


async def test_batch_membership_rejects_another_project_or_unrequested_unit(
    batch_queue: GenerationQueue,
) -> None:
    batch_id = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=_snapshot("E1S01", "E1S02"),
        blocked=[_blocked("E1S02")],
        source="mcp",
    )

    for project_name, unit_id in (("other", "E1S01"), ("demo", "E1S02"), ("demo", "E1S03")):
        with pytest.raises(ValueError, match="does not own unit"):
            await batch_queue.enqueue_task(
                project_name=project_name,
                task_type="storyboard",
                media_type="image",
                resource_id=unit_id,
                script_file="episode_01.json",
                batch_id=batch_id,
                batch_unit_id=unit_id,
            )

    assert (await batch_queue.list_tasks(project_name="other"))["items"] == []


async def test_unassociated_requested_member_is_a_durable_enqueue_failure(
    batch_queue: GenerationQueue,
) -> None:
    batch_id = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=_snapshot("E1S01"),
        blocked=[],
        source="mcp",
    )

    terminal = await batch_queue.get_generation_batch(project_name="demo", batch_id=batch_id)

    assert terminal.done is True
    assert terminal.counts.failed == 1
    assert terminal.members[0].problem is not None
    assert terminal.members[0].problem.code == "generation_enqueue_failed"
    assert terminal.generation_result is not None
    assert terminal.generation_result.requested == ["E1S01"]
    assert terminal.generation_result.failed == ["E1S01"]
    assert terminal.generation_result.items[0].task_state.value == "not_queued"


async def test_batch_cancel_uses_task_state_machine_and_is_idempotent(
    batch_queue: GenerationQueue,
) -> None:
    batch_id = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=_snapshot("running", "finished", "queued"),
        blocked=[],
        source="mcp",
    )
    running = await _enqueue(batch_queue, batch_id, "running")
    claimed = await batch_queue.claim_next_task("image")
    assert claimed is not None and claimed["task_id"] == running["task_id"]
    finished = await _enqueue(batch_queue, batch_id, "finished")
    claimed = await batch_queue.claim_next_task("image")
    assert claimed is not None and claimed["task_id"] == finished["task_id"]
    await batch_queue.mark_task_succeeded(finished["task_id"], {})
    queued = await _enqueue(batch_queue, batch_id, "queued")

    active = await batch_queue.get_generation_batch(project_name="demo", batch_id=batch_id)
    assert active.done is False
    assert active.counts.model_dump() == {
        "queued": 1,
        "running": 1,
        "cancelling": 0,
        "succeeded": 1,
        "failed": 0,
        "cancelled": 0,
        "blocked": 0,
        "total": 3,
    }
    assert active.poll_after_seconds is not None

    callbacks: list[str] = []
    batch_queue.set_worker_cancel_callback(lambda task_id: not callbacks.append(task_id))
    cancelled = await batch_queue.cancel_generation_batch(project_name="demo", batch_id=batch_id)
    assert cancelled.cancelled == [queued["task_id"]]
    assert cancelled.cancelling == [running["task_id"]]
    assert cancelled.skipped_terminal == [finished["task_id"]]
    assert callbacks == [running["task_id"]]

    repeated = await batch_queue.cancel_generation_batch(project_name="demo", batch_id=batch_id)
    assert repeated.cancelled == []
    assert repeated.cancelling == [running["task_id"]]
    assert set(repeated.skipped_terminal) == {finished["task_id"], queued["task_id"]}
    assert callbacks == [running["task_id"]]

    await batch_queue.mark_task_cancelled(running["task_id"])
    terminal = await batch_queue.get_generation_batch(project_name="demo", batch_id=batch_id)
    assert terminal.done is True
    assert terminal.poll_after_seconds is None
    assert terminal.generation_result is not None
    assert terminal.generation_result.succeeded == ["finished"]
    assert terminal.generation_result.failed == ["running", "queued"]
    assert {item.task_state.value for item in terminal.generation_result.items} == {"succeeded", "cancelled"}
