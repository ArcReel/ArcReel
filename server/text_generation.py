"""Host-independent implementations for ArcReel text-generation tools."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
)
from lib.artifacts.artifact_provenance import ScriptPlanPromptVariant, build_script_plan_request
from lib.artifacts.formal_write import formal_write_transaction
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextOutputTruncatedError, TextTaskType
from lib.backends.text_backends.base import TextGenerationRequest as BackendTextGenerationRequest
from lib.backends.text_generator import TextGenerator
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ConfigResolver, NoAvailableProviderError
from lib.episode.episode_ids import describe_episode_for_agent
from lib.episode.episode_paths import (
    SCRIPT_PLAN_FILENAMES,
    episode_drafts_dir,
    episode_source_relpath,
)
from lib.episode.episode_sources import episode_entry
from lib.episode.source_kinds import DEFAULT_SOURCE_KIND
from lib.generation.video_request_facts import (
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    planning_durations,
    require_video_request_facts,
)
from lib.i18n import _ as translate
from lib.infra.async_thread import run_sync_transaction
from lib.infra.content_digest import prefixed_sha256_file
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.schema_guards import is_int, is_str
from lib.infra.text_utils import strip_json_code_fences
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.prompts.prompt_builders_reference import build_reference_units_split_prompt
from lib.prompts.prompt_builders_script import build_narration_split_prompt, build_normalize_prompt
from lib.script import script_review
from lib.script.draft_quarantine import (
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    clear_quarantine,
    quarantine_and_report,
    quarantine_path,
    read_quarantine,
)
from lib.script.draft_violation import DraftViolation
from lib.script.plan_new_assets import (
    NEW_ASSETS_FIELD,
    dedupe_new_assets,
    with_new_assets,
)
from lib.script.prompt_authoring_scope import PromptOverwriteRequired, prompt_overwrite_with_text
from lib.script.reference_video.script_preview import (
    WARN_UNIT_WITHOUT_SCENE,
)
from lib.script.reference_video.text_parser import extract_mentions
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_generator import (
    AdScriptOverwriteRequired,
    AdScriptRejected,
    PromptAuthoringTargetError,
    PromptAuthoringUnitRejected,
    ScriptGenerator,
)
from lib.script.script_models import (
    build_drama_script_plan_model,
    build_narration_script_plan_model,
    build_reference_units_script_plan_model,
)
from lib.script.script_plan_validation import (
    EpisodePlanSource,
    ReferenceSplitCaps,
    SoftViolation,
    validate_script_plan,
)
from lib.script.script_skeleton import resolve_kind_items
from lib.script.storyboard_mentions import render_storyboard_mention_warnings, storyboard_mention_warnings
from lib.workflow.operation_admission import (
    AdmissionReason,
    OperationAdmission,
    ad_inputs_present,
    admit_ad_script,
    admit_author_prompts,
    admit_script_plan,
    episode_source_present,
)
from lib.workflow.workflow_state import WorkflowStateService
from server.services.tasks.video_caps import reference_request_facts_lookup, storyboard_request_facts

logger = logging.getLogger(__name__)

MAX_INSTRUCTIONS_LEN = 4000

SCOPE_REMOVED_MESSAGE = (
    "scope 参数已取消：generate_episode_script 默认只编写正式脚本中全部待编写的条目；"
    "要覆盖已有提示词的条目，请用 entry_ids 点名这些条目并传 rewrite=true；要整集重做，请重跑脚本规划并重新完成内容确认。"
)


@dataclass(frozen=True, slots=True)
class TextGenerationRequest:
    episode: int
    source: str | None = None
    instructions: str | None = None
    dry_run: bool = False
    entry_ids: tuple[str, ...] = ()
    rewrite: bool = False
    overwrite_revision: str | None = None
    regenerate: bool = False

    def __post_init__(self) -> None:
        if not is_int(self.episode, minimum=1):
            raise ValueError("episode must be a positive integer")
        entry_ids = tuple(self.entry_ids)
        if any(not is_str(entry_id) or not entry_id for entry_id in entry_ids):
            raise ValueError("entry_ids must be non-empty strings")
        object.__setattr__(self, "entry_ids", entry_ids)

    def to_payload(self) -> dict[str, object]:
        """队列任务 payload：只用 JSON 原生类型。

        ``asdict`` 会把 ``entry_ids`` 留成 tuple，而从数据库读回来的同一份 payload 是 list；
        「本次请求与在跑任务是否同一件事」的比对按值相等判定，两种容器类型会让同一个请求
        判成不同，把幂等提交变成一次任务冲突。
        """
        return {**asdict(self), "entry_ids": list(self.entry_ids)}


@dataclass(frozen=True, slots=True)
class TextGenerationResult:
    message: str
    warnings: list[dict[str, Any]] = dataclass_field(default_factory=list)
    new_assets: list[dict[str, str]] = dataclass_field(default_factory=list)
    no_pending_authoring: bool = False


class TextGenerationError(Exception):
    """Expected refusal from a text-generation handler."""


class DraftQuarantinedError(TextGenerationError):
    """违约产出已存为待修复草稿；消息保持 Agent 报告原文，计数供任务摘要使用。"""

    def __init__(self, report: str, count: int) -> None:
        super().__init__(report)
        self.count = count


class UnexpectedTextGenerationError(TextGenerationError):
    """生成过程中的意外失败，原异常在 ``__cause__``：调用方据此区分供应商拒绝与可重试的任务失败。"""


class PromptAuthoringInputError(TextGenerationError):
    """提示词编写的输入没有通过调用文本模型前的预判，修改输入后才能编写；携带任务失败的问题码与参数。"""

    def __init__(self, message: str, *, code: str, params: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


class PromptOverwriteRequiredError(TextGenerationError):
    """提示词显式重写会替换已有视觉层内容，而调用方未认可覆盖；携带丢失清单（含 ``text``）。"""

    def __init__(self, message: str, overwrite: dict[str, Any]) -> None:
        super().__init__(message)
        self.overwrite = overwrite


def _prompt_overwrite_error(episode_label: str, exc: PromptOverwriteRequired) -> PromptOverwriteRequiredError:
    overwrite = prompt_overwrite_with_text(exc.overwrite.to_dict(), translate)
    return PromptOverwriteRequiredError(
        f"⚠️ {episode_label} 的显式重写需要用户确认覆盖，本次未调用文本模型、未写入。\n{overwrite['text']}\n"
        "须先把上面的丢失清单原文转述给用户，得到明确同意后，再以 "
        "overwrite_revision=params.prompt_overwrite.revision 重新调用；"
        "正式脚本在此期间又有变化时会按新清单再次拒绝。",
        overwrite,
    )


class AdScriptRejectedError(TextGenerationError):
    """广告/短片整份生成的产出违约：任务失败，正式脚本、资产与草稿都不变；携带逐条违约。"""

    def __init__(self, message: str, problems: Sequence[str]) -> None:
        super().__init__(message)
        self.problems = tuple(problems)


class VideoRequestFactsUnavailableError(TextGenerationError):
    """视频请求事实解析不出（模型能力不满足、时长档位缺失等），文本任务无法继续；携带失败对象。

    ``failure`` 的问题码、参数与修复指引原样进入工具问题与任务失败，读侧按问题码渲染原因。
    """

    def __init__(self, failure: VideoRequestFactsFailure) -> None:
        super().__init__(video_facts_failure_text(failure))
        self.failure = failure


class ScriptOverwriteRequiredError(TextGenerationError):
    """内容确认会覆盖该集已有的正式脚本，而调用方未认可覆盖；携带将被移除的条目与产物摘要。"""

    def __init__(self, message: str, overwrite: dict[str, Any] | None) -> None:
        super().__init__(message)
        self.overwrite = overwrite


def _draft_file_revision(path: Path) -> str | None:
    try:
        return prefixed_sha256_file(path)
    except FileNotFoundError:
        return None


def _generation_baselines(
    draft_path: Path,
    formal_path: Path,
) -> tuple[str | None, str | None]:
    return _draft_file_revision(draft_path), script_review.content_fingerprint(formal_path)


def _assert_draft_revision(path: Path, expected: str | None) -> None:
    actual = _draft_file_revision(path)
    if actual != expected:
        raise TextGenerationError(
            f"draft_revision_conflict: draft changed during generation; expected {expected}, actual {actual}"
        )


def _after_draft_revision_check[**P, T](
    draft_path: Path,
    expected: str | None,
    func: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """草稿修订校验与随后的写入放进同一次线程调用，二者之间不让出事件循环。"""
    _assert_draft_revision(draft_path, expected)
    return func(*args, **kwargs)


def _quarantine_formal_generation_conflict(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    source: str | None,
    expected: str | None,
    actual: str | None,
) -> str:
    return quarantine_and_report(
        project_path,
        episode,
        kind,
        content=content,
        violations=[
            DraftViolation(
                f"正式内容在模型生成期间已变化（expected {expected}, actual {actual}）；"
                "本次生成结果已保留为草稿，请合并最新正式内容后再晋升",
                code="formal_revision_conflict",
                params={},
            )
        ],
        meta={"source": source, "base_fingerprint": expected},
    )


def _commit_generated_reference_script_plan(
    project_path: Path,
    episode: int,
    content: dict[str, Any],
    expected_fingerprint: str | None,
    basis: ArtifactBasis,
    before_commit: Callable[[], None] | None = None,
) -> None:
    if before_commit is not None:
        before_commit()
    draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
    prompt_authoring_path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    formal_path = script_review.official_reference_script_plan_path(project_path, episode)
    pm = ProjectManager.for_project_dir(project_path)
    with (
        pm.file_lock(prompt_authoring_path),
        script_review.script_plan_write_lock(project_path, episode),
        formal_write_transaction(formal_path, prompt_authoring_path, draft_path),
    ):
        script_review.write_script_plan_locked(
            project_path,
            episode,
            content,
            expected_fingerprint=expected_fingerprint,
            basis=basis,
        )
        clear_quarantine(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)


def plan_item_ids(path: Path) -> list[str]:
    """盘上那份脚本规划的条目编号（按顺序）；文件不在或读不出时为空。参考生视频规划的单元数组键是 ``units``。"""
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(content, dict) and "units" in content:
            items, id_field = content["units"], "unit_id"
        else:
            items, id_field, _kind = resolve_kind_items(content)
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [item[id_field] for item in items if isinstance(item, dict) and isinstance(item.get(id_field), str)]


async def reissue_plan_item_ids(
    projects: ProjectManager,
    project_name: str,
    episode: int,
    content: dict[str, Any],
    *,
    keep: Collection[str] = (),
) -> None:
    """脚本规划落盘前由服务端给条目发号，就地改写：编号只由服务端发，只分配、不复用。

    ``keep`` 是此前已由服务端发给这份脚本规划的编号（晋升时盘上正式规划的编号）：条目带着其中之一且不重复时
    沿用，原样晋升的规划因此逐字不变；模型或草稿里自己写的编号一律换成新发的号。
    """
    items, id_field, kind = resolve_kind_items(content)
    if not isinstance(items, list):
        return
    kept: set[str] = set()
    fresh: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get(id_field)
        if isinstance(item_id, str) and item_id in keep and item_id not in kept:
            kept.add(item_id)
        else:
            fresh.append(item)
    if not fresh:
        return
    issued = await run_sync_transaction(projects.reserve_item_ids, project_name, episode, kind, len(fresh))
    for item, item_id in zip(fresh, issued, strict=True):
        item[id_field] = item_id


def commit_single_script_plan(
    project_path: Path,
    episode: int,
    script_plan_path: Path,
    kind: str,
    content: dict[str, Any],
    expected_fingerprint: Any,
    basis: ArtifactBasis | None,
) -> None:
    draft_path = quarantine_path(project_path, episode, kind)
    with (
        script_review.formal_script_plan_lock(project_path, episode, script_plan_path),
        formal_write_transaction(script_plan_path, draft_path),
    ):
        script_review.write_formal_script_plan_locked(
            project_path,
            episode,
            script_plan_path,
            content,
            expected_fingerprint=expected_fingerprint,
            basis=basis,
        )
        clear_quarantine(project_path, episode, kind)


def _quarantine_invalid_script_plan_generation(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    violations: list[DraftViolation],
    source: str | None,
    base_fingerprint: str | None,
) -> str:
    with formal_write_transaction(quarantine_path(project_path, episode, kind)):
        return quarantine_and_report(
            project_path,
            episode,
            kind,
            content=content,
            violations=violations,
            meta={"source": source or None, "base_fingerprint": base_fingerprint},
        )


def _instructions(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TextGenerationError("❌ 参数错误：instructions 必须是文本")
    if len(value) > MAX_INSTRUCTIONS_LEN:
        raise TextGenerationError(
            f"❌ 参数错误：instructions 过长（{len(value)} 字符，上限 {MAX_INSTRUCTIONS_LEN}），请精简后重试"
        )
    return value.strip() or None


def _project_default_duration(project: dict[str, Any]) -> int | None:
    """用户在项目里配置的默认秒数原样值；非法值按未配置。"""
    raw = project.get("default_duration")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def video_facts_failure_text(failure: VideoRequestFactsFailure) -> str:
    """视频请求事实解析不出时回给 Agent 的说明：按问题码陈述原因与出路，附问题码与参数摘要。

    问题码与参数就是失败契约，与预检、执行同码。
    """
    params: dict[str, Any] = failure.parameters()
    return f"❌ {translate(failure.code, **params)}（{failure.summary()}）"


async def fetch_storyboard_durations(
    project: dict[str, Any],
    *,
    config_resolver: ConfigResolver | None = None,
) -> tuple[int | None, list[int]]:
    """分镜路线剧本规划的 ``(default_duration, supported_durations)``，档位取分镜桶的视频请求事实。

    ``supported_durations`` 已按项目分辨率经时长联动约束收窄（时长由端点固定时借规划档位），与
    分镜预检、执行同一份结果：型号声明的全集不含「分辨率↔时长」约束，未收窄的集合交给 LLM 会产出
    执行期必然被拒的时长。事实解析不出（未配置模型、档位缺失 / 无效、收成空集）即抛
    :class:`VideoRequestFactsError`，不回退到任何档位。

    ``default_duration`` 非档位成员时按 None 处理（即回到「auto」档，由模型按内容节奏选）：项目存的
    是用户配置的原样值，收窄后它可能落在集合外，而 ``build_normalize_prompt`` 对非成员 default 是
    fail-loud 的——不归 None 会把「已保存的越界默认时长」变成整个工具的硬失败。
    """
    facts = require_video_request_facts(await storyboard_request_facts(project, config_resolver))
    durations = planning_durations(facts)
    default = _project_default_duration(project)
    return (default if default in durations else None), durations


def _parse_script_plan_json(response_text: str, model: type[BaseModel], *, label: str, top_shape: str) -> dict:
    """解析并校验 script_plan 结构化响应为 dict；校验失败 fail-loud 抛 ValueError，不返回未校验内容。

    ``model`` 取自调用处用 ``supported_durations`` 构造的同一份动态 schema（即 response_schema），
    令本地校验与 response_schema 同口径：即使 backend 未严格执行 schema，超出 supported_durations
    的时长、缺字段也在此被拦截。校验失败抛错而非降级保留原始 JSON——否则未校验内容会被当成正式
    script_plan 文件落盘（下游读取仅守最外层形状、放行），把非法时长 / 缺字段拖到 prompt_authoring 或最终
    save_script 才暴露。与 narration 的 _load_narration_script_plan 严格读取同口径：只有经 schema
    校验的内容才成为持久化的 script_plan 真值源。
    """
    text = strip_json_code_fences(response_text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{label}结构异常：顶层应为对象 {top_shape}")
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as e:
        raise ValueError(f"{label}结构校验失败: {e}") from e


def _parse_normalized_content(response_text: str, model: type[BaseModel]) -> dict:
    """drama script_plan（normalize）响应解析：见 ``_parse_script_plan_json``。"""
    return _parse_script_plan_json(response_text, model, label="script_plan 规范化内容", top_shape="{title, scenes}")


def load_novel_source(project_path: Path, source: str | None, *, episode: int) -> str:
    """读取 script_plan 工具的源文：指定 source 文件，或缺省时本集派生源文；异常情况抛 ValueError。

    normalize / split 两类 script_plan 工具共用：路径越界、文件缺失、内容为空均 fail-fast，
    调用方把消息包装为工具错误信封。

    缺省（``source`` 为 None）解析为 ``source/episode_N.txt``，与 ``WorkflowTarget.source``
    及 ``ArtifactBasis`` 的指纹来源同口径：``source/`` 目录同时存放原文与各集派生文件，按整个
    目录拼接会把别集内容一并送进 prompt，各集因此拿到同一份源文。派生文件缺失时不回落到原文
    ——那同样是「本集之外的内容」，报错指引先跑分集规划工具重建。

    ``source`` 除工具自己产出外，也会被 ``revalidate_reference_script_plan_draft`` 传入草稿的
    ``meta.source``——那是 Agent 可编辑的 JSON 字段，类型标注管不住运行时值。非 str/None 时
    直接抛 ValueError 而非让它落进 ``safe_join``：那里对非路径类型是 ``TypeError``，本函数
    的调用方一律只接 ValueError，放行 TypeError 会在内容确认的读时重算里变成未处理的
    500，而不是「无法重算」这个本该有的降级态。
    """
    if source is not None and not is_str(source):
        raise ValueError(f"meta.source 类型非法，须为字符串或 null：{source!r}")
    if source == "":
        raise ValueError("源文件路径不能为空")
    resolved = episode_source_relpath(episode) if source is None else source
    try:
        source_path = safe_join(project_path, resolved)
    except PathTraversalError as exc:
        raise ValueError(f"路径超出项目目录: {resolved}") from exc
    if not source_path.is_file():
        if source is not None:
            raise ValueError(f"未找到源文件: {source_path}")
        raise ValueError(
            f"未找到本集派生源文: {source_path}；请先运行分集规划工具（plan_episodes）按账本派生该文件，再重试"
        )
    novel_text = source_path.read_text(encoding="utf-8")
    if not novel_text.strip():
        raise ValueError("小说原文为空")
    return novel_text


def load_script_plan_source_with_basis(
    project_path: Path,
    source: str | None,
    project: dict[str, Any],
    episode: int,
    expected_variant: ScriptPlanPromptVariant,
) -> tuple[str, dict[str, object], ArtifactBasis]:
    """Freeze the exact source text and project semantics consumed by a script_plan request."""

    novel_text = load_novel_source(project_path, source, episode=episode)
    prompt_inputs, basis = build_script_plan_request(
        novel_text,
        episode=episode,
        project=project,
        expected_variant=expected_variant,
    )
    return novel_text, prompt_inputs, basis


def uses_reference_video_units(project_data: dict[str, Any]) -> bool:
    """脚本规划草稿仅适用于非广告的参考生视频项目。"""
    return project_data.get("content_mode", "narration") != "ad" and is_reference_video_project(project_data)


class OperationNotAdmittedError(TextGenerationError):
    """AI 操作的结构准入不成立；``reason`` 与制作状态 ``operations`` 里同一操作的理由码一致。"""

    def __init__(self, operation: str, admission: OperationAdmission, message: str) -> None:
        super().__init__(message)
        self.operation = operation
        self.reason = admission.reason


def _refusal_text(reason: AdmissionReason | None, episode: int | None, detail: str = "") -> str:
    where = translate("operation_episode", episode=episode) if episode is not None else translate("operation_project")
    code = reason.value if reason is not None else "refused"
    text = f"⏸️ {translate(f'operation_{code}', where=where)}（{code}）。"
    return f"{text}{detail}" if detail else text


def require_admitted(
    operation: str, admission: OperationAdmission, *, episode: int | None = None, detail: str = ""
) -> None:
    """准入不成立时拒绝：理由码与制作状态同源，文案只作转述；``detail`` 补充入口自己知道的处置方式。"""
    if not admission.admitted:
        raise OperationNotAdmittedError(operation, admission, _refusal_text(admission.reason, episode, detail))


def _prompt_authoring_draft_action(project_path: Path, episode: int) -> str:
    """提示词编写草稿在场时的处置方式：按草稿是否可读、是否带违约定位分别给出。"""
    path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    draft = read_quarantine(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    if draft is None:
        action = translate("operation_draft_repair_envelope", tool=PROMOTE_TOOL_NAME)
    elif draft.violations:
        action = translate("operation_draft_repair_violations", tool=PROMOTE_TOOL_NAME)
    else:
        action = translate("operation_draft_finish", tool=PROMOTE_TOOL_NAME)
    return translate("operation_draft_action", path=path.relative_to(project_path).as_posix(), action=action)


def _ad_overwrite_error(
    episode_label: str, overwrite: script_review.FormalScriptOverwrite
) -> ScriptOverwriteRequiredError:
    rendered = script_review.overwrite_with_text(overwrite.to_dict(), translate)
    assert rendered is not None
    return ScriptOverwriteRequiredError(
        f"⚠️ {episode_label} 的整份重做会替换已有的正式脚本，需要用户确认覆盖，本次未调用文本模型、未写入。\n"
        f"{rendered['text']}\n"
        "须先把上面的丢失清单原文转述给用户，得到明确同意后，再以 regenerate=true、"
        "overwrite_revision=params.script_overwrite.revision 重新调用；正式脚本在此期间又有变化时会按新清单再次拒绝。",
        rendered,
    )


def _regenerate_preflight(
    project_path: Path, episode: int, *, content_mode: str | None, formal_present: bool, overwrite_revision: str | None
) -> None:
    """整份重做的准入与覆盖确认：只适用于广告/短片，已有正式脚本时须认可它的丢失清单。"""
    if content_mode != "ad":
        raise TextGenerationError(
            "❌ regenerate 只适用于广告/短片；其他创作类型要整集重做，请重跑脚本规划并重新完成内容确认"
        )
    projects = ProjectManager.for_project_dir(project_path)
    project = projects.load_project(project_path.name)
    admission = admit_ad_script(
        content_mode, formal_script=formal_present, ad_inputs=ad_inputs_present(project), regenerate=True
    )
    require_admitted("generate_script", admission, episode=episode)
    overwrite = script_review.formal_script_overwrite(project_path, project, episode)
    if overwrite is not None and script_review.fingerprint_of_revision(overwrite_revision) != overwrite.fingerprint:
        raise _ad_overwrite_error(describe_episode_for_agent(project, episode), overwrite)


def prompt_authoring_preflight(
    project_path: Path,
    episode: int,
    *,
    entry_ids: Sequence[str] = (),
    rewrite: bool = False,
    overwrite_revision: str | None = None,
    regenerate: bool = False,
) -> None:
    """复用制作状态的正式脚本、待编写条目与草稿事实，再调用同一份准入谓词。"""
    projects = ProjectManager.for_project_dir(project_path)
    status = WorkflowStateService(projects).get_status(project_path.name, episode)
    if status.blockers:
        raise TextGenerationError("; ".join(blocker.reason for blocker in status.blockers))
    content = status.content
    content_mode = status.project.content_mode
    formal_present = (
        status.target is not None
        and status.target.episode == episode
        and content is not None
        and content.formal_script == "present"
    )
    if regenerate:
        _regenerate_preflight(
            project_path,
            episode,
            content_mode=content_mode,
            formal_present=formal_present,
            overwrite_revision=overwrite_revision,
        )
        return
    if content_mode == "ad" and not formal_present:
        operation = status.operations.get("generate_script")
        if operation is None:
            raise TextGenerationError(translate("operation_admission_unavailable", episode=episode))
        require_admitted("generate_script", OperationAdmission(**operation.model_dump()), episode=episode)
        return
    admission = admit_author_prompts(
        formal_script=formal_present,
        pending_ids=content.pending_authoring_ids if content is not None else [],
        draft_pending=content is not None
        and any(draft.kind == QUARANTINE_KIND_PROMPT_AUTHORING for draft in content.drafts),
        explicit_ids=entry_ids,
    )
    detail = ""
    if admission.reason is AdmissionReason.PROMPT_AUTHORING_DRAFT_PENDING:
        detail = _prompt_authoring_draft_action(project_path, episode)
    elif admission.reason is AdmissionReason.NO_PENDING_AUTHORING:
        redo = translate("operation_redo_ad" if content_mode == "ad" else "operation_redo_episode")
        detail = f"{translate('operation_rewrite_hint')}{redo}"
    require_admitted("author_prompts", admission, episode=episode, detail=detail)
    if rewrite:
        try:
            ScriptGenerator(project_path).prompt_overwrite(
                episode, entry_ids=entry_ids, rewrite=rewrite, overwrite_revision=overwrite_revision
            )
        except PromptAuthoringTargetError as exc:
            raise TextGenerationError(f"❌ 编写范围无效: {exc}") from exc
        except PromptOverwriteRequired as exc:
            raise _prompt_overwrite_error(
                describe_episode_for_agent(projects.load_project(project_path.name), episode), exc
            ) from exc


def script_plan_preflight(project_path: Path, project: Mapping[str, Any], episode: int, source: str | None) -> None:
    """AI 规划脚本的准入：本集有集原文（按账本记录的来源判定）。显式指定源文件时以该文件为输入，由生成时校验。"""
    has_source = source is not None or episode_source_present(project_path, episode, episode_entry(project, episode))
    require_admitted(
        "prepare_script_plan",
        admit_script_plan(project.get("content_mode", "narration"), episode_source=has_source),
        episode=episode,
        detail=translate("operation_episode_source_location", path=episode_source_relpath(episode)),
    )


def _load_project_context(projects: ProjectManager, project_name: str) -> tuple[Path, dict[str, Any]]:
    return projects.get_project_path(project_name), projects.load_project(project_name)


def _formal_script_exists(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    return (project_path / "scripts" / script_review.formal_script_filename(project_path, project, episode)).exists()


async def generate_episode_script(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path = await asyncio.to_thread(projects.get_project_path, project_name)
    await asyncio.to_thread(
        prompt_authoring_preflight,
        project_path,
        episode,
        entry_ids=request.entry_ids,
        rewrite=request.rewrite,
        overwrite_revision=request.overwrite_revision,
        regenerate=request.regenerate,
    )

    try:
        if request.dry_run:
            generator = await asyncio.to_thread(
                ScriptGenerator,
                project_path,
                config_resolver=config_resolver,
            )
            prompt = await generator.build_prompt(
                episode, instructions=instructions, entry_ids=request.entry_ids, rewrite=request.rewrite
            )
            return TextGenerationResult(f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}")

        generator = await ScriptGenerator.create(
            project_path,
            config_resolver=config_resolver,
        )
        formal_existed = await asyncio.to_thread(_formal_script_exists, project_path, generator.project_json, episode)
        rewritten: list[str] = []
        skipped: list[str] = []
        registered: list[dict[str, str]] = []
        result_path = await generator.generate(
            episode=episode,
            instructions=instructions,
            entry_ids=request.entry_ids,
            rewrite=request.rewrite,
            overwrite_revision=request.overwrite_revision,
            rewritten_entry_ids=rewritten,
            skipped_entry_ids=skipped,
            regenerate=request.regenerate,
            registered_assets=registered,
        )
    except AdScriptRejected as exc:
        raise AdScriptRejectedError(
            f"❌ {await _describe(projects, project_name, episode)} 的 AI 生成脚本不合规，本次未写入正式脚本、未登记资产：\n"
            + "\n".join(f"   - {problem}" for problem in exc.problems)
            + "\n   可以带上针对这些问题的附加指令重新生成。",
            exc.problems,
        ) from exc
    except AdScriptOverwriteRequired as exc:
        raise _ad_overwrite_error(await _describe(projects, project_name, episode), exc.overwrite) from exc
    except PromptOverwriteRequired as exc:
        raise _prompt_overwrite_error(await _describe(projects, project_name, episode), exc) from exc
    except PromptAuthoringUnitRejected as exc:
        raise PromptAuthoringInputError(str(exc), code=exc.code, params=exc.params) from exc
    except PromptAuthoringTargetError as exc:
        raise TextGenerationError(f"❌ 编写范围无效: {exc}") from exc
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except FileNotFoundError as exc:
        raise TextGenerationError(f"❌ 文件错误: {exc}") from exc
    skipped_note = (
        f"\n   视觉层已齐、补缺未改动的条目: {'、'.join(skipped)}（要覆盖请传 rewrite=true）" if skipped else ""
    )
    if not rewritten and formal_existed and not request.regenerate:
        redo = (
            "要整份重做请传 regenerate=true" if generator.content_mode == "ad" else "要整集重做请重跑脚本规划并重新确认"
        )
        return TextGenerationResult(
            f"✅ {describe_episode_for_agent(generator.project_json, episode)} 没有待编写的条目，"
            f"未调用文本模型，正式脚本未改动: {result_path.relative_to(project_path).as_posix()}{skipped_note}\n"
            f"   要覆盖已有视觉层请用 entry_ids 点名并传 rewrite=true；{redo}。",
            no_pending_authoring=True,
        )
    rewritten_note = "、".join(rewritten) if rewritten else "整份生成"
    summary = (
        f"✅ 剧本生成完成: {result_path.relative_to(project_path).as_posix()}\n"
        f"   本次编写条目: {rewritten_note}{skipped_note}"
    )
    if registered:
        labels = {asset_type: spec.label_zh for asset_type, spec in ASSET_SPECS.items()}
        summary += "\n   本次登记的新资产（待生成）: " + "、".join(
            f"{labels.get(asset['type'], asset['type'])}「{asset['name']}」" for asset in registered
        )
    warnings = await asyncio.to_thread(_rewritten_mention_warnings, projects, project_name, result_path, rewritten)
    summary += _unbound_mentions_note(warnings)
    return TextGenerationResult(summary, warnings, new_assets=registered)


def _rewritten_mention_warnings(
    projects: ProjectManager,
    project_name: str,
    result_path: Path,
    rewritten: Sequence[str],
) -> list[dict[str, Any]]:
    """本次重写条目的画面描述里没绑定参考图的 ``@[名称]``；剧本读不回来时不阻断回执。"""
    if not rewritten:
        return []
    try:
        project = projects.load_project(project_name)
        script = projects.load_script_readonly(project_name, result_path.name)
    except (OSError, ValueError):
        return []
    return storyboard_mention_warnings(project, script, unit_ids=rewritten)


def _unbound_mentions_note(warnings: Sequence[Mapping[str, Any]]) -> str:
    return "".join(f"\n   ⚠️ {line}" for line in render_storyboard_mention_warnings(warnings, translate))


async def _describe(projects: ProjectManager, project_name: str, episode: int) -> str:
    return describe_episode_for_agent(await asyncio.to_thread(projects.load_project, project_name), episode)


async def confirm_script_review(
    episode: int,
    *,
    overwrite_revision: str | None = None,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    from server.services.project.script_review import ScriptReviewError, ScriptReviewService

    try:
        state = await ScriptReviewService(projects, config_resolver=config_resolver).confirm(
            project_name, episode, overwrite_revision=overwrite_revision
        )
    except ScriptReviewError as exc:
        if exc.code == "overwrite_required":
            overwrite = script_review.overwrite_with_text(exc.overwrite, translate)
            loss_text = overwrite["text"] if overwrite is not None else ""
            raise ScriptOverwriteRequiredError(
                f"⚠️ {await _describe(projects, project_name, episode)} 需要用户确认覆盖，本次未写入。\n{loss_text}\n"
                "须先把上面的丢失清单原文转述给用户，得到明确同意后，再以 "
                "overwrite_revision=params.script_overwrite.revision 重新确认；"
                "正式脚本在此期间又有变化时会按新清单再次拒绝。",
                overwrite,
            ) from exc
        if exc.problem is not None:
            raise VideoRequestFactsUnavailableError(exc.problem) from exc
        raise TextGenerationError(f"❌ 无法完成 script_plan 内容确认（{exc.code}）：{exc.message or exc.code}") from exc
    return TextGenerationResult(
        f"✅ {await _describe(projects, project_name, episode)} 的 script_plan 已确认并整份转为正式脚本，全部分镜待编写，"
        f"prompt_authoring 视觉生成已放行（status={state['status']}）"
    )


async def generate_drama_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path, project = await asyncio.to_thread(_load_project_context, projects, project_name)
    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            load_script_plan_source_with_basis,
            project_path,
            request.source,
            project,
            episode,
            "drama",
        )
    except ValueError as exc:
        raise TextGenerationError(f"❌ {exc}") from exc

    try:
        default_duration, supported_durations = await fetch_storyboard_durations(
            project, config_resolver=config_resolver
        )
        prompt = build_normalize_prompt(
            novel_text=novel_text,
            project_overview=cast(dict[str, Any], prompt_inputs["project_overview"]),
            style=cast(str, prompt_inputs["style"]),
            characters=cast(dict[str, Any], prompt_inputs["characters"]),
            scenes=cast(dict[str, Any], prompt_inputs["scenes"]),
            props=cast(dict[str, Any], prompt_inputs["props"]),
            default_duration=default_duration,
            supported_durations=supported_durations,
            episode=episode,
            source_kind=cast(str, prompt_inputs["source_kind"]),
            episode_outline=cast(dict[str, Any] | None, prompt_inputs["episode_outline"]),
            next_episode_outline=cast(dict[str, Any] | None, prompt_inputs["next_episode_outline"]),
            target_language=cast(str, prompt_inputs["target_language"]),
            source_language=cast(str | None, prompt_inputs["source_language"]),
            speech_rate_override=cast(float | None, prompt_inputs["speech_rate_override"]),
            episode_target_duration=cast(int | None, prompt_inputs["episode_target_duration"]),
            instructions=instructions,
        )

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
        draft_locks = await asyncio.to_thread(ProjectManager.for_project_dir, project_path)
        async with draft_locks.async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                script_plan_path,
            )
        schema = build_drama_script_plan_model(supported_durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_name,
        )
        content = await asyncio.to_thread(_parse_normalized_content, result.text, schema)
        raw_scenes = content["scenes"]
        await reissue_plan_item_ids(projects, project_name, episode, content)
        content = with_new_assets(content, content[NEW_ASSETS_FIELD])

        validation = await asyncio.to_thread(
            validate_script_plan,
            "drama",
            content,
            project=project,
            project_path=project_path,
            episode=episode,
            source=EpisodePlanSource(novel_text, request.source),
            capabilities=supported_durations,
        )
        violations: list[DraftViolation] = []
        for issue in validation.blocking_issues:
            if issue.reason is not None and issue.item_index is not None:
                raw_scenes[issue.item_index]["needs_replan"] = True
            elif issue.code != "source_text_not_verbatim":
                violations.append(issue)
        if violations:
            async with draft_locks.async_file_lock(draft_path):
                report = await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _quarantine_invalid_script_plan_generation,
                    project_path,
                    episode,
                    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                    content,
                    violations,
                    request.source,
                    formal_baseline,
                )
            raise DraftQuarantinedError(report, len(violations))

        async with draft_locks.async_file_lock(draft_path):
            try:
                await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    commit_single_script_plan,
                    project_path,
                    episode,
                    script_plan_path,
                    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                    content,
                    formal_baseline,
                    script_plan_basis,
                )
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(
                    await run_sync_transaction(
                        _quarantine_formal_generation_conflict,
                        project_path,
                        episode,
                        QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                        content,
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc

        return TextGenerationResult(
            drama_script_plan_result_text(script_plan_path.relative_to(project_path), raw_scenes, action="生成")
        )
    except (TextGenerationError, TextOutputTruncatedError, ModelNotInCatalogError, NoAvailableProviderError):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}") from exc


async def fetch_reference_split_caps(
    project: dict[str, Any],
    *,
    config_resolver: ConfigResolver | None = None,
) -> ReferenceSplitCaps:
    """解析 rv 拆分所需的视频能力（见 ``ReferenceSplitCaps``），读 r2v 与 i2v 两桶的视频请求事实。

    unit 是一次生成调用的单元，拆分阶段定的时长就是真正发给供应商的那个值，故档位取**经时长
    联动约束收窄后**的集合（时长由端点固定的桶借规划档位）：不收窄的话（海螺 1080p 只接受 6 秒）
    script_plan 会按全集拆出超标的 unit，prompt_authoring 的枚举 schema 再把它判非法。

    收窄逐 unit 分两套：「参考图↔时长」约束只对真的带参考图的请求生效，整集一律按带图收窄会把无
    引用 unit 本可申请的短档也收掉。schema 枚举与 prompt 候选取两套的并集——落在任一套内的时长都
    可能合法，具体归属由该 unit 正文提及且此刻可用的参考图决定，在正文解析之后逐 unit 判（见
    ``collect_reference_flat_violations``）。``max_duration`` 随之是并集的最大值。

    r2v 桶（本路线的主桶）解析不出即抛 :class:`VideoRequestFactsError`，不回退到任何档位；i2v 桶
    解析不出时无图档位为空、失败原样带在 ``text_problem`` 里，落 i2v 的 unit 逐条报违约。
    ``default_duration`` 非并集成员（用户配置漂移）按 None 处理，避免 prompt 自相矛盾。
    """
    request_facts = reference_request_facts_lookup(project, config_resolver)
    with_ref_facts = require_video_request_facts(await request_facts("r2v"))
    without_ref_facts = await request_facts("i2v")
    with_refs = planning_durations(with_ref_facts)
    without_refs = planning_durations(without_ref_facts) if isinstance(without_ref_facts, VideoRequestFacts) else []
    unit_durations = sorted(set(with_refs) | set(without_refs))
    default = _project_default_duration(project)
    return ReferenceSplitCaps(
        default_duration=default if default in unit_durations else None,
        durations=unit_durations,
        reference_durations=sorted(set(with_refs)),
        text_durations=sorted(set(without_refs)),
        text_problem=without_ref_facts if isinstance(without_ref_facts, VideoRequestFactsFailure) else None,
        max_duration=max(unit_durations),
        max_refs=with_ref_facts.max_reference_images,
        voice=VoiceRenderSettings.from_request_facts(with_ref_facts),
        text_voice=(
            VoiceRenderSettings.from_request_facts(without_ref_facts)
            if isinstance(without_ref_facts, VideoRequestFacts)
            else None
        ),
    )


def build_reference_units_from_flat(
    flat_units: list[dict[str, Any]],
    *,
    unit_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Attach service-issued IDs to validated model output before persistence."""
    return [
        {
            "unit_id": unit_id,
            "text": unit["text"],
            "duration_seconds": unit["duration_seconds"],
            "source_text": unit["source_text"],
        }
        for unit, unit_id in zip(flat_units, unit_ids, strict=True)
    ]


