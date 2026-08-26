"""Durable database behavior for generation batches."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifact_manifest import ArtifactComparison, ArtifactKey, ArtifactStatus
from lib.db.models.task import BatchTask, GenerationBatch
from lib.db.models.user import User
from lib.generation_batch import (
    GenerationBatchBlockedItem,
    GenerationBatchRequestedItem,
    GenerationBatchRequestSnapshot,
)
from lib.generation_queue import ActiveTaskRequestConflict, GenerationBatchNotFound, GenerationQueue
from lib.generation_result import (
    GenerationAction,
    GenerationItemResult,
    GenerationItemState,
    GenerationProblem,
    GenerationSelectionMode,
    GenerationSkippedItem,
)


@pytest.fixture
async def batch_queue(session_factory):
    return GenerationQueue(session_factory=session_factory)


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


async def test_fresh_batch_cleanup_waits_for_a_committing_membership(concurrent_session_factory) -> None:
    queue = GenerationQueue(session_factory=concurrent_session_factory)
    historical_batch = await queue.create_generation_batch(
        project_name="demo",
        operation="historical",
        requested=_snapshot("E1S01"),
        blocked=[],
        source="mcp",
    )
    historical_task = await _enqueue(queue, historical_batch, "E1S01")
    fresh_batch = await queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=_snapshot("E1S01"),
        blocked=[],
        source="mcp",
    )
    engine = concurrent_session_factory.kw["bind"]
    cleanup_started = asyncio.Event()

    def observe_cleanup_query(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = statement.lstrip().upper()
        if "FROM BATCHES" in normalized and (normalized.startswith("DELETE") or "FOR UPDATE" in normalized):
            cleanup_started.set()

    async with concurrent_session_factory() as membership_session:
        membership_session.add(
            BatchTask(
                batch_id=fresh_batch,
                task_id=historical_task["task_id"],
                unit_id="E1S01",
                deduped=True,
            )
        )
        await membership_session.flush()
        event.listen(engine.sync_engine, "before_cursor_execute", observe_cleanup_query)
        cleanup = asyncio.create_task(queue.delete_fresh_generation_batch(project_name="demo", batch_id=fresh_batch))
        try:
            async with asyncio.timeout(5):
                await cleanup_started.wait()
            await membership_session.commit()
            assert await cleanup == 0
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_cleanup_query)
            if not cleanup.done():
                cleanup.cancel()

    submitted = await queue.get_generation_batch(project_name="demo", batch_id=fresh_batch)
    assert [(member.task_id, member.deduped) for member in submitted.members] == [(historical_task["task_id"], True)]


async def test_cancelled_batch_create_cleans_a_commit_before_return(concurrent_session_factory) -> None:
    batch_committed = asyncio.Event()

    class CancelAfterBatchCommitSession(AsyncSession):
        async def commit(self) -> None:
            pauses_after_commit = any(isinstance(row, GenerationBatch) for row in self.new)
            await super().commit()
            if pauses_after_commit:
                batch_committed.set()
                await asyncio.Event().wait()

    queue = GenerationQueue(
        session_factory=async_sessionmaker(
            concurrent_session_factory.kw["bind"],
            class_=CancelAfterBatchCommitSession,
            expire_on_commit=False,
        )
    )
    creating = asyncio.create_task(
        queue.create_generation_batch(
            project_name="demo",
            operation="generate_storyboards",
            requested=_snapshot("E1S01"),
            blocked=[],
            source="mcp",
        )
    )
    await batch_committed.wait()
    creating.cancel()

    with pytest.raises(asyncio.CancelledError):
        await creating
    async with concurrent_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(GenerationBatch)) == 0


async def test_one_paid_task_can_project_to_every_requested_unit(
    batch_queue: GenerationQueue,
) -> None:
    batch_id = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_grid",
        requested=_snapshot("E1S01", "E1S02"),
        blocked=[],
        source="mcp",
    )
    task = await batch_queue.enqueue_task(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="grid-1",
        script_file="episode_01.json",
        batch_id=batch_id,
        batch_unit_ids=("E1S01", "E1S02"),
    )

    submitted = await batch_queue.get_generation_batch(project_name="demo", batch_id=batch_id)

    assert [(member.unit_id, member.task_id) for member in submitted.members] == [
        ("E1S01", task["task_id"]),
        ("E1S02", task["task_id"]),
    ]

    claimed = await batch_queue.claim_next_task("image")
    assert claimed is not None
    await batch_queue.mark_task_succeeded(
        task["task_id"],
        {
            "unit_results": {
                "E1S01": {"file_path": "storyboards/E1S01.png"},
                "E1S02": {
                    "problem": {
                        "code": "generation_post_processing_failed",
                        "detail": "cell was not written",
                        "action": "fix_input",
                        "params": {},
                    }
                },
            }
        },
    )
    terminal = await batch_queue.get_generation_batch(project_name="demo", batch_id=batch_id)
    assert terminal.generation_result is not None
    assert terminal.generation_result.succeeded == ["E1S01"]
    assert terminal.generation_result.failed == ["E1S02"]
    assert terminal.generation_result.items[1].task_state.value == "succeeded"


async def test_all_reusable_submission_still_creates_a_terminal_batch(
    batch_queue: GenerationQueue,
) -> None:
    batch_id = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.MISSING_ONLY,
            requested=[],
            skipped=[GenerationSkippedItem(unit_id="E1S01")],
        ),
        blocked=[],
        source="mcp",
    )

    submitted = await batch_queue.get_generation_batch(project_name="demo", batch_id=batch_id)

    assert submitted.done is True
    assert [item.unit_id for item in submitted.skipped] == ["E1S01"]
    assert submitted.generation_result is not None
    assert [item.unit_id for item in submitted.generation_result.skipped] == ["E1S01"]


async def test_terminal_batch_reobserves_artifact_currency_instead_of_echoing_admission(
    batch_queue: GenerationQueue,
) -> None:
    key = ArtifactKey.episode_storyboard(1, "E1S01")
    batch_id = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.MISSING_ONLY,
            requested=[
                GenerationBatchRequestedItem(
                    unit_id="E1S01",
                    artifact_key=key.encode(),
                    artifact_path="storyboards/old.png",
                    artifact_status=ArtifactStatus.MISSING,
                )
            ],
        ),
        blocked=[],
        source="mcp",
    )
    task = await _enqueue(batch_queue, batch_id, "E1S01")
    assert await batch_queue.claim_next_task("image") is not None
    await batch_queue.mark_task_succeeded(task["task_id"], {"file_path": "storyboards/current.png"})

    class _Resolver:
        def compare(self, observed_key: ArtifactKey, *, artifact_path: str | None = None):
            assert observed_key == key
            assert artifact_path == "storyboards/current.png"
            return ArtifactComparison(status=ArtifactStatus.STALE, artifact_path="storyboards/current.png")

    without_resolver = await batch_queue.get_generation_batch(project_name="demo", batch_id=batch_id)
    with_resolver = await batch_queue.get_generation_batch(
        project_name="demo",
        batch_id=batch_id,
        resolver=_Resolver(),  # type: ignore[arg-type]
    )

    assert without_resolver.generation_result is not None
    assert without_resolver.generation_result.items[0].artifact_status is None
    assert with_resolver.generation_result is not None
    assert with_resolver.generation_result.items[0].artifact_status is ArtifactStatus.STALE


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


async def test_batches_and_active_dedup_are_user_scoped(batch_queue: GenerationQueue, session_factory) -> None:
    async with session_factory() as session:
        session.add(User(id="other-user", username="other-user"))
        await session.commit()

    scoped_fresh_batch = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=_snapshot("fresh"),
        blocked=[],
        source="embedded",
        user_id="other-user",
    )
    assert (
        await batch_queue.delete_fresh_generation_batch(
            project_name="other",
            batch_id=scoped_fresh_batch,
            user_id="other-user",
        )
        == 0
    )
    assert await batch_queue.delete_fresh_generation_batch(project_name="demo", batch_id=scoped_fresh_batch) == 0
    assert (
        await batch_queue.delete_fresh_generation_batch(
            project_name="demo",
            batch_id=scoped_fresh_batch,
            user_id="other-user",
        )
        == 1
    )

    first_batch = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_episode_script",
        requested=_snapshot("episode-1"),
        blocked=[],
        source="embedded",
    )
    first = await batch_queue.enqueue_task(
        project_name="demo",
        task_type="text_episode_script",
        media_type="text",
        resource_id="episode-1",
        payload={"episode": 1, "instructions": None},
        batch_id=first_batch,
        batch_unit_id="episode-1",
    )
    other_batch = await batch_queue.create_generation_batch(
        project_name="demo",
        operation="generate_episode_script",
        requested=_snapshot("episode-1"),
        blocked=[],
        source="embedded",
        user_id="other-user",
    )
    other = await batch_queue.enqueue_task(
        project_name="demo",
        task_type="text_episode_script",
        media_type="text",
        resource_id="episode-1",
        payload={"episode": 1, "instructions": None},
        batch_id=other_batch,
        batch_unit_id="episode-1",
        user_id="other-user",
    )

    assert other["task_id"] != first["task_id"]
    assert await batch_queue.get_generation_batch(project_name="demo", batch_id=other_batch, user_id="other-user")
    with pytest.raises(GenerationBatchNotFound):
        await batch_queue.get_generation_batch(project_name="demo", batch_id=first_batch, user_id="other-user")
    with pytest.raises(GenerationBatchNotFound):
        await batch_queue.cancel_generation_batch(project_name="demo", batch_id=other_batch)


async def test_text_dedup_rejects_different_request_facts(batch_queue: GenerationQueue) -> None:
    first = await batch_queue.enqueue_task(
        project_name="demo",
        task_type="text_episode_script",
        media_type="text",
        resource_id="episode-1",
        payload={"episode": 1, "instructions": "first"},
    )
    repeated = await batch_queue.enqueue_task(
        project_name="demo",
        task_type="text_episode_script",
        media_type="text",
        resource_id="episode-1",
        payload={"episode": 1, "instructions": "first", "projects_root": "/same-projects"},
    )
    assert repeated["task_id"] == first["task_id"]
    assert repeated["deduped"] is True

    with pytest.raises(ActiveTaskRequestConflict):
        await batch_queue.enqueue_task(
            project_name="demo",
            task_type="text_episode_script",
            media_type="text",
            resource_id="episode-1",
            payload={"episode": 1, "instructions": "different"},
        )


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
