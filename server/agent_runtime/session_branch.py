"""Session branching, the single message-rewrite entry point (``docs/adr/0058``).

Given an original session and its user-message anchor, copy transcript content before the anchor into a new
session_id, mark the original superseded and point to the new session. Callers receive only a new session_id
ready for initial input, without knowing how the prefix was assembled."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.prefix_fork import InvalidAnchorError, SessionStoreLike, copy_session_prefix
from server.agent_runtime.event_log import EventLogService
from server.agent_runtime.session_store import SessionMetaStore

logger = logging.getLogger(__name__)


class SessionBranchError(RuntimeError):
    """The branch session could not be established."""


class BranchAnchorError(SessionBranchError):
    """The anchor is not a branchable user message in this session.

    Separate from other failures because callers can fix this bad request: event logs cannot resolve the anchor,
    or its transcript uuid is absent or includes tool_result rejected by slicing."""


@dataclass(frozen=True, slots=True)
class BranchedSession:
    """Branch result: a prepared new session awaiting initial input.

    True ``resumable`` means persisted prefix, started with ``resume=session_id``. False means an empty prefix
    from rewriting the first message, so start a fresh session with preassigned ``session_id=``. Both paths use this identity."""

    session_id: str
    resumable: bool


class SessionBranchService:
    """Branch from a user message through one call."""

    def __init__(
        self,
        *,
        store: SessionStoreLike | None,
        meta_store: SessionMetaStore,
        event_log: EventLogService,
        resolve_project_cwd: Callable[[str], Path | None],
    ) -> None:
        self._store = store
        self._meta_store = meta_store
        self._event_log = event_log
        self._resolve_project_cwd = resolve_project_cwd

    async def branch(self, session_id: str, anchor_user_entry_uuid: str) -> BranchedSession:
        """Branch ``session_id`` at ``anchor_user_entry_uuid`` and return the new session.

        The anchor is the event-log user entry’s uuid, resolved there into transcript-domain identity. Reject failed
        resolution: anchors must be this session’s own user messages. This service does not distinguish the two uuid domains."""
        store = self._store
        if store is None:
            raise SessionBranchError(
                "session branching requires the DB transcript store (ARCREEL_SDK_SESSION_STORE=db)"
            )

        meta = await self._meta_store.get(session_id)
        if meta is None:
            raise SessionBranchError(f"session {session_id} not found")

        project_cwd = await asyncio.to_thread(self._resolve_project_cwd, meta.project_name)
        if project_cwd is None:
            raise SessionBranchError(f"project {meta.project_name} of session {session_id} is unavailable")

        anchor_uuid = await self._event_log.resolve_user_message_anchor(session_id, anchor_user_entry_uuid, project_cwd)
        if anchor_uuid is None:
            raise BranchAnchorError(f"anchor {anchor_user_entry_uuid} is not a user message of session {session_id}")

        # SDK session_id paths require UUID shape; non-UUID values silently appear
        # missing, so new IDs must be canonical uuid4 strings.
        new_session_id = str(uuid4())
        project_key = make_project_key(project_cwd)
        # From here through pointer persistence, transitions must fully succeed or revoke: each store
        # append commits separately, so failure leaves partial transcripts whose rows are visible
        # to store session enumeration without metadata rows.
        try:
            copied = await copy_session_prefix(
                store,
                project_key=project_key,
                session_id=session_id,
                anchor_uuid=anchor_uuid,
                new_session_id=new_session_id,
            )
            # Create new session rows before pointers so every pointer targets an existing session. Persist immutable fork lineage
            # with row creation, the inverse of superseded_by identifying
            # the original’s replacement; deletion takeover may rewrite the latter but never lineage.
            await self._meta_store.create(
                meta.project_name,
                new_session_id,
                fork_parent_session_id=session_id,
                fork_anchor_uuid=anchor_uuid,
            )
            if not await self._meta_store.mark_superseded(session_id, new_session_id):
                raise SessionBranchError(f"session {session_id} has already been superseded by another branch")
        except InvalidAnchorError as exc:
            # Validate anchors before writes; nothing needs revocation yet.
            raise BranchAnchorError(
                f"anchor {anchor_user_entry_uuid} is not a user message of session {session_id}: {exc}"
            ) from exc
        except BaseException:
            await self._discard(store, project_key, session_id, new_session_id)
            raise

        logger.info(
            "branch session: origin=%s new=%s entries=%d subagents=%d",
            session_id,
            new_session_id,
            copied.entries_copied,
            len(copied.subagent_subpaths),
        )
        return BranchedSession(session_id=new_session_id, resumable=copied.entries_copied > 0)

    async def discard(self, origin_session_id: str, new_session_id: str) -> None:
        """Revoke a published branch so the original session can be rewritten again.

        Orchestration uses this when branching succeeded but dispatching the rewritten message failed. That makes the
        entire rewrite fail; retaining superseded would prevent further rewrites while an empty new session replaced
        the original in session lists."""
        store = self._store
        if store is None:
            return
        meta = await self._meta_store.get(new_session_id)
        project_cwd = (
            await asyncio.to_thread(self._resolve_project_cwd, meta.project_name) if meta is not None else None
        )
        project_key = make_project_key(project_cwd) if project_cwd is not None else None
        await self._discard(store, project_key, origin_session_id, new_session_id)

    async def _discard(
        self, store: SessionStoreLike, project_key: str | None, origin_session_id: str, new_session_id: str
    ) -> None:
        """Revoke incomplete branches by clearing original pointers and new transcripts/metadata.

        Cleanup failures only log, preserving the triggering error. Remove pointers before data: persisted pointers,
        such as cancellation immediately after commit, must not reference disappearing sessions or hide originals from lists."""
        try:
            await self._meta_store.clear_superseded(origin_session_id, new_session_id)
        except Exception:
            logger.exception("failed to discard superseded pointer of session %s", origin_session_id)
        if project_key is None:
            # Without metadata, transcript project_key cannot be located; remaining rows become
            # orphaned data, traceable only through logs.
            logger.warning("cannot locate transcript of incomplete branch %s: project key unresolved", new_session_id)
        else:
            try:
                # delete without subpath removes subagent subpaths and summary too.
                await store.delete({"project_key": project_key, "session_id": new_session_id})
            except Exception:
                logger.exception("failed to discard transcript of incomplete branch %s", new_session_id)
        try:
            await self._meta_store.delete(new_session_id)
        except Exception:
            logger.exception("failed to discard metadata of incomplete branch %s", new_session_id)