def render_soft_violation_lines(soft_violations: list[SoftViolation]) -> list[str]:
    """把软违约渲染成回给 Agent 的文本行。

    声音降级跨 unit 去重、保持首现顺序：同一个角色没配参考音频，在每个 unit 各报一遍只是噪音。
    未引用场景不去重、带 unit 定位：每个未引用场景的 unit 都要各自被指名，合并成一条 Agent 无从
    定位要改哪几个。
    """
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for soft in soft_violations:
        rendered = translate(soft.code, **soft.params)
        if soft.code == WARN_UNIT_WITHOUT_SCENE:
            lines.append(f"unit {soft.item_id}：{rendered}")
            continue
        if (soft.code, rendered) in seen:
            continue
        seen.add((soft.code, rendered))
        lines.append(rendered)
    return lines


def soft_violation_entries(soft_violations: list[SoftViolation]) -> list[dict[str, Any]]:
    """软违约 → 下发给 Web 的结构化条目；``message`` 由呈现边界按请求语言以 ``code`` + ``params`` 成文。"""
    return [
        {"code": soft.code, "params": soft.params, "item_index": soft.item_index, "item_id": soft.item_id}
        for soft in soft_violations
    ]


SOFT_VIOLATION_NOTE_QUARANTINED = "非违约、无需为此重改，草稿仍在场"
SOFT_VIOLATION_NOTE_COMMITTED = "产物已落盘"


