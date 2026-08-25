"""SDK adapters for durable generation batch tools."""

from __future__ import annotations

import json

from lib.generation_batch import GenerationBatchRequestedItem, GenerationBatchRequestSnapshot
from lib.generation_queue import GenerationQueue
from lib.generation_result import GenerationSelectionMode
from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.generation_batches import (
    cancel_generation_batch_tool,
    get_generation_batch_tool,
)


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
    ctx = ToolContext("demo", projects.projects_root, projects, queue=queue)

    get_tool = get_generation_batch_tool(ctx)
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

    cancelled = await cancel_generation_batch_tool(ctx).handler({"batch_id": batch_id})
    cancellation = json.loads(cancelled["content"][0]["text"])["generation_batch_cancellation"]
    assert cancellation == {
        "cancelled": [enqueued["task_id"]],
        "cancelling": [],
        "skipped_terminal": [],
    }
