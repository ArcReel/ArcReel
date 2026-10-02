"""任务对项目的认领：项目删除使认领作废后，任务上下文里对该项目的落盘复核失败。"""

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
    # 删除可能发生在任务已被认领、但还没进入执行上下文的时候。
    revoke_task_project_claims(["t1"])
    with claim_task_project("t1", "demo") as claim:
        assert claim.revoked
        with pytest.raises(ProjectDeletedDuringTaskError):
            await asyncio.to_thread(ensure_task_project_claim, "demo")
        ensure_task_project_claim("other")
    ensure_task_project_claim("demo")


def test_restored_claim_lets_the_task_write_again():
    # 覆盖导入替换目录失败、项目原样保留时，作废的认领随之恢复。
    revoke_task_project_claims(["t2"])
    restore_task_project_claims(["t2"])
    with claim_task_project("t2", "demo") as claim:
        assert not claim.revoked
        ensure_task_project_claim("demo")
