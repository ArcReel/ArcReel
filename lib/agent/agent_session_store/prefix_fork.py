"""Prefix forking copies transcript entries before an anchor into a new session_id.

Resuming SDK sessions from assembled prefixes is unofficial; isolate knowledge/risks here
(``docs/adr/0058``). Depend only on SessionStore ``load`` / ``append`` / ``list_subkeys``,
never SQL; stores maintain seq allocation and summary folding themselves.

Preserve entry UUIDs: unique indexes and SDK materialization directories isolate by session_id,
so UUIDs never collide across sessions. parentUuid chains, independent sidechain graphs, and
cross-references need no remapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from claude_agent_sdk import SessionKey, SessionListSubkeysKey, SessionStoreEntry

SUBAGENT_SUBPATH_ROOT = "subagents/"
SUBAGENT_LEAF_PREFIX = "agent-"

# Conversation-entry types: absence of all these means an empty prefix. Transcripts often start with
# non-conversation metadata such as file-history-snapshot; nonzero rows do not imply conversation.
_CONVERSATION_ENTRY_TYPES = frozenset({"user", "assistant"})


class InvalidAnchorError(ValueError):
    """Anchor is not a user message on the original session’s main timeline."""


class SessionStoreLike(Protocol):
    """SessionStore subset consumed by this module."""

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None: ...

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None: ...

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]: ...

    async def delete(self, key: SessionKey) -> None: ...


@dataclass(frozen=True, slots=True)
class PrefixCopyResult:
    """Copy result; ``entries_copied == 0`` means an empty prefix, leaving no new-session transcript to resume."""

    entries_copied: int
    subagent_subpaths: tuple[str, ...]


async def copy_session_prefix(
    store: SessionStoreLike,
    *,
    project_key: str,
    session_id: str,
    anchor_uuid: str,
    new_session_id: str,
) -> PrefixCopyResult:
    """Copy entries before an anchor from ``session_id`` into ``new_session_id``.

    ``anchor_uuid`` identifies an original main-timeline user message, **excluded** from the slice:
    return to before that message was sent. User-message boundaries guarantee the preceding turn
    completed, preserving tool_use/tool_result pairing and subagent timeline integrity.

    Whole subagent timelines reached from prefixes accompany them: collect IDs from
    ``toolUseResult.agentId`` in prefix tool_results and copy corresponding subpaths completely.
    Match final path segment ``agent-<id>``, like the SDK; paths may be ``subagents/agent-<id>``
    or nested under ``subagents/workflows/<runId>/``.
    """
    main_entries = await store.load({"project_key": project_key, "session_id": session_id}) or []
    prefix = _prefix_before_anchor(main_entries, anchor_uuid)

    if not any(_entry_type(entry) in _CONVERSATION_ENTRY_TYPES for entry in prefix):
        return PrefixCopyResult(entries_copied=0, subagent_subpaths=())

    origin = {"sessionId": session_id, "messageUuid": anchor_uuid}
    rebound = [_rebind(entry, new_session_id=new_session_id, origin=origin) for entry in prefix]
    # Retimestamp the final entry, matching SDK forks, for resume leaf-node selection.
    rebound[-1] = {**rebound[-1], "timestamp": _now_iso()}

    await store.append({"project_key": project_key, "session_id": new_session_id}, rebound)
    copied = len(rebound)

    carried: list[str] = []
    wanted = {f"{SUBAGENT_LEAF_PREFIX}{agent_id}" for agent_id in _prefix_agent_ids(prefix)}
    if wanted:
        for subpath in await store.list_subkeys({"project_key": project_key, "session_id": session_id}):
            if not _matches_subagent(subpath, wanted):
                continue
            sub_entries = await store.load({"project_key": project_key, "session_id": session_id, "subpath": subpath})
            if not sub_entries:
                continue
            await store.append(
                {"project_key": project_key, "session_id": new_session_id, "subpath": subpath},
                [_rebind(entry, new_session_id=new_session_id, origin=origin) for entry in sub_entries],
            )
            carried.append(subpath)
            copied += len(sub_entries)

    return PrefixCopyResult(entries_copied=copied, subagent_subpaths=tuple(carried))


def _prefix_before_anchor(entries: list[SessionStoreEntry], anchor_uuid: str) -> list[SessionStoreEntry]:
    for index, entry in enumerate(entries):
        if entry.get("uuid") != anchor_uuid:
            continue
        if _entry_type(entry) != "user":
            raise InvalidAnchorError(f"anchor {anchor_uuid} is a {_entry_type(entry)!r} entry, not a user message")
        if _carries_tool_result(entry):
            # Transcripts also store tool results as type:"user" entries; those mixing text cannot be distinguished
            # from actual user messages by entry type alone. Anchoring them would leave their paired tool_use
            # dangling at the prefix end, so reject them as invalid anchors.
            raise InvalidAnchorError(f"anchor {anchor_uuid} carries tool results, not a plain user message")
        return entries[:index]
    raise InvalidAnchorError(f"anchor {anchor_uuid} is not on the main timeline of session")


def _carries_tool_result(entry: SessionStoreEntry) -> bool:
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def _matches_subagent(subpath: str, wanted_leaves: set[str]) -> bool:
    return subpath.startswith(SUBAGENT_SUBPATH_ROOT) and subpath.rsplit("/", 1)[-1] in wanted_leaves


def _prefix_agent_ids(prefix: list[SessionStoreEntry]) -> set[str]:
    agent_ids: set[str] = set()
    for entry in prefix:
        tool_use_result = entry.get("toolUseResult")
        if not isinstance(tool_use_result, dict):
            continue
        agent_id = tool_use_result.get("agentId")
        if isinstance(agent_id, str) and agent_id:
            agent_ids.add(agent_id)
    return agent_ids


def _rebind(entry: SessionStoreEntry, *, new_session_id: str, origin: dict[str, str]) -> SessionStoreEntry:
    """Rewrite entry ownership.

    Rewrite ``sessionId`` only if already present; add ``forkedFrom`` only to transcript rows.
    SDK writes subagent ``agent_metadata`` entries verbatim into .meta.json sidecars, so additional
    fields would otherwise leak into those files.
    """
    rebound = entry.copy()
    if "sessionId" in rebound:
        rebound["sessionId"] = new_session_id
    if entry.get("uuid"):
        rebound["forkedFrom"] = origin  # pyright: ignore[reportGeneralTypeIssues]  # SessionStoreEntry declares only type/uuid/timestamp; preserve other transcript fields per SDK protocol, which TypedDict cannot express as extension keys.
    return rebound


# Historical payload type values are not guaranteed strings; read them as object.
def _entry_type(entry: Mapping[str, object]) -> str:
    entry_type = entry.get("type")
    return entry_type if isinstance(entry_type, str) else ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
