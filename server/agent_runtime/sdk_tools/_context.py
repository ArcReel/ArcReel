"""Per-session context shared by ArcReel SDK MCP tool handlers."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation_result import GenerationBatchResult, migration_problem, render_generation_result
from lib.project_manager import ProjectManager
from lib.project_migration_failure import MigrationFailureRecord
from lib.project_migration_guard import project_migration_failure
from server.services import workflow_planner
from server.services.video_caps import (
    constrained_caps_durations,
    resolve_video_caps,
)
from server.services.video_caps import (
    reference_unit_duration_tiers as reference_unit_duration_tiers,
)
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome

logger = logging.getLogger(__name__)


class ToolContext:
    """Bind a tool handler to one agent session's project + projects_root.

    Project-scoped tools are closure-bound to ``project_name``. Project entry
    tools may address another project, but only through this ``projects_root``.
    """

    def __init__(
        self,
        project_name: str,
        projects_root: Path,
        pm: ProjectManager | None = None,
        *,
        config_resolver: ConfigResolver | None = None,
        caller: CallerContext | None = None,
    ):
        self.project_name = project_name
        self.projects_root = projects_root
        # Avoid ``ProjectManager.from_cwd()`` — the server main process cwd is
        # the repo root, not ``projects/<name>/``. Tests may inject a fake pm.
        self.pm: ProjectManager = pm if pm is not None else ProjectManager(str(projects_root))
        self.config_resolver = config_resolver
        self.caller = caller or CallerContext(user_id=DEFAULT_USER_ID, source="embedded")

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)

    @property
    def scope(self) -> ProjectScope:
        return ProjectScope(project_name=self.project_name, projects_root=self.projects_root)


def tool_services(ctx: ToolContext) -> Services:
    return Services(
        projects=ctx.pm,
        workflow_planner=workflow_planner.get_workflow_planner(ctx.pm),
        capabilities=ctx.config_resolver or ConfigResolver(async_session_factory),
    )


def tool_outcome_response(domain_key: str, outcome: ToolOutcome[Any]) -> dict[str, Any]:
    """Encode a host-independent outcome into the subset the Claude SDK preserves."""
    if outcome.problem is not None:
        payload = outcome.problem.model_dump(mode="json")
        return {
            "content": [{"type": "text", "text": json.dumps({"problem": payload}, ensure_ascii=False)}],
            "is_error": True,
            "problem": payload,
        }
    value = outcome.value
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)
    else:
        payload = value
    return {"content": [{"type": "text", "text": json.dumps({domain_key: payload}, ensure_ascii=False)}]}


async def migration_failure_for(ctx: ToolContext) -> MigrationFailureRecord | None:
    """This session's project migration verdict, or ``None`` when it is healthy.

    The registration-time write guard and the read-only tools that answer the
    verdict inside their own handlers both go through here, so every refusal in
    the tool layer rests on the same persisted record read the same way, off the
    event loop.
    """
    return await asyncio.to_thread(project_migration_failure, ctx.project_name, ctx.pm)


def tool_error(name: str, exc: BaseException, log: list[str] | None = None) -> dict[str, Any]:
    """Build the ``{"is_error": True}`` response every SDK tool handler emits on failure."""
    msg = f"{name} 失败: {exc}"
    text = "\n".join([msg, *log]) if log else msg
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def migration_refusal_response(failure: MigrationFailureRecord, *, text: str) -> dict[str, Any]:
    """Envelope the migration verdict as an SDK MCP tool refusal.

    Every tool that reports the verdict — the blocked ones wrapped at
    registration and the retry tool when the rerun fails again — returns this
    one shape, so the agent reads a single ``problem`` payload carrying the
    named episode / file / violation instead of two envelopes for one fact.
    """
    problem = migration_problem(failure)
    payload = problem.model_dump(mode="json")
    return {
        "content": [{"type": "text", "text": text + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)}],
        "is_error": True,
        "problem": payload,
    }


def generation_result_response(
    result: GenerationBatchResult,
    log: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Envelope one generation batch contract for an SDK MCP tool response.

    ``generation_result`` is the machine-readable payload; the text block is a
    rendering of the same fields, so no consumer has to parse it to decide
    whether to retry.
    """
    return {
        "content": [{"type": "text", "text": render_generation_result(result, log=log or ())}],
        "is_error": not result.ok,
        "generation_result": result.model_dump(mode="json"),
        **extra,
    }


# instructions 超长会失控 token 用量并稀释模型对原文的处理，超限按参数错误提前拒绝。
# 上限对意见文本足够宽松，仅挡病态输入。
MAX_INSTRUCTIONS_LEN = 4000


def _param_error(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"❌ 参数错误：{msg}"}], "is_error": True}


def read_instructions_arg(args: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """取可选 ``instructions`` 入参（分集生成工具共享）：空白 strip 后视同未传。

    返回 ``(instructions, error)``：入参非法（非字符串 / 超长）时 ``error`` 是现成的
    参数错误响应，调用方直接 return。
    """
    raw = args.get("instructions")
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        logger.debug("instructions 入参类型非法: %s", type(raw).__name__)
        return None, _param_error("instructions 必须是文本")
    if len(raw) > MAX_INSTRUCTIONS_LEN:
        return None, _param_error(f"instructions 过长（{len(raw)} 字符，上限 {MAX_INSTRUCTIONS_LEN}），请精简后重试")
    text = raw.strip()
    return (text or None), None


async def fetch_video_caps(
    project: dict[str, Any],
    *,
    generation_mode: str | None = None,
    config_resolver: ConfigResolver | None = None,
) -> tuple[int | None, list[int]]:
    """Resolve ``(default_duration, supported_durations)`` for an MCP tool call.

    ``supported_durations`` 已按项目分辨率与 ``generation_mode`` 经时长联动约束收窄：型号声明的
    全集不含「分辨率↔时长」「参考图↔时长」两条约束，未收窄的集合交给 LLM 会产出执行期必然被拒
    的时长。``default_duration`` 是用户配置的原样值，成员性由调用方按各自口径判定。
    Callers decide whether an empty result is a hard error (video generation) or
    a soft fallback (script normalization).
    """
    if config_resolver is None:
        caps = await resolve_video_caps(project)
    else:
        caps = await resolve_video_caps(project, config_resolver=config_resolver)
    durations = [int(d) for d in caps.get("supported_durations") or []]
    durations = constrained_caps_durations(project, caps, durations, generation_mode=generation_mode)
    default = caps.get("default_duration")
    default_int = int(default) if isinstance(default, int | float) else None
    return default_int, durations


def validate_script_filename(value: str) -> str:
    """Reject any agent-provided ``script`` arg that is not a bare basename.

    Agents must reference scripts by filename only (e.g. ``episode_1.json``);
    the project root is bound by ``ToolContext`` and the ``scripts/`` subdir
    is fixed inside ``ProjectManager.load_script``. Any path separator —
    including a ``scripts/`` prefix or ``..`` segments — is rejected.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("script 文件名不能为空")
    if "/" in value or "\\" in value or value in (".", ".."):
        raise ValueError(f"script 必须是纯文件名，禁止路径分隔符: {value!r}")
    return value
