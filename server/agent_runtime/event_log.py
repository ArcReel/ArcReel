"""Session event log: sole read source for the UI timeline.

Entries take their final form at the write boundary: normalize the SDK message stream once on logging
(tool_result becomes a standalone entry, subagent messages carry parent_tool_use_id, stream_event is
excluded). Renderers and readers perform no semantic sniffing or deduplication. The log is a materialized
view of the SDK transcript: replay and reconstruct legacy transcript-only sessions on first access (lazy generation)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID, utc_now
from lib.db.models.session_event import AgentSessionEventLogEntry
from lib.db.models.session_message_link import AgentSessionUserMessageLink
from server.agent_runtime.failure_observation import build_turn_failure_observation
from server.agent_runtime.keyed_locks import KeyedLocks
from server.agent_runtime.message_serialization import utc_now_iso
from server.agent_runtime.result_status import result_indicates_error
from server.agent_runtime.turn_schema import normalize_content, stringify_content

logger = logging.getLogger(__name__)

ENTRY_TYPE_USER = "user"
ENTRY_TYPE_ASSISTANT = "assistant"
ENTRY_TYPE_TOOL_RESULT = "tool_result"
ENTRY_TYPE_SYSTEM = "system"

ENTRY_SUBTYPE_INTERRUPT = "interrupt"
ENTRY_SUBTYPE_TASK_NOTIFICATION = "task_notification"
ENTRY_SUBTYPE_QUESTION_ANSWER = "question_answer"
ENTRY_SUBTYPE_AGENT_TURN_FAILURE = "agent_turn_failure"
ENTRY_SUBTYPE_COMPACT_SUMMARY = "compact_summary"
ENTRY_SUBTYPE_SUBAGENT_OUTCOME = "subagent_outcome"

SYSTEM_SUBTYPE_SKILL_INVOCATION = "skill_invocation"

_TASK_SUBTYPES = {"task_started", "task_progress", "task_notification"}

# Prefix recognizing skill-injected user messages. Semantic sniffing is permitted only at this write boundary:
# the finalized log stores only skill name and arguments; readers and renderers no longer encounter injection text.
_SKILL_INJECTION_PREFIXES = ("Base directory for this skill:", "Skill content:")

# Case variants of subagent ownership keys in SDK messages and transcript payloads;
# normalize them to parent_tool_use_id at the write boundary.
_PARENT_KEY_VARIANTS = ("parent_tool_use_id", "parentToolUseID", "parentToolUseId")

# CLI-injected interruption echo prefixes. Exact wording is an internal CLI detail (not a stable API);
# recognize prefixes once at the write boundary, with no sniffing by readers afterward.
_INTERRUPT_ECHO_PREFIX = "[Request interrupted"

# CLI-injected continuation summary after compaction. Transcript payloads carry isCompactSummary
# (backfilled as is_compact_summary by the adapter); parsed live messages lack that marker, so identify
# them by fixed summary-opening wording. This is an internal CLI detail, recognized only once at the write boundary.
_COMPACT_SUMMARY_PREFIX = "This session is being continued from a previous conversation"

# Truncate inferred subagent descriptions to this length after joining lines.
_SUBAGENT_DESCRIPTION_MAX_CHARS = 200

# Background-task notification XML injected by the SDK as user messages.
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>\s*.*?</task-notification>", re.DOTALL)

_ASK_USER_QUESTION_TOOL = "askuserquestion"

# Same seq-contention retry parameters as DbSessionStore.append.
_MAX_APPEND_RETRY = 16
_APPEND_BACKOFF_CAP_S = 0.05

# PostgreSQL unique-constraint SQLSTATE (exposed by asyncpg after SQLAlchemy adaptation on
# exc.orig.sqlstate / pgcode）。
_PG_UNIQUE_VIOLATION_SQLSTATE = "23505"

# SQLite extended error names for unique-constraint violations: PRIMARYKEY for composite primary keys,
# UNIQUE for ordinary unique indexes; both mean unique-constraint violation for this table.
_SQLITE_UNIQUE_ERRORNAMES = {"SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"}

# Mark CLI-replayed user-message copies (entries already logged on acceptance) for the write boundary to skip.
# Exists only on in-process message dicts, never in persistence.
REPLAYED_USER_ECHO_KEY = "_replayed_user_echo"

# Event-log user-entry uuid carried on replay copies: the write boundary records the server message id ↔ SDK
# transcript entry uuid mapping. Like the previous key, this is an in-process marker, excluded from entry payloads.
REPLAYED_USER_ECHO_ENTRY_UUID_KEY = "_replayed_user_echo_entry_uuid"


def _extract_parent(message: dict[str, Any]) -> str | None:
    """Normalize subagent ownership keys at the write boundary (recognize case variants only here)."""
    for key in _PARENT_KEY_VARIANTS:
        parent = message.get(key)
        if isinstance(parent, str) and parent.strip():
            return parent
    return None


def _copy_parent(message: dict[str, Any], entry: dict[str, Any]) -> None:
    parent = _extract_parent(message)
    if parent:
        entry["parent_tool_use_id"] = parent


def _is_skill_injection_text(text: str) -> bool:
    return text.startswith(_SKILL_INJECTION_PREFIXES)


def _parse_skill_name_from_injection(text: str) -> str | None:
    """Extract the skill name from the base-directory path in the injection's first line (fallback without a preceding Skill tool_use)."""
    first_line = text.split("\n", 1)[0].strip()
    prefix = "Base directory for this skill:"
    if not first_line.startswith(prefix):
        return None
    path = first_line[len(prefix) :].strip().replace("\\", "/").rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if segments and segments[-1].upper() == "SKILL.MD":
        segments = segments[:-1]
    return segments[-1] if segments else None


