"""Generation-batch query and cancellation declarations."""

from __future__ import annotations

from server.agent_toolset.declaration import Exempt, ToolDeclaration
from server.tool_runtime import GenerationBatchToolRequest, cancel_generation_batch, get_generation_batch

GET_GENERATION_BATCH = ToolDeclaration(
    name="get_generation_batch",
    description=(
        "Query a generation batch: task state for each member, counts, suggested poll_after_seconds, and terminal flag done. Once terminal, includes generation_result (success / failure / blocked outcomes by ID). Until done=true, call again at poll_after_seconds intervals. Returns generation_batch_not_found when absent or not owned by this project. Read-only, no side effects."
    ),
    request_model=GenerationBatchToolRequest,
    migration=Exempt(
        "Read-only batch state from the task queue, not project artifacts. Batches submitted before migration failure must remain queryable."
    ),
    domain_key="generation_batch",
    handler=get_generation_batch,
)

CANCEL_GENERATION_BATCH = ToolDeclaration(
    name="cancel_generation_batch",
    description=(
        "Cancel members of a generation batch that are still queued. Returns task IDs cancelled, skipped_running because execution began, and skipped_terminal because already terminal. Running members cannot be cancelled and finish normally; repeated calls are safe. Returns generation_batch_not_found when absent or not owned by this project."
    ),
    request_model=GenerationBatchToolRequest,
    migration=Exempt(
        "Cancels only queued tasks, writing no project artifacts. Must permit cancelling previously submitted batches after migration failure to limit further cost."
    ),
    domain_key="generation_batch_cancellation",
    handler=cancel_generation_batch,
)

GENERATION_BATCH_TOOLS = (GET_GENERATION_BATCH, CANCEL_GENERATION_BATCH)

__all__ = [
    "CANCEL_GENERATION_BATCH",
    "GENERATION_BATCH_TOOLS",
    "GET_GENERATION_BATCH",
]
