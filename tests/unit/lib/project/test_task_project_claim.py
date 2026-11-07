"""Task project claims: after deletion invalidates a claim, task-context persistence rechecks for that project fail."""

import asyncio

import pytest

from lib.project.task_project_claim import (
    ProjectDeletedDuringTaskError,
    claim_task_project,
    ensure_task_project_claim,
    restore_task_project_claims,
    revoke_task_project_claims,
)


async def test_revoked_claim_blocks_only_the_claimed_project_inside_the_task_context():
    # Deletion can occur after claiming but before entering the execution context.
    revoke_task_project_claims(["t1"])
    with claim_task_project("t1", "demo") as claim:
        assert claim.revoked
        with pytest.raises(ProjectDeletedDuringTaskError):
            await asyncio.to_thread(ensure_task_project_claim, "demo")
        ensure_task_project_claim("other")
    ensure_task_project_claim("demo")


def test_restored_claim_lets_the_task_write_again():
    # If overwrite import fails to replace directories and preserves the original project, restore the invalidated claim.
    revoke_task_project_claims(["t2"])
    restore_task_project_claims(["t2"])
    with claim_task_project("t2", "demo") as claim:
        assert not claim.revoked
        ensure_task_project_claim("demo")
