"""Tests for Tool Approval Flow (Task 4).

Validates:
1. PendingApproval dataclass CRUD on ManagedSession
2. Auto-approve policy (Read/Glob/Grep skip approval)
3. Approval → allow / deny decision lifecycle
4. Snapshot includes pending_approvals
5. Cancel propagation on session cleanup
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.agent_runtime.session_manager import (
    ManagedSession,
    PendingApproval,
    SessionManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def managed(event_loop):
    """ManagedSession with a mock actor."""
    actor = MagicMock()
    actor.enqueue = AsyncMock()
    managed_session = ManagedSession(
        session_id="test-session-1",
        actor=actor,
        status="running",
    )
    return managed_session


# ---------------------------------------------------------------------------
# 1. PendingApproval CRUD
# ---------------------------------------------------------------------------
class TestPendingApprovalCrud:
    def test_add_pending_approval(self, managed: ManagedSession, event_loop):
        """add_pending_approval creates entry in dict and returns PendingApproval."""

        async def _run():
            payload = {
                "type": "tool_approval_request",
                "tool_name": "Bash",
                "input": {"command": "ls"},
            }
            pending = managed.add_pending_approval(payload)
            assert isinstance(pending, PendingApproval)
            assert pending.request_id.startswith("ta_")
            assert pending.request_id in managed.pending_approvals
            assert payload["request_id"] == pending.request_id

        event_loop.run_until_complete(_run())

    def test_resolve_pending_approval_allow(self, managed: ManagedSession, event_loop):
        """resolve_pending_approval with allow sets future result."""

        async def _run():
            payload = {"type": "tool_approval_request", "tool_name": "Write", "input": {}}
            pending = managed.add_pending_approval(payload)
            rid = pending.request_id

            # Resolve with allow
            ok = managed.resolve_pending_approval(rid, "allow", {"file_path": "/test"})
            assert ok is True
            assert rid not in managed.pending_approvals

            decision, data = await pending.decision_future
            assert decision == "allow"
            assert data == {"file_path": "/test"}

        event_loop.run_until_complete(_run())

    def test_resolve_pending_approval_deny(self, managed: ManagedSession, event_loop):
        """resolve_pending_approval with deny sets future result."""

        async def _run():
            payload = {"type": "tool_approval_request", "tool_name": "Bash", "input": {}}
            pending = managed.add_pending_approval(payload)
            rid = pending.request_id

            ok = managed.resolve_pending_approval(rid, "deny", "too risky")
            assert ok is True

            decision, data = await pending.decision_future
            assert decision == "deny"
            assert data == "too risky"

        event_loop.run_until_complete(_run())

    def test_resolve_unknown_id(self, managed: ManagedSession):
        """resolve_pending_approval returns False for unknown IDs."""
        ok = managed.resolve_pending_approval("nonexistent", "allow", {})
        assert ok is False

    def test_cancel_pending_approvals(self, managed: ManagedSession, event_loop):
        """cancel_pending_approvals sets exception on all futures and clears dict."""

        async def _run():
            p1 = managed.add_pending_approval({"tool_name": "Bash", "input": {}})
            p2 = managed.add_pending_approval({"tool_name": "Write", "input": {}})
            assert len(managed.pending_approvals) == 2

            managed.cancel_pending_approvals("test cancel")
            assert len(managed.pending_approvals) == 0

            with pytest.raises(RuntimeError, match="test cancel"):
                await p1.decision_future
            with pytest.raises(RuntimeError, match="test cancel"):
                await p2.decision_future

        event_loop.run_until_complete(_run())

    def test_get_pending_approval_payloads(self, managed: ManagedSession, event_loop):
        """get_pending_approval_payloads returns unresolved payloads."""

        async def _run():
            managed.add_pending_approval({"tool_name": "Bash", "input": {"cmd": "ls"}})
            managed.add_pending_approval({"tool_name": "Edit", "input": {"file": "x.py"}})

            payloads = managed.get_pending_approval_payloads()
            assert len(payloads) == 2
            tool_names = {p["tool_name"] for p in payloads}
            assert tool_names == {"Bash", "Edit"}

        event_loop.run_until_complete(_run())


# ---------------------------------------------------------------------------
# 2. Auto-approve policy
# ---------------------------------------------------------------------------
class TestAutoApprovePolicy:
    def test_auto_approve_tools_lowercase(self):
        """AUTO_APPROVE_TOOLS should be all lowercase."""
        for tool in SessionManager.AUTO_APPROVE_TOOLS:
            assert tool == tool.lower(), f"{tool} should be lowercase"

    def test_read_is_auto_approved(self):
        assert "read" in SessionManager.AUTO_APPROVE_TOOLS

    def test_glob_is_auto_approved(self):
        assert "glob" in SessionManager.AUTO_APPROVE_TOOLS

    def test_grep_is_auto_approved(self):
        assert "grep" in SessionManager.AUTO_APPROVE_TOOLS

    def test_bash_is_not_auto_approved(self):
        assert "bash" not in SessionManager.AUTO_APPROVE_TOOLS

    def test_write_is_not_auto_approved(self):
        assert "write" not in SessionManager.AUTO_APPROVE_TOOLS

    def test_edit_is_not_auto_approved(self):
        assert "edit" not in SessionManager.AUTO_APPROVE_TOOLS

    def test_multiedit_is_not_auto_approved(self):
        assert "multiedit" not in SessionManager.AUTO_APPROVE_TOOLS


# ---------------------------------------------------------------------------
# 3. Snapshot includes pending_approvals
# ---------------------------------------------------------------------------
class TestSnapshotPendingApprovals:
    def test_build_snapshot_includes_approvals(self):
        from server.agent_runtime.stream_projector import AssistantStreamProjector

        projector = AssistantStreamProjector()
        snapshot = projector.build_snapshot(
            session_id="s1",
            status="running",
            pending_questions=[],
            pending_approvals=[{"request_id": "ta_123", "tool_name": "Bash"}],
        )
        assert "pending_approvals" in snapshot
        assert len(snapshot["pending_approvals"]) == 1
        assert snapshot["pending_approvals"][0]["request_id"] == "ta_123"

    def test_build_snapshot_empty_approvals(self):
        from server.agent_runtime.stream_projector import AssistantStreamProjector

        projector = AssistantStreamProjector()
        snapshot = projector.build_snapshot(
            session_id="s1",
            status="idle",
        )
        assert snapshot["pending_approvals"] == []


# ---------------------------------------------------------------------------
# 4. ApprovalDecisionRequest model validation
# ---------------------------------------------------------------------------
class TestApprovalDecisionRequest:
    def test_allow_decision(self):
        from server.routers.assistant import ApprovalDecisionRequest

        req = ApprovalDecisionRequest(decision="allow", updated_input={"cmd": "ls"})
        assert req.decision == "allow"
        assert req.updated_input == {"cmd": "ls"}
        assert req.message is None

    def test_deny_decision(self):
        from server.routers.assistant import ApprovalDecisionRequest

        req = ApprovalDecisionRequest(decision="deny", message="too risky")
        assert req.decision == "deny"
        assert req.updated_input is None
        assert req.message == "too risky"

    def test_invalid_decision_rejected(self):
        from server.routers.assistant import ApprovalDecisionRequest

        with pytest.raises(Exception):
            ApprovalDecisionRequest(decision="maybe")