def render_soft_violation_section(soft_violations: list[str], *, note: str) -> str:
    """把软违约文本行渲染成可拼接的「降级提示」段（无软违约时为空串）。

    ``note`` 交代该段在当前语境下的处置：成功回执里产物已落盘，违约报告里草稿仍在场。段本身
    的标题与条目形状由本函数独占，报告与回执因此不会在措辞上分叉，Agent 也能按同一形状识别。
    """
    if not soft_violations:
        return ""
    return f"\n⚠️ 降级提示（不阻断，{note}）:\n" + "\n".join(f"- {line}" for line in soft_violations)


def drama_script_plan_result_text(script_plan_path: Path, scenes: list[dict], *, action: str) -> str:
    """drama script_plan 的落盘回执。``action`` 是「生成」/「晋升」——两条路的统计段同一出口，
    只在动作词上分叉，Agent 因此按同一种格式读产出与晋升两次结果。
    """
    return f"✅ 规范化剧本{action}（结构化内容）已保存: {script_plan_path}\n📊 生成统计: {len(scenes)} 个分镜"


def narration_script_plan_result_text(script_plan_path: Path, segments: list[dict], *, action: str) -> str:
    """narration script_plan 的落盘回执；``action`` 取「拆分」/「晋升」，理由同 drama 侧。"""
    total_chars = sum(len(str(segment.get("novel_text") or "")) for segment in segments)
    total_seconds = sum(int(segment.get("duration_seconds") or 0) for segment in segments)
    break_count = sum(1 for segment in segments if segment.get("segment_break"))
    return (
        f"✅ 旁白/解说分镜{action}（结构化 script_plan）已保存: {script_plan_path}\n"
        f"📊 生成统计: {len(segments)} 个分镜 / {total_chars} 字，"
        f"预计总时长 {total_seconds} 秒；segment_break 标记 {break_count} 个"
    )


