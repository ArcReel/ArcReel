"""Host-independent implementations for ArcReel text-generation tools."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
)
from lib.artifacts.artifact_provenance import ScriptPlanPromptVariant, build_script_plan_request
from lib.backends.http_status_errors import TextWaitTimeout
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import TextGenerationRequest as BackendTextGenerationRequest
from lib.backends.text_backends.base import TextOutputTruncatedError, TextTaskType
from lib.backends.text_generator import TextGenerator
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ConfigResolver, NoAvailableProviderError
from lib.episode.episode_ids import describe_episode_for_agent
from lib.episode.episode_paths import (
    SCRIPT_PLAN_FILENAMES,
    episode_drafts_dir,
    episode_source_relpath,
)
from lib.episode.source_kinds import DEFAULT_SOURCE_KIND
from lib.generation.video_request_facts import (
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    plan_capability_inputs,
    planning_durations,
    require_video_request_facts,
)
from lib.i18n import _ as translate
from lib.infra.async_thread import run_noninterruptible_async, run_noninterruptible_sync
from lib.infra.json_io import load_json_or_none
from lib.infra.schema_guards import is_int, is_str
from lib.infra.text_utils import strip_json_code_fences
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager, find_episode, is_reference_video_project
from lib.prompts.prompt_builders_reference import build_reference_units_split_prompt
from lib.prompts.prompt_builders_script import build_narration_split_prompt, build_normalize_prompt
from lib.script import script_review
from lib.script.draft_violation import DraftViolation, violation_entries
from lib.script.item_ids import ItemIdIssuer, max_item_number, raise_item_id_high_water
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
from lib.script.script_plan_entries import ScriptPlanKind, entry_id_field, plan_variant
from lib.script.script_plan_validation import (
    ReferenceSplitCaps,
    ScriptPlanValidation,
    SoftViolation,
    read_episode_plan_source,
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
    uses_reference_unit_authoring,
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
    blocking_issues: list[dict[str, Any]] = dataclass_field(default_factory=list)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceAuthoringResult(TextGenerationResult):
    outcome: Literal["complete", "partial"]
    written_ids: list[str]
    failed_units: list[dict[str, str]]


class TextGenerationError(Exception):
    """Expected refusal from a text-generation handler."""


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


_PRESERVED_PLAN = "原规划已保留。待确认时可继续编辑，也可调整指令后重新生成。"


def _generation_issues(issues: list[DraftViolation], baseline: str | None) -> str:
    if not issues:
        return ""
    report = "\n确认阻断项：\n" + "\n".join(f"- [{issue.code}] {issue}" for issue in issues)
    if baseline is not None:
        raise TextGenerationError(f"{_PRESERVED_PLAN}{report}")
    return report


PlanCapabilities = list[int] | ReferenceSplitCaps | VideoRequestFactsFailure | None


async def resolve_plan_capabilities(project: dict[str, Any], resolver: ConfigResolver | None) -> PlanCapabilities:
    try:
        if script_review.script_plan_kind(project) == "reference_video":
            return await fetch_reference_split_caps(project, config_resolver=resolver)
        if script_review.script_plan_kind(project) is not None:
            return (await fetch_storyboard_durations(project, config_resolver=resolver))[1]
    except VideoRequestFactsError as exc:
        return exc.failure
    return None


def _retain_ids_for_identical_plan(path: Path, content: dict[str, Any], kind: ScriptPlanKind) -> bool:
    key = plan_variant(kind).plan_items_key
    previous = load_json_or_none(path)
    id_field = entry_id_field(kind)
    old = previous.get(key) if isinstance(previous, dict) else None
    if (
        isinstance(old, list)
        and len(old) == len(content[key])
        and all(isinstance(item, dict) and isinstance(item.get(id_field), str) for item in old)
    ):
        same_ids = [item | {id_field: prior[id_field]} for item, prior in zip(content[key], old, strict=True)]
        if content | {key: same_ids} == previous:
            content[key][:] = same_ids
            return True
    return False


def _assign_item_ids(items: Sequence[dict[str, Any]], id_field: str, item_ids: Sequence[str]) -> None:
    for item, item_id in zip(items, item_ids, strict=True):
        item[id_field] = item_id


async def _commit_generated_plan(
    projects: ProjectManager,
    project_name: str,
    project_path: Path,
    episode: int,
    kind: ScriptPlanKind,
    content: dict[str, Any],
    project: dict[str, Any],
    resolver: ConfigResolver,
    formal_baseline: str | None,
    basis: ArtifactBasis,
    before_commit: Callable[[], None] | None = None,
) -> tuple[ScriptPlanValidation, str]:
    latest = await run_noninterruptible_sync(projects.load_project, project_name)
    capability_inputs = plan_capability_inputs(latest)
    capabilities = await run_noninterruptible_async(resolve_plan_capabilities(latest, resolver))
    preserved = f"。{_PRESERVED_PLAN}" if formal_baseline is not None else "，请重新生成。"

    def check_capabilities(current: dict[str, Any]) -> None:
        if plan_capability_inputs(current) != capability_inputs or script_review.script_plan_kind(current) != kind:
            raise TextGenerationError(f"视频模型配置已变化{preserved}")

    def commit() -> tuple[ScriptPlanValidation, str]:
        path = script_review.script_plan_path(project_path, project, episode)
        assert path is not None
        pm = ProjectManager.for_project_dir(project_path)
        if before_commit:
            before_commit()
        with script_review.formal_script_plan_lock(project_path, episode, path):
            try:
                script_review.assert_base_fingerprint(path, formal_baseline)
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(f"formal_revision_conflict: {exc}。{_PRESERVED_PLAN}") from exc
            variant = plan_variant(kind)
            key = variant.plan_items_key
            id_field = entry_id_field(kind)
            current = projects.load_project(project_name)
            check_capabilities(current)
            validation_source = read_episode_plan_source(project_path, current, episode)
            if formal_baseline is not None:
                if validation_source.text is None and validation_source.problem is None:
                    raise TextGenerationError(f"本集缺少原文。{_PRESERVED_PLAN}")
                retained = _retain_ids_for_identical_plan(path, content, kind)
            else:
                retained = False

            project_update = None
            if not retained:
                floor = pm.episode_item_id_floor(project_name, current, episode)
                issuer = ItemIdIssuer(
                    episode, variant.skeleton_kind, max(floor, max_item_number(plan_item_ids(path), episode))
                )
                _assign_item_ids(content[key], id_field, [issuer.issue() for _ in content[key]])

                def record_ids(locked_project: dict[str, Any]) -> None:
                    if pm.episode_item_id_floor(project_name, locked_project, episode) != floor:
                        raise TextGenerationError(f"本集的条目编号已变化{preserved}")
                    entry = find_episode(locked_project, episode)
                    if entry is not None:
                        raise_item_id_high_water(entry, issuer.last)

                project_update = record_ids

            validation = validate_script_plan(
                kind,
                content,
                project=current,
                project_path=project_path,
                episode=episode,
                source=validation_source,
                capabilities=capabilities,
            )
            report = _generation_issues(validation.blocking_issues, formal_baseline)
            if kind == "drama":
                for issue in validation.blocking_issues:
                    if issue.reason is not None and issue.item_index is not None:
                        content[key][issue.item_index]["needs_replan"] = True
            try:
                script_review.write_formal_script_plan_locked(
                    project_path,
                    episode,
                    path,
                    content,
                    expected_fingerprint=formal_baseline,
                    basis=basis,
                    generated_from=project,
                    project_check=check_capabilities,
                    project_update=project_update,
                )
            except TextGenerationError:
                raise
            except Exception as exc:
                if formal_baseline is not None:
                    raise UnexpectedTextGenerationError(f"{exc}。{_PRESERVED_PLAN}") from exc
                raise
            return validation, report

    return await run_noninterruptible_sync(commit)


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


def load_script_plan_source_with_basis(
    project_path: Path,
    source: str | None,
    project: dict[str, Any],
    episode: int,
    expected_variant: ScriptPlanPromptVariant,
) -> tuple[str, dict[str, object], ArtifactBasis]:
    """Freeze the exact source text and project semantics consumed by a script_plan request."""

    novel_text = _canonical_plan_source(project_path, project, episode, source)
    prompt_inputs, basis = build_script_plan_request(
        novel_text,
        episode=episode,
        project=project,
        expected_variant=expected_variant,
    )
    return novel_text, prompt_inputs, basis


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
    """复用制作状态的正式脚本、待编写条目事实，再调用同一份准入谓词。"""
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
    reference_authoring = uses_reference_unit_authoring(content_mode, status.project.generation_mode)
    admission = admit_author_prompts(
        formal_script=formal_present,
        pending_ids=content.pending_authoring_ids if content is not None else [],
        explicit_ids=entry_ids,
    )
    if reference_authoring and admission.reason is AdmissionReason.NO_PENDING_AUTHORING:
        return
    detail = ""
    if admission.reason is AdmissionReason.NO_PENDING_AUTHORING:
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
    _canonical_plan_source(project_path, project, episode, source)


def _canonical_plan_source(project_path: Path, project: Mapping[str, Any], episode: int, source: str | None) -> str:
    canonical = episode_source_relpath(episode)
    if source is not None and source != canonical:
        raise TextGenerationError(f"source 仅接受本集规范集原文路径 {canonical}")
    current = read_episode_plan_source(project_path, project, episode)
    require_admitted(
        "prepare_script_plan",
        admit_script_plan(project.get("content_mode", "narration"), episode_source=current.path is not None),
        episode=episode,
        detail=translate("operation_episode_source_location", path=episode_source_relpath(episode)),
    )

    if current.problem:
        raise TextGenerationError(str(current.problem))
    assert current.text is not None
    return current.text


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
        failed_units: list[dict[str, str]] = []
        result_path = await generator.generate(
            episode=episode,
            instructions=instructions,
            entry_ids=request.entry_ids,
            rewrite=request.rewrite,
            overwrite_revision=request.overwrite_revision,
            rewritten_entry_ids=rewritten,
            skipped_entry_ids=skipped,
            failed_units=failed_units,
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
    status = "部分完成" if failed_units else "生成完成"
    summary = (
        f"✅ 剧本{status}: {result_path.relative_to(project_path).as_posix()}\n"
        f"   本次编写条目: {rewritten_note}{skipped_note}"
    )
    if registered:
        labels = {asset_type: spec.label_zh for asset_type, spec in ASSET_SPECS.items()}
        summary += "\n   本次登记的新资产（待生成）: " + "、".join(
            f"{labels.get(asset['type'], asset['type'])}「{asset['name']}」" for asset in registered
        )
    warnings = await asyncio.to_thread(_rewritten_mention_warnings, projects, project_name, result_path, rewritten)
    summary += _unbound_mentions_note(warnings)
    if rewritten and is_reference_video_project(generator.project_json) and generator.content_mode != "ad":
        if failed_units:
            summary += "\n" + "\n".join(
                f"{unit['unit_id']}: {unit['message']} {unit.get('abort_reason', '')}".rstrip() for unit in failed_units
            )
        return ReferenceAuthoringResult(
            summary,
            warnings,
            outcome="partial" if failed_units else "complete",
            written_ids=rewritten,
            failed_units=failed_units,
        )
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
    formal_baseline: str | None = None
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

        script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
        formal_baseline = await asyncio.to_thread(script_review.content_fingerprint, script_plan_path)
        schema = build_drama_script_plan_model(supported_durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
            ),
            project_name=project_name,
        )
        content = await asyncio.to_thread(_parse_normalized_content, result.text, schema)
        raw_scenes = content["scenes"]
        content = with_new_assets(content, content[NEW_ASSETS_FIELD])

        validation, report = await _commit_generated_plan(
            projects,
            project_name,
            project_path,
            episode,
            "drama",
            content,
            project,
            config_resolver,
            formal_baseline,
            script_plan_basis,
        )
        return TextGenerationResult(
            drama_script_plan_result_text(script_plan_path.relative_to(project_path), raw_scenes) + report,
            blocking_issues=violation_entries(validation.blocking_issues),
        )
    except (
        TextGenerationError,
        TextWaitTimeout,
        TextOutputTruncatedError,
        ModelNotInCatalogError,
        NoAvailableProviderError,
    ):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        preserved = f"。{_PRESERVED_PLAN}" if formal_baseline is not None else ""
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}{preserved}") from exc


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


SOFT_VIOLATION_NOTE_COMMITTED = "产物已落盘"


def render_soft_violation_section(soft_violations: list[str], *, note: str) -> str:
    if not soft_violations:
        return ""
    return f"\n⚠️ 降级提示（不阻断，{note}）:\n" + "\n".join(f"- {line}" for line in soft_violations)


def drama_script_plan_result_text(script_plan_path: Path, scenes: list[dict]) -> str:
    return f"✅ 规范化剧本生成（结构化内容）已保存: {script_plan_path}\n📊 生成统计: {len(scenes)} 个分镜"


def narration_script_plan_result_text(script_plan_path: Path, segments: list[dict]) -> str:
    total_chars = sum(len(str(segment.get("novel_text") or "")) for segment in segments)
    total_seconds = sum(int(segment.get("duration_seconds") or 0) for segment in segments)
    break_count = sum(1 for segment in segments if segment.get("segment_break"))
    return (
        f"✅ 旁白/解说分镜拆分（结构化 script_plan）已保存: {script_plan_path}\n"
        f"📊 生成统计: {len(segments)} 个分镜 / {total_chars} 字，"
        f"预计总时长 {total_seconds} 秒；segment_break 标记 {break_count} 个"
    )


def reference_result_text(script_plan_path: Path, units: list[dict], soft_violations: list[str]) -> str:
    total_seconds = sum(int(u.get("duration_seconds") or 0) for u in units)
    max_unit_refs = max(len(extract_mentions(str(u.get("text") or ""))) for u in units)
    text = (
        f"✅ 视频单元拆分（结构化 script_plan）已保存: {script_plan_path}\n"
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

    formal_baseline: str | None = None
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

        formal_script_plan_path = script_review.official_reference_script_plan_path(project_path, episode)
        formal_baseline = await asyncio.to_thread(script_review.content_fingerprint, formal_script_plan_path)
        schema = build_reference_units_script_plan_model(split_caps.durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
            ),
            project_name=project_name,
        )
        flat = await asyncio.to_thread(
            _parse_script_plan_json, result.text, schema, label="script_plan 拆分内容", top_shape="{units}"
        )
        flat_units = flat["units"]
        new_assets = dedupe_new_assets(flat[NEW_ASSETS_FIELD])

        raw_units = build_reference_units_from_flat(flat_units, unit_ids=[""] * len(flat_units))
        content = with_new_assets({"units": raw_units}, new_assets)
        validation, report = await _commit_generated_plan(
            projects,
            project_name,
            project_path,
            episode,
            "reference_video",
            content,
            project,
            config_resolver,
            formal_baseline,
            script_plan_basis,
            before_commit,
        )
        soft_violations = render_soft_violation_lines(validation.soft_violations)
        return TextGenerationResult(
            reference_result_text(
                script_review.official_reference_script_plan_path(project_path, episode).relative_to(project_path),
                raw_units,
                soft_violations,
            )
            + report,
            blocking_issues=violation_entries(validation.blocking_issues),
        )
    except (
        TextGenerationError,
        TextWaitTimeout,
        TextOutputTruncatedError,
        ModelNotInCatalogError,
        NoAvailableProviderError,
    ):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        preserved = f"。{_PRESERVED_PLAN}" if formal_baseline is not None else ""
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}{preserved}") from exc


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

    formal_baseline: str | None = None
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

        script_plan_path = narration_script_plan_path(project_path, episode)
        formal_baseline = await asyncio.to_thread(script_review.content_fingerprint, script_plan_path)
        schema = build_narration_script_plan_model(supported_durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
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
        content = with_new_assets(content, content[NEW_ASSETS_FIELD])

        validation, report = await _commit_generated_plan(
            projects,
            project_name,
            project_path,
            episode,
            "narration",
            content,
            project,
            config_resolver,
            formal_baseline,
            script_plan_basis,
        )
        return TextGenerationResult(
            narration_script_plan_result_text(script_plan_path.relative_to(project_path), raw_segments) + report,
            blocking_issues=violation_entries(validation.blocking_issues),
        )
    except (
        TextGenerationError,
        TextWaitTimeout,
        TextOutputTruncatedError,
        ModelNotInCatalogError,
        NoAvailableProviderError,
    ):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        preserved = f"。{_PRESERVED_PLAN}" if formal_baseline is not None else ""
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}{preserved}") from exc
