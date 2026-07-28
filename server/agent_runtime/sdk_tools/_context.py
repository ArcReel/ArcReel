"""Per-session context shared by ArcReel SDK MCP tool handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.config.resolver import ConfigResolver, constrain_durations_for_project
from lib.db import async_session_factory
from lib.project_manager import ProjectManager


class ToolContext:
    """Bind a tool handler to one agent session's project + projects_root.

    The agent never names the project explicitly — every tool is closure-bound
    to ``project_name`` via ``build_arcreel_mcp_server(project_name=...)``.
    """

    def __init__(self, project_name: str, projects_root: Path, pm: ProjectManager | None = None):
        self.project_name = project_name
        self.projects_root = projects_root
        # Avoid ``ProjectManager.from_cwd()`` — the server main process cwd is
        # the repo root, not ``projects/<name>/``. Tests may inject a fake pm.
        self.pm: ProjectManager = pm if pm is not None else ProjectManager(str(projects_root))

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)


def tool_error(name: str, exc: BaseException, log: list[str] | None = None) -> dict[str, Any]:
    """Build the ``{"is_error": True}`` response every SDK tool handler emits on failure."""
    msg = f"{name} 失败: {exc}"
    text = "\n".join([msg, *log]) if log else msg
    return {"content": [{"type": "text", "text": text}], "is_error": True}


async def resolve_video_caps(project: dict[str, Any]) -> dict[str, Any]:
    """Resolve the full video capability dict for an MCP tool call.

    Single source of truth for video model capability lookup across SDK MCP
    tools. Callers that only need durations should use :func:`fetch_video_caps`;
    this variant exposes the model identity so the caller can evaluate the
    duration linkage constraints declared on it.
    """
    resolver = ConfigResolver(async_session_factory)
    return await resolver.video_capabilities_for_project(project)


def constrained_caps_durations(
    project: dict[str, Any],
    caps: dict[str, Any],
    durations: list[int],
    *,
    generation_mode: str | None,
) -> list[int]:
    """按 caps 里的模型身份对 ``durations`` 施加时长联动约束（见 ``constrain_durations_for_project``）。

    ``durations`` 单独传入而非从 caps 取：调用方对该集合做过自己的软回退 / 过滤，收窄要作用在
    那个结果上，不能绕回 caps 的原始集合。
    """
    return constrain_durations_for_project(
        project,
        durations,
        provider_id=caps.get("provider_id"),
        model_id=caps.get("model"),
        generation_mode=generation_mode,
    )


async def fetch_video_caps(
    project: dict[str, Any], *, generation_mode: str | None = None
) -> tuple[int | None, list[int]]:
    """Resolve ``(default_duration, supported_durations)`` for an MCP tool call.

    ``supported_durations`` 已按项目分辨率与 ``generation_mode`` 经时长联动约束收窄：型号声明的
    全集不含「分辨率↔时长」「参考图↔时长」两条约束，未收窄的集合交给 LLM 会产出执行期必然被拒
    的时长。``default_duration`` 是用户配置的原样值，成员性由调用方按各自口径判定。
    Callers decide whether an empty result is a hard error (video generation) or
    a soft fallback (script normalization).
    """
    caps = await resolve_video_caps(project)
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