def reference_result_text(script_plan_path: Path, units: list[dict], soft_violations: list[str], *, action: str) -> str:
    """晋升 / 拆分成功后回给 Agent 的摘要：落盘统计 + 软违约段。

    ``action`` 点明这份正式 script_plan 是重新拆分还是草稿晋升来的：两条路都写同一个文件，摘要不分
    的话，Agent 修完草稿会收到一句「拆分已保存」，读起来像它的修改被一次重抽覆盖了。

    软违约不阻断落盘，但必须随产物呈现——「角色没配参考音频」「本单元没引用场景」这类降级只在
    生成后才听得出来、看得出来，不在产出当时说，Agent 与用户都不会知道声音一致性或画面地点已经
    打了折。段的渲染走 ``render_soft_violation_section``，与晋升被违约挡下时的报告同一出口。
    """
    total_seconds = sum(int(u.get("duration_seconds") or 0) for u in units)
    max_unit_refs = max(len(extract_mentions(str(u.get("text") or ""))) for u in units)
    text = (
        f"✅ 视频单元{action}（结构化 script_plan）已保存: {script_plan_path}\n"
        f"📊 生成统计: {len(units)} 个 unit，总时长 {total_seconds} 秒；"
        f"单 unit `@` 提及最多 {max_unit_refs} 个"
    )
    return text + render_soft_violation_section(soft_violations, note=SOFT_VIOLATION_NOTE_COMMITTED)


