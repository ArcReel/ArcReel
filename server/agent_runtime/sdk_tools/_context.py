"""Per-session context shared by ArcReel SDK MCP tool handlers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from lib.config.resolver import ConfigResolver, VideoCapability, constrain_durations_for_project
from lib.db import async_session_factory
from lib.generation_result import GenerationBatchResult, migration_problem, render_generation_result
from lib.project_manager import ProjectManager
from lib.project_migration_failure import MigrationFailureRecord

logger = logging.getLogger(__name__)


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
        return None, _param_error(f"instructions 必须是字符串，收到 {type(raw).__name__}")
    if len(raw) > MAX_INSTRUCTIONS_LEN:
        return None, _param_error(f"instructions 过长（{len(raw)} 字符，上限 {MAX_INSTRUCTIONS_LEN}），请精简后重试")
    text = raw.strip()
    return (text or None), None


async def resolve_video_caps(project: dict[str, Any], *, capability: VideoCapability | None = None) -> dict[str, Any]:
    """Resolve the full video capability dict for an MCP tool call.

    Single source of truth for video model capability lookup across SDK MCP
    tools. Callers that only need durations should use :func:`fetch_video_caps`;
    this variant exposes the model identity so the caller can evaluate the
    duration linkage constraints declared on it.

    能力按项目生成模式解析——生成模式创建即定、全项目同一条，智能体拿到的与执行层同口径。
    ``capability`` 给定时按指定桶解析（参考路线内无参考图退化镜头的 i2v 读侧）。
    """
    resolver = ConfigResolver(async_session_factory)
    return await resolver.video_capabilities_for_project(project, capability=capability)


def constrained_caps_durations(
    project: dict[str, Any],
    caps: dict[str, Any],
    durations: list[int],
    *,
    generation_mode: str | None,
    uses_reference_images: bool | None = None,
) -> list[int]:
    """按 caps 里的模型身份对 ``durations`` 施加时长联动约束（见 ``constrain_durations_for_project``）。

    ``durations`` 单独传入而非从 caps 取：调用方对该集合做过自己的软回退 / 过滤，收窄要作用在
    那个结果上，不能绕回 caps 的原始集合。

    ``uses_reference_images`` 缺省时按生成模式近似判定；调用方能区分「这次求的是带参考图还是
    不带参考图的档位」时应显式传入。
    """
    return constrain_durations_for_project(
        project,
        durations,
        provider_id=caps.get("provider_id"),
        model_id=caps.get("model"),
        generation_mode=generation_mode,
        uses_reference_images=uses_reference_images,
    )


async def reference_unit_duration_tiers(
    project: dict[str, Any],
    caps: dict[str, Any],
    durations: list[int],
) -> tuple[list[int], list[int]]:
    """参考视频路径逐 unit 的两套生效档位：``(带参考图, 不带参考图)``。

    「参考图↔时长」约束只对实际带参考图的请求生效（执行层 ``effective_reference_durations``
    与 backend 同此判据），故 unit 的生效档位取决于该 unit 正文里有没有 ``@[名称]`` 引用。
    整集按其中一套一刀切都有代价：一律按带图算会收掉无引用 unit 本可申请的短档，一律按不带图
    算则会让带引用的 unit 拆出执行期申请不到的时长。

    不带图集按 **i2v 桶模型**求值：无引用 unit 执行期降级到 i2v 桶执行，档位跟着执行模型走，
    否则两桶模型不同时创作侧会放行只有 r2v 桶才有的秒数、漏掉 i2v 桶独有的秒数。``caps`` /
    ``durations`` 是调用方按路线主桶（r2v）解析并做过软回退的结果，只用于带图集；i2v 桶解析
    失败或无档位声明时，不带图集回退按同一份 r2v 输入求值（退回「两桶同模型」的既有行为，
    档位标注不挡主流程）。

    两套集合之间**不假定包含关系**：``constrain_durations`` 在交集为空时回退到未收窄的候选，
    带图集因而可能反过来比不带图集宽（型号同时声明「带图仅 8s」与「1080p 仅 6s」即是）。
    需要「任一状态下合法」的并集时由调用方对两个返回值取并，不要拿其中一个当上界。
    """
    with_references = constrained_caps_durations(
        project, caps, durations, generation_mode="reference_video", uses_reference_images=True
    )
    i2v_caps, i2v_durations = caps, durations
    try:
        resolved = await resolve_video_caps(project, capability="i2v")
        resolved_durations = [int(d) for d in resolved.get("supported_durations") or []]
        if resolved_durations:
            i2v_caps, i2v_durations = resolved, resolved_durations
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("i2v 桶能力不可解析，不带图档位回退按 r2v 桶求值：%s", exc)
    without_references = constrained_caps_durations(
        project, i2v_caps, i2v_durations, generation_mode="reference_video", uses_reference_images=False
    )
    return with_references, without_references


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
