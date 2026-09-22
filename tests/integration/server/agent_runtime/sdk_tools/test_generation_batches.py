"""SDK adapters for durable generation batch tools."""

from __future__ import annotations

import json

from lib.generation.generation_batch import GenerationBatchRequestedItem, GenerationBatchRequestSnapshot
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import GenerationSelectionMode
from lib.project.project_manager import ProjectManager
from server.agent_runtime.sdk_tools.generation_batches import (
    cancel_generation_batch_tool,
    get_generation_batch_tool,
)
from server.media_tools.context import ToolContext


async def test_sdk_batch_tools_are_project_bound_and_use_the_durable_queue(db_factory, tmp_path) -> None:
    queue = GenerationQueue(session_factory=db_factory)
    batch_id = await queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.EXPLICIT,
            requested=[GenerationBatchRequestedItem(unit_id="E1S01")],
        ),
        blocked=[],
        source="embedded",
    )
    enqueued = await queue.enqueue_task(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        batch_id=batch_id,
        batch_unit_id="E1S01",
    )
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo")
    ctx = ToolContext("demo", projects.projects_root, projects, queue=queue)

    get_tool = get_generation_batch_tool(ctx)
    assert get_tool.name == "get_generation_batch"
    assert get_tool.description
    assert isinstance(get_tool.input_schema, dict)
    assert "project" not in get_tool.input_schema["properties"]
    read = await get_tool.handler({"batch_id": batch_id})
    payload = json.loads(read["content"][0]["text"])["generation_batch"]
    assert payload["members"] == [
        {
            "unit_id": "E1S01",
            "task_id": enqueued["task_id"],
            "task_type": "storyboard",
            "status": "queued",
            "deduped": False,
            "problem": None,
            "admission": {},
        }
    ]
    assert payload["done"] is False

    cancel_tool = cancel_generation_batch_tool(ctx)
    assert cancel_tool.name == "cancel_generation_batch"
    assert cancel_tool.description
    cancelled = await cancel_tool.handler({"batch_id": batch_id})
    cancellation = json.loads(cancelled["content"][0]["text"])["generation_batch_cancellation"]
    assert cancellation == {
        "cancelled": [enqueued["task_id"]],
        "skipped_running": [],
        "skipped_terminal": [],
    }


async def test_sdk_cancel_tool_leaves_running_member_to_finish(db_factory, tmp_path) -> None:
    queue = GenerationQueue(session_factory=db_factory)
    batch_id = await queue.create_generation_batch(
        project_name="demo",
        operation="generate_storyboards",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.EXPLICIT,
            requested=[GenerationBatchRequestedItem(unit_id="E1S01"), GenerationBatchRequestedItem(unit_id="E1S02")],
        ),
        blocked=[],
        source="embedded",
    )
    running = await queue.enqueue_task(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        batch_id=batch_id,
        batch_unit_id="E1S01",
    )
    claimed = await queue.claim_next_task(media_type="image")
    assert claimed is not None
    assert claimed["task_id"] == running["task_id"]
    queued = await queue.enqueue_task(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S02",
        batch_id=batch_id,
        batch_unit_id="E1S02",
    )
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo")
    ctx = ToolContext("demo", projects.projects_root, projects, queue=queue)

    cancelled = await cancel_generation_batch_tool(ctx).handler({"batch_id": batch_id})

    cancellation = json.loads(cancelled["content"][0]["text"])["generation_batch_cancellation"]
    assert cancellation == {
        "cancelled": [queued["task_id"]],
        "skipped_running": [running["task_id"]],
        "skipped_terminal": [],
    }
    running_row = await queue.get_task(running["task_id"])
    assert running_row is not None
    assert running_row["status"] == "running"
    assert await queue.mark_task_succeeded(running["task_id"], {"file_path": "storyboards/E1S01.png"}) == 1
    read = await get_generation_batch_tool(ctx).handler({"batch_id": batch_id})
    members = json.loads(read["content"][0]["text"])["generation_batch"]["members"]
    assert [(member["unit_id"], member["status"]) for member in members] == [
        ("E1S01", "succeeded"),
        ("E1S02", "cancelled"),
    ]