def narration_script_plan_path(project_path: Path, episode: int) -> Path:
    """该集正式 narration script_plan 的路径（``drafts/episode_N/script_plan_segments.json``）。"""
    return episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["narration"]


async def generate_reference_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
    before_commit: Callable[[], None] | None = None,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path, project = await asyncio.to_thread(_load_project_context, projects, project_name)

    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            load_script_plan_source_with_basis,
            project_path,
            request.source,
            project,
            episode,
            "reference_video",
        )
    except ValueError as exc:
        raise TextGenerationError(f"❌ {exc}") from exc

    try:
        characters = cast(dict[str, Any], prompt_inputs["characters"])
        scenes = cast(dict[str, Any], prompt_inputs["scenes"])
        props = cast(dict[str, Any], prompt_inputs["props"])
        split_caps = await fetch_reference_split_caps(project, config_resolver=config_resolver)
        prompt = build_reference_units_split_prompt(
            novel_text=novel_text,
            project_overview=cast(dict[str, Any], prompt_inputs["project_overview"]),
            characters=characters,
            scenes=scenes,
            props=props,
            supported_durations=split_caps.durations,
            reference_supported_durations=split_caps.reference_durations,
            text_supported_durations=split_caps.text_durations,
            max_duration=split_caps.max_duration,
            max_reference_images=split_caps.max_refs,
            reference_max_speakers=split_caps.voice.planning_speaker_limit,
            text_max_speakers=split_caps.text_voice.planning_speaker_limit
            if split_caps.text_voice is not None
            else None,
            default_duration=split_caps.default_duration,
            episode=episode,
            source_kind=cast(str | None, prompt_inputs["source_kind"]) or DEFAULT_SOURCE_KIND,
            target_language=cast(str, prompt_inputs["target_language"]),
            source_language=cast(str | None, prompt_inputs["source_language"]),
            speech_rate_override=cast(float | None, prompt_inputs["speech_rate_override"]),
            episode_target_duration=cast(int | None, prompt_inputs["episode_target_duration"]),
            episode_outline=cast(dict[str, Any] | None, prompt_inputs["episode_outline"]),
            next_episode_outline=cast(dict[str, Any] | None, prompt_inputs["next_episode_outline"]),
            instructions=instructions,
        )

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
        formal_script_plan_path = script_review.official_reference_script_plan_path(project_path, episode)
        draft_locks = await asyncio.to_thread(ProjectManager.for_project_dir, project_path)
        async with draft_locks.async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                formal_script_plan_path,
            )
        schema = build_reference_units_script_plan_model(split_caps.durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_name,
        )
        flat = await asyncio.to_thread(
            _parse_script_plan_json, result.text, schema, label="script_plan 拆分内容", top_shape="{units}"
        )
        flat_units = flat["units"]
        new_assets = dedupe_new_assets(flat[NEW_ASSETS_FIELD])

        validation = await asyncio.to_thread(
            validate_script_plan,
            "reference_video",
            with_new_assets({"units": flat_units}, new_assets),
            project=project,
            project_path=project_path,
            episode=episode,
            source=EpisodePlanSource(novel_text, request.source),
            capabilities=split_caps,
        )
        violations = validation.blocking_issues
        if violations:
            soft_violations = render_soft_violation_lines(validation.soft_violations)
            async with draft_locks.async_file_lock(draft_path):
                report = await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _quarantine_invalid_script_plan_generation,
                    project_path,
                    episode,
                    QUARANTINE_KIND_SCRIPT_PLAN,
                    with_new_assets({"units": flat_units}, new_assets),
                    violations,
                    request.source,
                    formal_baseline,
                )
            raise DraftQuarantinedError(
                report + render_soft_violation_section(soft_violations, note=SOFT_VIOLATION_NOTE_QUARANTINED),
                len(violations),
            )

        unit_ids = await run_sync_transaction(
            projects.reserve_item_ids, project_name, episode, "video_units", len(flat_units)
        )
        raw_units = build_reference_units_from_flat(flat_units, unit_ids=unit_ids)
        soft_violations = render_soft_violation_lines(
            [replace(issue, item_id=unit_ids[issue.item_index]) for issue in validation.soft_violations]
        )
        async with draft_locks.async_file_lock(draft_path):
            try:
                await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _commit_generated_reference_script_plan,
                    project_path,
                    episode,
                    with_new_assets({"units": raw_units}, new_assets),
                    formal_baseline,
                    script_plan_basis,
                    before_commit,
                )
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(
                    await run_sync_transaction(
                        _quarantine_formal_generation_conflict,
                        project_path,
                        episode,
                        QUARANTINE_KIND_SCRIPT_PLAN,
                        with_new_assets({"units": flat_units}, new_assets),
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc
        return TextGenerationResult(
            reference_result_text(
                script_review.official_reference_script_plan_path(project_path, episode).relative_to(project_path),
                raw_units,
                soft_violations,
                action="拆分",
            )
        )
    except (TextGenerationError, TextOutputTruncatedError, ModelNotInCatalogError, NoAvailableProviderError):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}") from exc


async def generate_narration_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path, project = await asyncio.to_thread(_load_project_context, projects, project_name)

    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            load_script_plan_source_with_basis,
            project_path,
            request.source,
            project,
            episode,
            "narration",
        )
    except ValueError as exc:
        raise TextGenerationError(f"❌ {exc}") from exc

    try:
        characters = cast(dict[str, Any], prompt_inputs["characters"])
        scenes = cast(dict[str, Any], prompt_inputs["scenes"])
        props = cast(dict[str, Any], prompt_inputs["props"])
        default_duration, supported_durations = await fetch_storyboard_durations(
            project, config_resolver=config_resolver
        )
        prompt = build_narration_split_prompt(
            novel_text=novel_text,
            project_overview=cast(dict[str, Any], prompt_inputs["project_overview"]),
            characters=characters,
            scenes=scenes,
            props=props,
            default_duration=default_duration,
            supported_durations=supported_durations,
            episode=episode,
            target_language=cast(str, prompt_inputs["target_language"]),
            episode_target_duration=cast(int | None, prompt_inputs["episode_target_duration"]),
            instructions=instructions,
        )

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        script_plan_path = narration_script_plan_path(project_path, episode)
        draft_locks = await asyncio.to_thread(ProjectManager.for_project_dir, project_path)
        async with draft_locks.async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                script_plan_path,
            )
        schema = build_narration_script_plan_model(supported_durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_name,
        )
        content = await asyncio.to_thread(
            _parse_script_plan_json,
            result.text,
            schema,
            label="script_plan 拆分内容",
            top_shape="{segments}",
        )
        raw_segments = content["segments"]
        await reissue_plan_item_ids(projects, project_name, episode, content)
        content = with_new_assets(content, content[NEW_ASSETS_FIELD])

        validation = await asyncio.to_thread(
            validate_script_plan,
            "narration",
            content,
            project=project,
            project_path=project_path,
            episode=episode,
            source=EpisodePlanSource(novel_text, request.source),
            capabilities=supported_durations,
        )
        violations = validation.blocking_issues
        if violations:
            async with draft_locks.async_file_lock(draft_path):
                report = await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _quarantine_invalid_script_plan_generation,
                    project_path,
                    episode,
                    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                    content,
                    violations,
                    request.source,
                    formal_baseline,
                )
            raise DraftQuarantinedError(report, len(violations))

        async with draft_locks.async_file_lock(draft_path):
            try:
                await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    commit_single_script_plan,
                    project_path,
                    episode,
                    script_plan_path,
                    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                    content,
                    formal_baseline,
                    script_plan_basis,
                )
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(
                    await run_sync_transaction(
                        _quarantine_formal_generation_conflict,
                        project_path,
                        episode,
                        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                        content,
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc

        return TextGenerationResult(
            narration_script_plan_result_text(script_plan_path.relative_to(project_path), raw_segments, action="拆分")
        )
    except (TextGenerationError, TextOutputTruncatedError, ModelNotInCatalogError, NoAvailableProviderError):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}") from exc