def build_interrupt_entry(
    *,
    uuid: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a typed interruption entry (a logged timeline event)."""
    return {
        "type": ENTRY_TYPE_SYSTEM,
        "subtype": ENTRY_SUBTYPE_INTERRUPT,
        "uuid": uuid or f"interrupt-{uuid4().hex}",
        "timestamp": timestamp or utc_now_iso(),
    }


def is_interrupt_entry(entry: dict[str, Any]) -> bool:
    return entry.get("type") == ENTRY_TYPE_SYSTEM and entry.get("subtype") == ENTRY_SUBTYPE_INTERRUPT


def _blocks_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")


def _is_interrupt_echo(blocks: list[dict[str, Any]]) -> bool:
    """Match only when the entire content is one interruption-echo text block, not the same wording inside the body."""
    if len(blocks) != 1 or blocks[0].get("type") != "text":
        return False
    return str(blocks[0].get("text") or "").strip().startswith(_INTERRUPT_ECHO_PREFIX)


def _is_compact_summary(message: dict[str, Any], blocks: list[dict[str, Any]]) -> bool:
    if message.get("is_compact_summary") is True:
        return True
    return _blocks_text(blocks).lstrip().startswith(_COMPACT_SUMMARY_PREFIX)


def _extract_task_notifications(blocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract all task-notification XML from message text (multiple notifications may be batched in one tick)."""
    text = _blocks_text(blocks)

    def _tag(xml: str, name: str) -> str:
        m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
        return m.group(1).strip() if m else ""

    return [
        {
            "task_id": _tag(match.group(0), "task-id"),
            "tool_use_id": _tag(match.group(0), "tool-use-id"),
            "status": _tag(match.group(0), "status"),
            "summary": _tag(match.group(0), "summary"),
        }
        for match in _TASK_NOTIFICATION_RE.finditer(text)
    ]


def _extract_structured_answers(message: dict[str, Any]) -> dict[str, str] | None:
    """Extract the answer mapping from message-level tool_use_result (CLI structured tool results)."""
    tool_use_result = message.get("tool_use_result")
    if not isinstance(tool_use_result, dict):
        return None
    answers = tool_use_result.get("answers")
    if not isinstance(answers, dict) or not answers:
        return None
    return {str(k): str(v) for k, v in answers.items()}


def build_user_entry(
    content_blocks: list[dict[str, Any]],
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build the authoritative user-message entry: allocate identity on send and log when the Agent accepts the message."""
    return {
        "type": ENTRY_TYPE_USER,
        "content": normalize_content(content_blocks),
        "uuid": f"user-{uuid4().hex}",
        "timestamp": timestamp or utc_now_iso(),
    }


def _build_turn_failure_entry(
    *,
    assistant_message: dict[str, Any] | None,
    result_message: dict[str, Any] | None,
    project_name: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    source = assistant_message if assistant_message is not None else (result_message or {})
    entry: dict[str, Any] = {
        "type": ENTRY_TYPE_SYSTEM,
        "subtype": ENTRY_SUBTYPE_AGENT_TURN_FAILURE,
        "failure": build_turn_failure_observation(
            assistant_message=assistant_message,
            result_message=result_message,
            project_name=project_name,
            session_id=session_id,
        ),
        "uuid": source.get("uuid") or f"failure-{uuid4().hex}",
        "timestamp": source.get("timestamp") or utc_now_iso(),
    }
    if assistant_message is not None:
        _copy_parent(assistant_message, entry)
    elif result_message is not None:
        _copy_parent(result_message, entry)
    return entry


def build_failure_entry(observation: dict[str, Any]) -> dict[str, Any]:
    """Finalize an already-redacted failure observation as a system event."""
    timestamp = observation.get("timestamp")
    return {
        "type": ENTRY_TYPE_SYSTEM,
        "subtype": ENTRY_SUBTYPE_AGENT_TURN_FAILURE,
        "failure": observation,
        "uuid": f"failure-{uuid4().hex}",
        "timestamp": timestamp if isinstance(timestamp, str) and timestamp else utc_now_iso(),
    }


class SdkMessageNormalizer:
    """Write-boundary normalizer: convert SDK message dicts into zero or more log entries.

    - assistant(error) → buffer until result, then finalize as one agent_turn_failure system entry;
      ordinary assistant → one assistant entry (with message_id for exact draft replacement).
      Register AskUserQuestion tool_use_id in instance state to finalize answers; register Skill
      tool_use in instance state to finalize subsequent injection messages as skill_invocation system entries.
    - user → finalize interruption echoes as system/interrupt entries; task-notification XML
      (possibly multiple in one tick) as system/task_notification entries; AskUserQuestion tool_result
      as user/question_answer entries (structured answers from message-level tool_use_result);
      skill injection text as skill_invocation system entries (skill name and arguments only, not the full
      injection text); remaining tool_result blocks as standalone entries referencing tool_use_id;
      remaining content as generic user entries. Marked SDK replay copies are not logged.
    - system(task_*) → typed system entries.
    - result(error) → merge with buffered assistant(error) into agent_turn_failure;
      stream_event / others → not logged.

    Instances maintain cross-message correlations: Skill tool_use → subsequent injected user message;
    AskUserQuestion tool_use → subsequent answer tool_result. Slot Skill state by ownership context
    (parent_tool_use_id, None for the main line): main-line and subagent messages interleave in live
    streams and correlate independently. Live pipelines and lazy-generation replay share this class,
    ensuring both paths produce the same typed entries."""

    def __init__(self, *, capture_failures: bool = True) -> None:
        self._capture_failures = capture_failures
        # Context → FIFO queue of Skill tool_use metadata not yet consumed by injection messages.
        # A context may launch multiple Skill calls concurrently (multiple tool_use blocks in one assistant
        # message); consume in invocation order to prevent later calls overwriting earlier ones.
        self._pending_skills: dict[str | None, list[dict[str, Any]]] = {}
        # Registered AskUserQuestion tool_use_id values still awaiting answers.
        self.question_tool_use_ids: set[str] = set()
        # Main-line and subagent messages interleave; wait for each result by parent context
        # rather than combining failures from two execution contexts into a false observation.
        self._pending_turn_failures: dict[str | None, dict[str, Any]] = {}

    def normalize(
        self,
        message: Any,
        *,
        project_name: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(message, dict):
            return []
        msg_type = message.get("type")

        if msg_type == "assistant":
            if self._capture_failures and message.get("error") is not None:
                parent = _extract_parent(message)
                self._pending_turn_failures[parent] = dict(message)
                return []
            content = normalize_content(message.get("content", []))
            for block in content:
                if (
                    block.get("type") == "tool_use"
                    and str(block.get("name") or "").strip().lower() == _ASK_USER_QUESTION_TOOL
                    and block.get("id")
                ):
                    self.question_tool_use_ids.add(str(block["id"]))
            entry: dict[str, Any] = {
                "type": ENTRY_TYPE_ASSISTANT,
                "content": content,
                "message_id": message.get("message_id"),
                "uuid": message.get("uuid") or f"entry-{uuid4().hex}",
                "timestamp": message.get("timestamp") or utc_now_iso(),
            }
            _copy_parent(message, entry)
            self._track_skill_tool_use(message, entry)
            return [entry]

        if msg_type == "result":
            if not self._capture_failures:
                return []
            parent = _extract_parent(message)
            assistant = self._pending_turn_failures.pop(parent, None)
            if str(message.get("session_status") or "") == "interrupted":
                return []
            if assistant is not None or result_indicates_error(message):
                return [
                    _build_turn_failure_entry(
                        assistant_message=assistant,
                        result_message=message,
                        project_name=project_name,
                        session_id=session_id,
                    )
                ]
            return []

        if msg_type == "user":
            return self._normalize_user(message)

        if msg_type == "system" and message.get("subtype") in _TASK_SUBTYPES:
            sys_entry: dict[str, Any] = {
                "type": ENTRY_TYPE_SYSTEM,
                "subtype": message.get("subtype"),
                "task_id": message.get("task_id"),
                "description": message.get("description", ""),
                "summary": message.get("summary"),
                "task_status": message.get("status"),
                "usage": message.get("usage"),
                "tool_use_id": message.get("tool_use_id"),
                "uuid": message.get("uuid") or f"entry-{uuid4().hex}",
                "timestamp": message.get("timestamp") or utc_now_iso(),
            }
            _copy_parent(message, sys_entry)
            return [sys_entry]

        return []

    def _normalize_user(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if message.get(REPLAYED_USER_ECHO_KEY):
            return []
        blocks = normalize_content(message.get("content", ""))
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        timestamp = message.get("timestamp") or utc_now_iso()
        base_uuid = message.get("uuid")
        parent = _extract_parent(message)

        if not tool_results:
            if _is_compact_summary(message, blocks):
                summary_entry: dict[str, Any] = {
                    "type": ENTRY_TYPE_USER,
                    "subtype": ENTRY_SUBTYPE_COMPACT_SUMMARY,
                    "content": blocks,
                    "uuid": base_uuid or f"entry-{uuid4().hex}",
                    "timestamp": timestamp,
                }
                _copy_parent(message, summary_entry)
                return [summary_entry]
            if _is_interrupt_echo(blocks):
                return [
                    build_interrupt_entry(
                        uuid=base_uuid,
                        timestamp=message.get("timestamp"),
                    )
                ]
            task_infos = _extract_task_notifications(blocks)
            if task_infos:
                # Preserve the original uuid for a single notification (most cases); add numbered suffixes only
                # when batching multiple notifications, preventing same-uuid system entries from overwriting each other in frontend grouping/lookups.
                multiple = len(task_infos) > 1
                notification_entries = [
                    {
                        "type": ENTRY_TYPE_SYSTEM,
                        "subtype": ENTRY_SUBTYPE_TASK_NOTIFICATION,
                        "task_id": task_info["task_id"] or None,
                        "description": "",
                        "summary": task_info["summary"] or None,
                        "task_status": task_info["status"] or None,
                        "tool_use_id": task_info["tool_use_id"] or None,
                        "uuid": (
                            f"{base_uuid}-tn{i}" if multiple and base_uuid else (base_uuid or f"entry-{uuid4().hex}")
                        ),
                        "timestamp": timestamp,
                    }
                    for i, task_info in enumerate(task_infos)
                ]
                if parent:
                    for notification_entry in notification_entries:
                        notification_entry["parent_tool_use_id"] = parent
                return notification_entries

        # tool_use_result is message-level (CLI single toolUseResult, not partitioned by tool_use_id):
        # structured answers are attributable only when the message batches exactly one pending-answer tool_result.
        # With tool_result for several questions in one message, ownership cannot be determined;
        # prefer raw-text fallback over misassigning one answer set to multiple questions.
        question_result_count = sum(
            1
            for block in tool_results
            if block.get("tool_use_id") and str(block["tool_use_id"]) in self.question_tool_use_ids
        )

        entries: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []
        tool_result_index = 0
        skill_index = 0
        for block in blocks:
            if block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if tool_use_id and str(tool_use_id) in self.question_tool_use_ids:
                    qa_entry: dict[str, Any] = {
                        "type": ENTRY_TYPE_USER,
                        "subtype": ENTRY_SUBTYPE_QUESTION_ANSWER,
                        "tool_use_id": tool_use_id,
                        "content": stringify_content(block.get("content", "")),
                        "is_error": bool(block.get("is_error", False)),
                        "answers": _extract_structured_answers(message) if question_result_count == 1 else None,
                        "uuid": f"{base_uuid}-tr{tool_result_index}" if base_uuid else f"entry-{uuid4().hex}",
                        "timestamp": timestamp,
                    }
                    _copy_parent(message, qa_entry)
                    entries.append(qa_entry)
                    tool_result_index += 1
                    continue
                tr_entry: dict[str, Any] = {
                    "type": ENTRY_TYPE_TOOL_RESULT,
                    "tool_use_id": tool_use_id,
                    "content": block.get("content", ""),
                    "is_error": bool(block.get("is_error", False)),
                    "uuid": f"{base_uuid}-tr{tool_result_index}" if base_uuid else f"entry-{uuid4().hex}",
                    "timestamp": timestamp,
                }
                _copy_parent(message, tr_entry)
                entries.append(tr_entry)
                tool_result_index += 1
                continue
            text = block.get("text") if block.get("type") == "text" else None
            if isinstance(text, str) and _is_skill_injection_text(text.strip()):
                entries.append(self._build_skill_entry(text.strip(), timestamp, base_uuid, skill_index, parent))
                skill_index += 1
                continue
            others.append(block)
        if others:
            user_entry: dict[str, Any] = {
                "type": ENTRY_TYPE_USER,
                "content": others,
                "uuid": base_uuid or f"entry-{uuid4().hex}",
                "timestamp": timestamp,
            }
            _copy_parent(message, user_entry)
            entries.append(user_entry)
        return entries

    def _build_skill_entry(
        self,
        text: str,
        timestamp: str,
        base_uuid: Any,
        index: int,
        parent: str | None,
    ) -> dict[str, Any]:
        queue = self._pending_skills.get(parent)
        pending = queue.pop(0) if queue else {}
        if queue is not None and not queue:
            self._pending_skills.pop(parent, None)
        entry: dict[str, Any] = {
            "type": ENTRY_TYPE_SYSTEM,
            "subtype": SYSTEM_SUBTYPE_SKILL_INVOCATION,
            "skill_name": pending.get("name") or _parse_skill_name_from_injection(text),
            "skill_args": pending.get("args"),
            "tool_use_id": pending.get("tool_use_id"),
            "uuid": f"{base_uuid}-skill{index}" if base_uuid else f"entry-{uuid4().hex}",
            "timestamp": timestamp,
        }
        if parent:
            entry["parent_tool_use_id"] = parent
        return entry

    def _track_skill_tool_use(self, message: dict[str, Any], entry: dict[str, Any]) -> None:
        parent = _extract_parent(message)
        for block in entry.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Skill":
                continue
            raw_input = block.get("input")
            input_data: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
            name = input_data.get("skill") or input_data.get("name")
            args = input_data.get("args")
            self._pending_skills.setdefault(parent, []).append(
                {
                    "tool_use_id": block.get("id"),
                    "name": name if isinstance(name, str) and name else None,
                    "args": args if isinstance(args, str) and args else None,
                }
            )


def normalize_sdk_message_to_entries(message: Any) -> list[dict[str, Any]]:
    """Normalize a single message once (cross-message correlations for skill injection/AskUserQuestion
    require retaining a SdkMessageNormalizer instance)."""
    return SdkMessageNormalizer().normalize(message)


def _assistant_tool_use_ids(message: Any) -> list[str]:
    """List of tool_use block IDs in an assistant message (candidate anchors for subagent timelines)."""
    if not isinstance(message, dict) or message.get("type") != "assistant":
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    ids: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            block_id = block.get("id")
            if isinstance(block_id, str) and block_id:
                ids.append(block_id)
    return ids


def infer_subagent_outcome(
    tool_use_id: str,
    messages: list[dict[str, Any]],
    description: str | None = None,
) -> dict[str, Any] | None:
    """Infer description and terminal state from subagent timelines lacking main-line anchor tool_use.

    After compaction continuation, the main line starts with the continuation summary. Subagents started
    before it lack anchors, hence their invocation descriptions and tool_result. Prefer ``description``
    from original-payload invocation arguments (passed by caller); otherwise use the sub-timeline's first
    instruction. Join multiple lines before truncating. A final answer with no further tool calls means
    completed, with the answer body as conclusion; a final SDK error marker means failed; other cases
    (stopped during a tool call) mean stopped."""
    conversation = [m for m in messages if m.get("type") in (ENTRY_TYPE_USER, ENTRY_TYPE_ASSISTANT)]
    if not conversation:
        return None
    text = (description or "").strip()
    if not text:
        for message in conversation:
            if message.get("type") != ENTRY_TYPE_USER:
                continue
            text = _blocks_text(normalize_content(message.get("content", ""))).strip()
            if text:
                break
    description = " ".join(text.split())[:_SUBAGENT_DESCRIPTION_MAX_CHARS]

    last = conversation[-1]
    last_blocks = normalize_content(last.get("content", ""))
    summary: str | None = None
    if last.get("type") == ENTRY_TYPE_ASSISTANT and last.get("error") is not None:
        status = "failed"
    elif (
        last.get("type") == ENTRY_TYPE_ASSISTANT
        and not any(block.get("type") == "tool_use" for block in last_blocks)
        and _blocks_text(last_blocks).strip()
    ):
        status = "completed"
        summary = _blocks_text(last_blocks).strip()
    else:
        status = "stopped"
    return {
        "type": ENTRY_TYPE_SYSTEM,
        "subtype": ENTRY_SUBTYPE_SUBAGENT_OUTCOME,
        "tool_use_id": tool_use_id,
        "description": description,
        "task_status": status,
        "summary": summary,
        "uuid": f"subagent-outcome-{tool_use_id}",
        "timestamp": last.get("timestamp") or utc_now_iso(),
    }


def _first_driver_attr(exc: IntegrityError, *attr_names: str) -> Any:
    """Find the first non-None driver attribute along the ``__cause__``/``__context__`` chain of ``exc.orig``.

    SQLAlchemy's asyncpg adapter attaches the original driver exception as the translated exception's cause.
    Unique-constraint and client_key-conflict detection both inspect driver attributes along this chain.
    Prefer explicit ``__cause__`` (``raise ... from ...``); fall back to ``__context__`` when drivers or
    intermediate layers wrap implicitly (bare ``raise``). Track visited exceptions by ``id()`` to prevent
    infinite loops in defensive handling of cyclic exception chains (not produced by normal propagation,
    but pathological construction is possible)."""
    err: BaseException | None = exc.orig
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        for name in attr_names:
            value = getattr(err, name, None)
            if value is not None:
                return value
        err = err.__cause__ or err.__context__
    return None


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Detect unique-constraint violations, preferring error codes (PostgreSQL SQLSTATE / SQLite errorname)
    and using message substrings only as fallback.

    PostgreSQL localizes messages using server lc_messages, so non-English messages lack "duplicate key";
    substring matching would misclassify retryable races as real errors."""
    sqlstate = _first_driver_attr(exc, "sqlstate", "pgcode")
    if sqlstate is not None:
        return str(sqlstate) == _PG_UNIQUE_VIOLATION_SQLSTATE
    errorname = _first_driver_attr(exc, "sqlite_errorname")
    if errorname is not None:
        return errorname in _SQLITE_UNIQUE_ERRORNAMES
    msg = str(exc.orig) if exc.orig else str(exc)
    return "UNIQUE" in msg or "duplicate key" in msg


def _is_client_key_violation(exc: IntegrityError) -> bool:
    """Determine whether a conflict concerns the client_key unique index: prefer constraint names (unaffected
    by locale), falling back to error text if the driver exposes no name. Both backends include index/column names.

    When ``exc.orig`` is None, do not match against the entire ``str(exc)``: SQLAlchemy ``StatementError``
    text includes ``[SQL: INSERT INTO ...
    client_key ...]``. The INSERT column name itself contains "client_key", misclassifying every exception
    (including unrelated seq primary-key races) as client_key conflicts. Strip the portion starting at
    ``[SQL: ...]`` before matching."""
    constraint = _first_driver_attr(exc, "constraint_name")
    if constraint:
        return "client_key" in str(constraint)
    msg = str(exc.orig) if exc.orig else str(exc).split("[SQL:", 1)[0]
    return "client_key" in msg


class EventLogStore:
    """Event-log DB access: monotonic seq allocation (append-only) and idempotency-key deduplication."""

    def __init__(self, *, session_factory=None, user_id: str = DEFAULT_USER_ID):
        self._session_factory = session_factory or safe_session_factory
        self._user_id = user_id

    async def append(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        *,
        client_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Append entries and return the authoritative entry list with seq.

        Allocate seq using max+1 with primary-key-conflict retries (as in DbSessionStore.append).
        ``client_key`` matters only for a single user entry; its unique index handles concurrent retries."""
        if not entries:
            return []
        for attempt in range(_MAX_APPEND_RETRY):
            try:
                return await self._append_once(session_id, entries, client_key)
            except IntegrityError as exc:
                unique_violation = _is_unique_violation(exc)
                # With client_key None, this write has no client_key value and cannot violate its unique constraint.
                # Even if _is_client_key_violation misclassifies via fallback text matching,
                # exclude it structurally here so it does not disable ordinary seq-contention
                # retries.
                client_key_conflict = unique_violation and client_key is not None and _is_client_key_violation(exc)
                if client_key_conflict and client_key is not None:
                    # Concurrent idempotency-key conflict: another request wrote the same key; return its authoritative entry.
                    existing = await self.find_by_client_key(session_id, client_key)
                    if existing is not None:
                        return [existing]
                    raise
                # Only two unique constraints exist ((session_id, seq) primary key + client_key unique index);
                # excluding client_key conflict establishes seq contention without relying on driver/configuration-specific
                # error wording (primary-key DETAIL text does not necessarily contain "seq" across drivers).
                is_seq_race = unique_violation and not client_key_conflict
                if not is_seq_race or attempt == _MAX_APPEND_RETRY - 1:
                    raise
                delay = random.uniform(0, min(_APPEND_BACKOFF_CAP_S, 0.001 * (2**attempt)))
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _append_once(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        client_key: str | None,
    ) -> list[dict[str, Any]]:
        now_dt = utc_now()
        async with self._session_factory() as session:
            seq_start_row = await session.execute(
                select(func.coalesce(func.max(AgentSessionEventLogEntry.seq), -1) + 1).where(
                    AgentSessionEventLogEntry.session_id == session_id,
                )
            )
            seq_start = int(seq_start_row.scalar_one())
            enriched: list[dict[str, Any]] = []
            for i, entry in enumerate(entries):
                seq = seq_start + i
                session.add(
                    AgentSessionEventLogEntry(
                        session_id=session_id,
                        seq=seq,
                        entry_type=str(entry.get("type") or ""),
                        payload=entry,
                        client_key=client_key if len(entries) == 1 else None,
                        user_id=self._user_id,
                        created_at=now_dt,
                        updated_at=now_dt,
                    )
                )
                enriched.append({"seq": seq, **entry})
            await session.commit()
        return enriched

    async def append_user_entry(
        self,
        session_id: str,
        entry: dict[str, Any],
        *,
        client_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Append a user entry idempotently: retries with the same key return the existing entry without duplication.

        Returns:
            (authoritative entry, whether newly written)"""
        if client_key:
            existing = await self.find_by_client_key(session_id, client_key)
            if existing is not None:
                return existing, False
        appended = await self.append(session_id, [entry], client_key=client_key)
        authoritative = appended[0]
        # append returns the existing entry on idempotency-key conflict; use uuid to identify whether this write created it.
        created = authoritative.get("uuid") == entry.get("uuid")
        return authoritative, created

    async def find_by_client_key(self, session_id: str, client_key: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEventLogEntry.seq, AgentSessionEventLogEntry.payload).where(
                    AgentSessionEventLogEntry.session_id == session_id,
                    AgentSessionEventLogEntry.client_key == client_key,
                )
            )
            row = result.first()
        if row is None:
            return None
        return {"seq": int(row.seq), **row.payload}

    async def find_new_session_by_client_key(self, client_key: str) -> tuple[str, dict[str, Any]] | None:
        """Find a new-session acceptance entry (seq 0) across sessions by idempotency key, returning (session_id, authoritative entry).

        The client_key unique index partitions by (session_id, client_key), so it cannot cover new-session
        acceptance before session_id exists. When restart / LRU eviction loses the in-process mapping, this
        query ensures retries find the existing session rather than duplicate creation. Restrict to seq 0 because
        only a new session's first user entry occupies it; ordinary message idempotency keys do not participate."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    AgentSessionEventLogEntry.session_id,
                    AgentSessionEventLogEntry.seq,
                    AgentSessionEventLogEntry.payload,
                )
                .where(
                    AgentSessionEventLogEntry.client_key == client_key,
                    AgentSessionEventLogEntry.seq == 0,
                )
                .order_by(AgentSessionEventLogEntry.created_at)
                .limit(1)
            )
            row = result.first()
        if row is None:
            return None
        return str(row.session_id), {"seq": int(row.seq), **row.payload}

    async def list_after(self, session_id: str, after_seq: int = -1) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEventLogEntry.seq, AgentSessionEventLogEntry.payload)
                .where(
                    AgentSessionEventLogEntry.session_id == session_id,
                    AgentSessionEventLogEntry.seq > after_seq,
                )
                .order_by(AgentSessionEventLogEntry.seq)
            )
            rows = result.all()
        return [{"seq": int(row.seq), **row.payload} for row in rows]

    async def record_user_message_link(
        self,
        session_id: str,
        user_entry_uuid: str,
        sdk_entry_uuid: str,
    ) -> None:
        """Record user-message entry ↔ SDK transcript entry identity mapping idempotently.

        Repeated recording (a replay copy processed twice) creates no second row and does not rewrite an
        existing mapping: transcript-entry identity never changes once established; the first write is authoritative."""
        now_dt = utc_now()
        try:
            async with self._session_factory() as session:
                session.add(
                    AgentSessionUserMessageLink(
                        session_id=session_id,
                        user_entry_uuid=user_entry_uuid,
                        sdk_entry_uuid=sdk_entry_uuid,
                        user_id=self._user_id,
                        created_at=now_dt,
                        updated_at=now_dt,
                    )
                )
                await session.commit()
        except IntegrityError as exc:
            if not _is_unique_violation(exc):
                raise

    async def find_user_message_link(self, session_id: str, user_entry_uuid: str) -> str | None:
        """Look up the SDK transcript entry uuid by event-log user-entry uuid; return None without a mapping."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionUserMessageLink.sdk_entry_uuid).where(
                    AgentSessionUserMessageLink.session_id == session_id,
                    AgentSessionUserMessageLink.user_entry_uuid == user_entry_uuid,
                )
            )
            row = result.first()
        return str(row.sdk_entry_uuid) if row is not None else None

    async def delete_session(self, session_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                sa_delete(AgentSessionEventLogEntry).where(AgentSessionEventLogEntry.session_id == session_id)
            )
            await session.execute(
                sa_delete(AgentSessionUserMessageLink).where(AgentSessionUserMessageLink.session_id == session_id)
            )
            await session.commit()

    async def has_entries(self, session_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(select(exists().where(AgentSessionEventLogEntry.session_id == session_id)))
            return bool(result.scalar())

    async def last_entry(self, session_id: str) -> dict[str, Any] | None:
        """Last entry (for write-boundary checks deduplicating adjacent interrupts)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEventLogEntry.seq, AgentSessionEventLogEntry.payload)
                .where(AgentSessionEventLogEntry.session_id == session_id)
                .order_by(AgentSessionEventLogEntry.seq.desc())
                .limit(1)
            )
            row = result.first()
        if row is None:
            return None
        return {"seq": int(row.seq), **row.payload}


class TranscriptReader(Protocol):
    """Minimal transcript-reading interface for lazy generation (satisfied by SdkTranscriptAdapter)."""

    async def read_raw_messages(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def read_subagent_timelines(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None = None,
    ) -> dict[str, list[dict[str, Any]]]: ...

    async def read_subagent_descriptions(
        self,
        sdk_session_id: str | None,
        project_cwd: Path | str | None,
        tool_use_ids: Collection[str],
    ) -> dict[str, str]: ...


class EventLogService:
    """Event-log read entry point: lazy generation and cursor-based listing."""

    def __init__(self, store: EventLogStore, transcript_adapter: TranscriptReader):
        self._store = store
        self._adapter = transcript_adapter
        # Per-session lazy-generation lock: automatically reclaimed with no coroutine holding/waiting; empty-session locks do not persist in memory.
        self._backfill_locks = KeyedLocks()

    @property
    def store(self) -> EventLogStore:
        return self._store

    async def ensure_backfilled(self, session_id: str, project_cwd: Path | str | None) -> None:
        """Replay and reconstruct the log on first access to legacy transcript-only sessions.

        Merge transcript subagent subpaths: sub-timeline entries carry parent_tool_use_id and follow the
        main-line entry containing the corresponding Task tool_use. Preserve each subgroup's own order;
        no global sorting is needed."""
        if await self._store.has_entries(session_id):
            return
        lock = self._backfill_locks.lock_for(session_id)
        async with lock:
            if await self._store.has_entries(session_id):
                return
            raw_messages = await self._adapter.read_raw_messages(session_id, project_cwd)
            try:
                subagent_groups = await self._adapter.read_subagent_timelines(session_id, project_cwd)
            except Exception:
                logger.exception("subagent 子时间线读取失败，跳过合并 session_id=%s", session_id)
                subagent_groups = {}
            # Historical transcripts reconstruct only existing timelines; ADR 0052 explicitly prohibits inferring
            # and adding failure events from them. Failures are finalized only at the live write boundary.
            normalizer = SdkMessageNormalizer(capture_failures=False)
            entries: list[dict[str, Any]] = []
            project_name = Path(project_cwd).name if project_cwd is not None else None

            def _consume(message: dict[str, Any], parent_tool_use_id: str | None = None) -> None:
                if parent_tool_use_id:
                    message = {**message, "parent_tool_use_id": parent_tool_use_id}
                try:
                    for entry in normalizer.normalize(
                        message,
                        project_name=project_name,
                        session_id=session_id,
                    ):
                        # Collapse adjacent interrupt echoes (SDK echo + racing copies) into one,
                        # matching live write-boundary tail-check deduplication semantics.
                        if is_interrupt_entry(entry) and entries and is_interrupt_entry(entries[-1]):
                            continue
                        entries.append(entry)
                except Exception:
                    # Tolerate historical transcript evolution across versions: one dirty entry must not fail lazy generation
                    # of an entire legacy session's history.
                    logger.exception("历史消息规范化失败，跳过该条 session_id=%s", session_id)

            for message in raw_messages:
                _consume(message)
                for tool_use_id in _assistant_tool_use_ids(message):
                    for sub_message in subagent_groups.pop(tool_use_id, []):
                        _consume(sub_message, tool_use_id)
            # Log all remaining subgroups missing main-line anchor tool_use (common after compaction continuation):
            # frontend groups by parent and shows independent cards without anchors, preserving all sub-timeline data.
            # Append an inferred description and terminal state to each group for card display instead of always showing stopped.
            descriptions: dict[str, str] = {}
            if subagent_groups:
                try:
                    descriptions = await self._adapter.read_subagent_descriptions(
                        session_id, project_cwd, list(subagent_groups)
                    )
                except Exception:
                    logger.exception("子代理调用描述读取失败，改用子时间线推断 session_id=%s", session_id)
            for tool_use_id, group in subagent_groups.items():
                for sub_message in group:
                    _consume(sub_message, tool_use_id)
                outcome = infer_subagent_outcome(tool_use_id, group, descriptions.get(tool_use_id))
                if outcome is not None:
                    entries.append(outcome)
            if entries:
                await self._store.append(session_id, entries)
                # Clear lock references only after successful writing: has_entries stays true thereafter, so both old-lock waiters
                # and new-lock arrivals short-circuit at the second check. Keep the lock for empty transcripts;
                # otherwise later setdefault creates a new lock, allowing replay concurrent with old-lock waiters and duplicate ingestion.
                self._backfill_locks.discard(session_id)

    async def list_entries(
        self,
        session_id: str,
        project_cwd: Path | str | None,
        *,
        after_seq: int = -1,
    ) -> list[dict[str, Any]]:
        await self.ensure_backfilled(session_id, project_cwd)
        return await self._store.list_after(session_id, after_seq)

    async def resolve_user_message_anchor(
        self,
        session_id: str,
        entry_uuid: str,
        project_cwd: Path | str | None,
    ) -> str | None:
        """Resolve an event-log user-entry uuid to a transcript-domain entry uuid.

        Two stages: identity mapping first, then identity-equality fallback on a miss. The mapping table is the
        only live-path route: ``user-<hex>`` minted at POST acceptance precedes SDK uuid allocation, so the
        two domains naturally have different names and require a mapping. Other entries already share uuids
        across domains: lazy generation takes user-entry uuids directly from transcript entries, and prefix
        branch copying preserves them, so branches of branches also work.

        Fallback accepts only main-line plain user entries (no subtype or parent_tool_use_id): question answers,
        tool_result, skill injections, etc. have derived/finalized uuids that are meaningless as anchors. The
        returned uuid is not checked for transcript existence. A live-path entry with no mapping reaches here
        and returns a uuid absent from the transcript; prefix branching fails loudly during slicing instead
        of silently switching to another location."""
        linked = await self._store.find_user_message_link(session_id, entry_uuid)
        if linked is not None:
            return linked
        await self.ensure_backfilled(session_id, project_cwd)
        for entry in await self._store.list_after(session_id):
            if entry.get("uuid") != entry_uuid:
                continue
            if entry.get("type") != ENTRY_TYPE_USER or entry.get("subtype") or entry.get("parent_tool_use_id"):
                return None
            return entry_uuid
        return None
