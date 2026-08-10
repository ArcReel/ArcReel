"""前缀分叉：把会话 transcript 在锚点之前的部分复制到新 session_id 下。

「以拼装出的前缀供 SDK resume」是非官方用法，风险与知识收敛在本模块（见
``docs/adr/0058``）。只依赖 SessionStore 协议的 ``load`` / ``append`` /
``list_subkeys``，不碰 SQL——seq 分配与 summary fold 由 store 自己维护。

条目 uuid 原样保留：唯一索引与 SDK 物化目录都以 session_id 分域，跨会话同
uuid 不照面，因此 parentUuid 链、sidechain 独立图与交叉引用都无需重映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

SUBAGENT_SUBPATH_ROOT = "subagents/"
SUBAGENT_LEAF_PREFIX = "agent-"

# 会话条目类型：前缀里没有任何一条即视为空前缀。transcript 开头常有
# file-history-snapshot 之类的非会话元数据行，行数非零不等于有对话。
_CONVERSATION_ENTRY_TYPES = frozenset({"user", "assistant"})


class InvalidAnchorError(ValueError):
    """锚点不是原会话主时间线上的用户消息。"""


class SessionStoreLike(Protocol):
    """本模块用到的 SessionStore 子集。"""

    async def load(self, key: dict) -> list[dict] | None: ...

    async def append(self, key: dict, entries: list[dict]) -> None: ...

    async def list_subkeys(self, key: dict) -> list[str]: ...

    async def delete(self, key: dict) -> None: ...


@dataclass(frozen=True, slots=True)
class PrefixCopyResult:
    """复制结果。``entries_copied == 0`` 即空前缀，新会话无 transcript 可 resume。"""

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
    """把 ``session_id`` 中锚点之前的条目复制到 ``new_session_id`` 下。

    ``anchor_uuid`` 指向原会话主时间线上的一条用户消息，切片**不含**该条本身
    ——语义是「回到这条消息发出前」。锚点固定在用户消息边界，前一轮次必然完整
    收尾，tool_use / tool_result 配对与 subagent 子时间线的完整性由此保证。

    命中前缀的 subagent 子时间线整段随行：从前缀内 tool_result 的
    ``toolUseResult.agentId`` 收集 agent id，对应子路径全量复制。子路径按末段
    ``agent-<id>`` 匹配，SDK 自身也这么找——它既可能是 ``subagents/agent-<id>``，
    也可能嵌套在 ``subagents/workflows/<runId>/`` 下。
    """
    main_entries = await store.load({"project_key": project_key, "session_id": session_id}) or []
    prefix = _prefix_before_anchor(main_entries, anchor_uuid)

    if not any(_entry_type(entry) in _CONVERSATION_ENTRY_TYPES for entry in prefix):
        return PrefixCopyResult(entries_copied=0, subagent_subpaths=())

    origin = {"sessionId": session_id, "messageUuid": anchor_uuid}
    rebound = [_rebind(entry, new_session_id=new_session_id, origin=origin) for entry in prefix]
    # 末条重打时间戳：SDK 自身的 fork 同样如此，供 resume 时的叶子节点判定。
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


def _prefix_before_anchor(entries: list[dict], anchor_uuid: str) -> list[dict]:
    for index, entry in enumerate(entries):
        if entry.get("uuid") != anchor_uuid:
            continue
        if _entry_type(entry) != "user":
            raise InvalidAnchorError(f"anchor {anchor_uuid} is a {_entry_type(entry)!r} entry, not a user message")
        return entries[:index]
    raise InvalidAnchorError(f"anchor {anchor_uuid} is not on the main timeline of session")


def _matches_subagent(subpath: str, wanted_leaves: set[str]) -> bool:
    return subpath.startswith(SUBAGENT_SUBPATH_ROOT) and subpath.rsplit("/", 1)[-1] in wanted_leaves


def _prefix_agent_ids(prefix: list[dict]) -> set[str]:
    agent_ids: set[str] = set()
    for entry in prefix:
        tool_use_result = entry.get("toolUseResult")
        if not isinstance(tool_use_result, dict):
            continue
        agent_id = tool_use_result.get("agentId")
        if isinstance(agent_id, str) and agent_id:
            agent_ids.add(agent_id)
    return agent_ids


def _rebind(entry: dict[str, Any], *, new_session_id: str, origin: dict[str, str]) -> dict[str, Any]:
    """改写条目归属。

    ``sessionId`` 仅在原条目已有时改写，``forkedFrom`` 仅加在 transcript 行上
    ——subagent 子路径里的 ``agent_metadata`` 条目会被 SDK 原样写成 .meta.json
    旁车文件，多出的字段会跟着落进去。
    """
    rebound = dict(entry)
    if "sessionId" in rebound:
        rebound["sessionId"] = new_session_id
    if isinstance(entry.get("uuid"), str) and entry["uuid"]:
        rebound["forkedFrom"] = origin
    return rebound


def _entry_type(entry: dict[str, Any]) -> str:
    entry_type = entry.get("type")
    return entry_type if isinstance(entry_type, str) else ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
