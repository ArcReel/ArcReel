"""Authoritative workflow status for ArcReel projects.

状态计算只读，不写盘。
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.artifacts.artifact_activation import ArtifactComparer, ArtifactCurrencyResolver, RegisteredArtifactResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.store import EditTimelineStore
from lib.episode.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    mismatched_source_fingerprints,
    normalize_source_text,
    parse_positive_episode_num,
)
from lib.episode.episode_paths import episode_source_relpath
from lib.episode.episode_replan import replan_candidate
from lib.episode.episode_sources import legacy_cut_episode_ids, unplanned_text_remains, whole_source_files
from lib.infra.content_digest import prefixed_canonical_json_digest
from lib.project.asset_derivatives import derivative_artifact_key, derivative_table, split_derivative_artifact_id
from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.project.data_validator import DataValidator
from lib.project.episode_asset_references import episode_referenced_assets
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.project.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MIGRATION_FAILURE_FILENAME,
    MigrationFailureRecord,
    load_migration_verdict,
)
from lib.project.project_migration_report import MigrationReport, load_migration_report
from lib.project.source_revision import SourceRevisionResult, SourceScope, compute_source_revision
from lib.references.reference_admission import admit_references, admit_storyboard_items
from lib.references.reference_catalog import build_reference_catalog
from lib.script import script_review
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_PROMPT_AUTHORING,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
)
from lib.script.reference_video.text_parser import derive_references_from_text
from lib.script.script_models import get_generated_assets, script_duration_total
from lib.script.script_skeleton import SKELETONS, STORYBOARD_ITEM_ID_PATTERN, ensure_route_skeleton, resolve_kind_items
from lib.speech.narration_config import USE_TTS, project_narration_delivery
from lib.workflow.operation_admission import (
    AdmissionReason,
    AdmissionState,
    OperationAdmission,
    ad_inputs_present,
    admit_ad_script,
    admit_author_prompts,
    admit_edit_timeline,
    admit_plan_episodes,
    admit_script_plan,
    episode_source_present,
    pending_authoring_entry_ids,
    whole_source_present,
)
from lib.workflow.workflow_rules import workflow_rule

logger = logging.getLogger(__name__)


class WorkflowRequestError(ValueError):
    """调用方给出的查询参数本身不合法。

    与之相对的是持久化数据损坏（剧本骨架、content_mode / generation_mode 组合等）：
    那类问题同样以 ``ValueError`` 家族抛出，但责任在服务端数据而非本次请求，消费方
    据此区分「回 400 / invalid_request」与「按服务端故障上报」，不把排障方向指向调用方。
    """


class WorkflowProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_mode: str
    generation_mode: str
    grid_storyboard: bool


class WorkflowTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode: int
    script: str
    script_filename: str
    source: str


class WorkflowBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    reason: str


class WorkflowActionType(StrEnum):
    """``WorkflowNextAction.type`` 的闭集。

    三个来源合成同一份取值：本模块给出的建议下一步与分岔、``lib.workflow.workflow_plan`` 投影时
    额外注入的动作，以及整批准入判定被拒时原样交回的 ``lib.generation.generation_result.GenerationAction``。
    消费方（前端联合类型、profile 受控动作表、动作译文）一律从本枚举派生，新增成员即
    自动进入各处覆盖检查，不必再手抄一份清单。
    """

    # 本模块给出的建议下一步及其分岔
    NONE = "none"
    COLLECT_PROJECT_INPUT = "collect_project_input"
    CREATE_EPISODE = "create_episode"
    DRAFT_SELLING_POINTS = "draft_selling_points"
    PLAN_EPISODES = "plan_episodes"
    RESET_EPISODE_PLANNING = "reset_episode_planning"
    RESOLVE_DRAFT = "resolve_draft"
    PREPARE_SCRIPT_PLAN = "prepare_script_plan"
    START_BLANK_SCRIPT = "start_blank_script"
    PROVIDE_EPISODE_SOURCE = "provide_episode_source"
    CONFIRM_SCRIPT_PLAN = "confirm_script_plan"
    GENERATE_SCRIPT = "generate_script"
    ADD_SCRIPT_ITEMS = "add_script_items"
    AUTHOR_PROMPTS = "author_prompts"
    GENERATE_ASSET_SHEETS = "generate_asset_sheets"
    GENERATE_STORYBOARDS = "generate_storyboards"
    GENERATE_GRID = "generate_grid"
    REPAIR_VIDEO_UNITS = "repair_video_units"
    GENERATE_VIDEOS = "generate_videos"
    CREATE_EDIT_TIMELINE = "create_edit_timeline"

    # 数据升级失败的项目在任何阶段都只报这一个动作
    RETRY_PROJECT_MIGRATION = "retry_project_migration"

    # ``build_workflow_plan`` 投影时注入的动作
    PATCH_EPISODE_SCRIPT = "patch_episode_script"

    # ``GenerationAction`` 闭集；整批准入判定与任务失败把它原样交回成 next_action
    RETRY = "retry"
    FIX_INPUT = "fix_input"
    GENERATE_DEPENDENCY = "generate_dependency"
    WAIT_FOR_TASK = "wait_for_task"
    REPLAN_UNIT = "replan_unit"
    CONFIRM_REQUEST_DURATION = "confirm_request_duration"
    CONFIGURE_PROVIDER = "configure_provider"
    REPAIR_ARTIFACT_STATE = "repair_artifact_state"
    RETRY_ARTIFACT_DOWNLOAD = "retry_artifact_download"


class WorkflowNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WorkflowActionType
    args: dict[str, Any] = Field(default_factory=dict)
    requested_ids: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    reason: str


class WorkflowOperation(BaseModel):
    """一个 AI 操作此刻的结构准入：与操作入口调用同一份谓词（见 ``lib.workflow.operation_admission``）。"""

    model_config = ConfigDict(extra="forbid")

    state: AdmissionState
    reason: AdmissionReason | None = None


class WorkflowDraft(BaseModel):
    """目标集上在场的一份草稿。``needs_repair`` 区分待修复草稿与 Agent 的可编辑草稿。"""

    model_config = ConfigDict(extra="forbid")

    kind: str
    path: str
    needs_repair: bool


class WorkflowContent(BaseModel):
    """内容现状：各类内容此刻是什么样，不含「该做哪一步」的判断。

    集级字段只在有目标集时有值。``episode_plan_stale`` 表示该集的集规划状态为 stale、
    脚本规划尚待重建：它只在现状里陈述，不进建议的下一步。

    ``episode_complete`` 是目标集已完成（判定见 ``is_episode_complete``）；完成的集仍可能有集内建议的
    下一步，例如待编写条目或缺资产图。``project_complete`` 只在不指定集的查询里出现：每集都完成、
    没有待重新规划的集，且整本源文没有剩余。
    """

    model_config = ConfigDict(extra="forbid")

    episode_count: int
    whole_source: Literal["present", "absent", "not_applicable"]
    source_remaining: bool
    ad_inputs: Literal["present", "absent", "not_applicable"]
    products_without_selling_points: list[str] = Field(default_factory=list)
    episode_source: Literal["present", "absent", "not_applicable"] | None = None
    episode_plan_stale: bool = False
    expected_stale_script_plan_revision: str | None = None
    drafts: list[WorkflowDraft] = Field(default_factory=list)
    formal_script: Literal["present", "absent", "invalid"] | None = None
    script_item_count: int | None = None
    pending_authoring_ids: list[str] = Field(default_factory=list)
    needs_replan_ids: list[str] = Field(default_factory=list)
    #: 本集引用、但没有资产图的角色 / 场景 / 道具（含衍生），与生成入口同一判定（ADR 0073）。
    referenced_assets_without_sheet: list[str] = Field(default_factory=list)
    #: 本集引用、但没有登记的名字；生成入口会据此拒绝。
    unregistered_references: list[str] = Field(default_factory=list)
    #: 本集引用、资产图过期的资产（含衍生）。只陈述，不进建议的下一步。
    referenced_asset_sheets_stale: list[str] = Field(default_factory=list)
    #: 本集引用、缺描述因而不能生成资产图的资产（含衍生）。只陈述，不进建议的下一步。
    referenced_assets_without_description: list[str] = Field(default_factory=list)
    episode_complete: bool = False
    project_complete: bool = False


class WorkflowStatus(BaseModel):
    """Shared response model serialized unchanged by REST and MCP adapters.

    制作状态陈述三件事（见 ``docs/adr/0091``）：``content`` 与 ``artifacts`` 是内容现状；
    ``operations`` 是每个 AI 操作能否执行及原因；``next_action`` 是建议的下一步，分岔处的并列
    选项在 ``next_alternatives``。``blockers`` 只表示项目整体不可用，内容本身的数据问题进
    ``issues``，只陈述、不阻断其余内容。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    project_revision: str
    source_revision: str | None
    project: WorkflowProject
    target: WorkflowTarget | None
    blockers: list[WorkflowBlocker]
    issues: list[WorkflowBlocker] = Field(default_factory=list)
    content: WorkflowContent | None
    operations: dict[str, WorkflowOperation] = Field(default_factory=dict)
    gates: dict[str, dict[str, Any]]
    artifacts: dict[str, dict[str, Any]]
    next_action: WorkflowNextAction
    next_alternatives: list[WorkflowNextAction] = Field(default_factory=list)
    migration_report: MigrationReport | None = None
    """上一次跑完的项目迁移登记与跳过了什么；只作说明，不影响状态与阻断。"""


#: 剪辑时间线目录读不出、或有时间线文件无法解析时记下的 issue 码。
INVALID_EDIT_TIMELINES_CODE = "invalid_edit_timelines"
#: 一集完成且集内没有别的建议动作时 ``next_action`` 的理由；此时下一步为 ``none``。
EPISODE_COMPLETE_REASON = "episode has an edit timeline"
#: 查询范围内每一集都完成、源文也已排布完时 ``next_action`` 的理由。
ALL_EPISODES_COMPLETE_REASON = "every episode has an edit timeline"


def workflow_finished(status: WorkflowStatus) -> bool:
    """建议的下一步已走到末尾：集查询里是一集完成且集内没有别的建议动作，项目查询里是全部完成。

    这不是「一集完成」的判定：完成的集仍可能有待编写条目等集内建议，那一集的完成看
    ``content.episode_complete``。
    """

    return status.next_action.type is WorkflowActionType.NONE and status.next_action.reason in {
        EPISODE_COMPLETE_REASON,
        ALL_EPISODES_COMPLETE_REASON,
    }


#: 每集脚本的产物态派生值：正式脚本可用即 generated，只有 script_plan 即 segmented。
EpisodeScriptStatus = Literal["none", "segmented", "generated"]

#: 每集在广度视图上的粗粒度进度，由该集产物计数派生。
EpisodeProductionStatus = Literal["draft", "scripted", "in_production", "completed"]
#: 项目摘要的产物判定口径：``verified`` 与规范状态逐件比对；``registered`` 只看清单登记与文件在场。
ProjectSummaryCurrency = Literal["verified", "registered"]


class ArtifactCount(BaseModel):
    """一组产物的计数：可用 = current ∪ stale，stale 另计。

    stale 不从 available 里扣——比当前内容旧的产物仍然可用（见 ADR 0062），
    它是「可以决定要不要重生」的提示，不是缺口。
    """

    model_config = ConfigDict(extra="forbid")

    total: int
    available: int
    stale: int

    @classmethod
    def zero(cls) -> ArtifactCount:
        return cls(total=0, available=0, stale=0)

    @classmethod
    def of(cls, collection: Mapping[str, Any], *, total: int) -> ArtifactCount:
        stale = len(collection["stale_ids"])
        return cls(total=total, available=len(collection["current_ids"]) + stale, stale=stale)


def is_episode_complete(videos: ArtifactCount, *, has_edit_timeline: bool) -> bool:
    """一集完成：视频齐全（可用 = current ∪ stale），且至少有一条剪辑时间线。

    这是「一集完成」唯一的判定，集进度、项目卡、制作状态的 ``episode_complete`` 与跨集选择都用它。
    分镜图、待编写条目、待重新规划的单元与资产图都不参与，它们只作为集内建议的下一步。
    """

    return videos.total > 0 and videos.available >= videos.total and has_edit_timeline


class EpisodeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode: int
    script_status: EpisodeScriptStatus
    status: EpisodeProductionStatus
    # 该集的内容规模：分镜图生视频报分镜数、参考生视频报视频单元数，
    # 三种创作类型同一口径。读时按脚本条目数算，不落盘。
    item_count: int
    duration_seconds: int
    storyboards: ArtifactCount
    videos: ArtifactCount


class EpisodesSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    scripted: int
    in_production: int
    completed: int


class ProjectSummary(BaseModel):
    """项目在广度视图上的投影：资产可用计数、分集汇总与各集进度。

    与 ``WorkflowStatus`` 同源不同粒度——后者回答「这个项目下一步做什么」，本模型回答
    「几十个项目各自完成了几集、手上有多少可用产物」；项目的进度就是各集的进度。因此它只读
    项目元数据、各集脚本、产物清单与剪辑时间线的文件名：源文正文与源文修订号（sha256）不参与，否则列出
    N 个项目就要读 N 份小说。剪辑时间线不解析内容，文件损坏只在制作状态里报 issue。

    代价是「源文是否已全部排布成集」不进入本投影，它只能由源文得出。因此本投影可能报告
    「已有的集全部完成」，而制作状态的下一步仍是继续分集规划。
    产物口径本身两处一致：可用与 stale 都取自同一份产物清单。

    产物判定有两种口径（``ProjectSummaryCurrency``）：``verified`` 逐件与规范状态比对，能
    区分 current 与 stale；``registered`` 只看清单登记与文件在场，产物比对不产生 stale。
    前者供单个项目的详情与剧集卡，后者供项目列表——列出 N 个项目时不读任何产物内容。
    参考生视频的重规划壳（``needs_replan``）在两种口径下都计 stale：那是脚本条目自身的
    标记，不是产物比对的结果。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    needs_repair: bool
    repair_reason: str | None
    #: 按 ``ASSET_SPECS`` 的资产类型键给出资产图计数，新增资产类型自动进入投影。
    assets: dict[str, ArtifactCount]
    episodes_summary: EpisodesSummary
    episodes: list[EpisodeSummary]


class EpisodeNextStep(BaseModel):
    """账本中一集建议的下一步，供项目层的逐集清单使用；与按集查询制作状态的 ``next_action`` 相同。"""

    model_config = ConfigDict(extra="forbid")

    episode: int
    #: 该集的集规划已失效（原文已重新规划），下一步为 ``none``，等待重建。
    plan_stale: bool
    next_action: WorkflowNextAction


@dataclass(frozen=True)
class _SharedWorkflowFacts:
    source: SourceRevisionResult | None
    planning_sources: tuple[SourceDoc, ...]
    planning_complete: bool
    sheets: dict[str, dict[str, Any]]
    episodes: list[tuple[int, dict[str, Any]]]
    currency: ArtifactCurrencyResolver | None
    blockers: tuple[WorkflowBlocker, ...]
    issues: tuple[WorkflowBlocker, ...]
    whole_source: bool


def _project_revision(project: Mapping[str, Any]) -> str:
    return prefixed_canonical_json_digest(dict(project))


def _action(
    action_type: WorkflowActionType,
    reason: str,
    *,
    args: dict[str, Any] | None = None,
    ids: list[str] | None = None,
    requires_confirmation: bool = False,
) -> WorkflowNextAction:
    return WorkflowNextAction(
        type=action_type,
        args=args or {},
        requested_ids=ids or [],
        requires_confirmation=requires_confirmation,
        reason=reason,
    )


def planning_docs(project: Mapping[str, Any], source: SourceRevisionResult | None) -> tuple[SourceDoc, ...]:
    """把修订号计算那一次读取的原文转成整本源文：按项目登记的文件清单顺序，账本坐标系里的规范化全文。

    源文在一次状态查询里只读一遍：``compute_source_revision`` 已经把每份源文读进内存，
    分集排布所需的归一化全文由那一次读取派生，不再回磁盘重读。
    """

    if source is None or source.blockers:
        return ()
    by_rel = {unicodedata.normalize("NFC", f"source/{document.name}"): document for document in source.documents}
    docs: list[SourceDoc] = []
    for rel in whole_source_files(project):
        document = by_rel.get(unicodedata.normalize("NFC", rel))
        if document is not None:
            docs.append(SourceDoc(rel_path=rel, text=normalize_source_text(document.text)))
    return tuple(docs)


def _planning_fingerprints_diverged(project: Mapping[str, Any], sources: tuple[SourceDoc, ...]) -> bool:
    recorded = project.get(SOURCE_FINGERPRINTS_KEY)
    if not isinstance(recorded, Mapping) or not recorded:
        return False
    return bool(mismatched_source_fingerprints(recorded, list(sources)))


def _empty_collection() -> dict[str, list[str]]:
    return {"current_ids": [], "missing_ids": [], "stale_ids": []}


def _not_applicable_collection() -> dict[str, Any]:
    return {"state": "not_applicable", **_empty_collection()}


def _episode_production_status(
    script_status: EpisodeScriptStatus,
    storyboards: ArtifactCount,
    videos: ArtifactCount,
    *,
    has_edit_timeline: bool,
) -> EpisodeProductionStatus:
    """完成按 ``is_episode_complete`` 判定；分镜图或视频有任何一件可用即为制作中。"""

    if script_status != "generated":
        return "draft"
    if is_episode_complete(videos, has_edit_timeline=has_edit_timeline):
        return "completed"
    if storyboards.available + videos.available:
        return "in_production"
    return "scripted"


def _asset_bucket_total(project: Mapping[str, Any], bucket_key: str) -> int:
    bucket = project.get(bucket_key)
    return len(bucket) if isinstance(bucket, Mapping) else 0


def _episodes_summary(episodes: list[EpisodeSummary]) -> EpisodesSummary:
    return EpisodesSummary(
        total=len(episodes),
        scripted=sum(1 for episode in episodes if episode.script_status == "generated"),
        in_production=sum(1 for episode in episodes if episode.status == "in_production"),
        completed=sum(1 for episode in episodes if episode.status == "completed"),
    )


class WorkflowStateService:
    """Calculate the first unmet workflow condition from durable project facts."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    @staticmethod
    def _artifact_state(
        resolver: ArtifactComparer,
        key: ArtifactKey,
        artifact_path: str,
        issues: list[WorkflowBlocker],
    ) -> str:
        try:
            comparison = resolver.compare(key, artifact_path=artifact_path)
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError) as exc:
            issues.append(
                WorkflowBlocker(
                    code="artifact_currency_unavailable",
                    path=artifact_path,
                    reason=str(exc),
                )
            )
            return ArtifactStatus.BLOCKED.value
        if comparison.status is ArtifactStatus.BLOCKED:
            assert comparison.blocker is not None
            issues.append(
                WorkflowBlocker(
                    code=comparison.blocker.code,
                    path=comparison.blocker.path,
                    reason=comparison.blocker.detail,
                )
            )
        return comparison.status.value

    @classmethod
    def _classify_artifact(
        cls,
        collection: dict[str, Any],
        *,
        resolver: ArtifactComparer,
        key: ArtifactKey,
        artifact_path: str,
        resource_id: str,
        issues: list[WorkflowBlocker],
    ) -> None:
        state = cls._artifact_state(resolver, key, artifact_path, issues)
        if state == ArtifactStatus.BLOCKED.value:
            collection["state"] = "blocked"
        else:
            collection[f"{state}_ids"].append(resource_id)

    @staticmethod
    def _source_revision(
        project_path: Path, project: dict[str, Any], mode: str, issues: list[WorkflowBlocker]
    ) -> SourceRevisionResult | None:
        if mode == "ad":
            return None
        source = compute_source_revision(project_path, project, SourceScope(kind="all"))
        issues.extend(WorkflowBlocker(code=item.code, path=item.path, reason=item.reason) for item in source.blockers)
        return source

    def _asset_sheets(
        self,
        project_path: Path,
        project: dict[str, Any],
        issues: list[WorkflowBlocker],
        resolver: ArtifactComparer | None,
    ) -> dict[str, dict[str, Any]]:
        collections: dict[str, dict[str, Any]] = {}
        for asset_type, spec in ASSET_SPECS.items():
            collection: dict[str, Any] = _empty_collection()
            bucket = project.get(spec.bucket_key, {})
            if not isinstance(bucket, Mapping):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_asset_bucket",
                        path=spec.bucket_key,
                        reason=f"{spec.bucket_key} must be an object",
                    )
                )
                collection["state"] = "blocked"
                collections[asset_type] = collection
                continue
            for name, item in bucket.items():
                if not isinstance(name, str) or not isinstance(item, Mapping):
                    issues.append(
                        WorkflowBlocker(
                            code="invalid_asset_entry",
                            path=f"{spec.bucket_key}.{name}",
                            reason="asset entries must be named objects",
                        )
                    )
                    collection["state"] = "blocked"
                    collection["current_ids"] = []
                    collection["missing_ids"] = []
                    break
                path = item.get(spec.sheet_field)
                if resolver is not None and isinstance(path, str) and path:
                    self._classify_artifact(
                        collection,
                        resolver=resolver,
                        key=ArtifactKey.asset_sheet(asset_type, asset_name_comparison_key(name)),
                        artifact_path=path,
                        resource_id=name,
                        issues=issues,
                    )
                else:
                    collection["missing_ids"].append(name)
            collections[asset_type] = collection
        return collections

    @staticmethod
    def _episodes(project: dict[str, Any], issues: list[WorkflowBlocker]) -> list[tuple[int, dict[str, Any]]]:
        raw = project.get("episodes")
        if not isinstance(raw, list):
            issues.append(
                WorkflowBlocker(code="invalid_episode_ledger", path="episodes", reason="episodes must be an array")
            )
            return []
        parsed: list[tuple[int, dict[str, Any]]] = []
        seen: set[int] = set()
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_episode_entry",
                        path=f"episodes[{index}]",
                        reason="episode entry must be an object",
                    )
                )
                continue
            number = parse_positive_episode_num(entry.get("episode"))
            if number is None or number in seen:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_episode_number",
                        path=f"episodes[{index}].episode",
                        reason="episode number must be a unique positive integer",
                    )
                )
                continue
            seen.add(number)
            ledger_status = entry.get("ledger_status")
            if ledger_status is not None and not isinstance(ledger_status, str):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_ledger_status",
                        path=f"episodes[{index}].ledger_status",
                        reason="ledger_status must be a string",
                    )
                )
                continue
            parsed.append((number, entry))
        return parsed

    @staticmethod
    def _planning_complete(project: dict[str, Any], planning_sources: tuple[SourceDoc, ...]) -> bool:
        """判定整本源文是否已全部排布完：由账本推导的规划起点之后没有非空白的原文。

        源文只来自 ``planning_sources``——本次请求已经读过一遍的那份，不再回磁盘取。源文在规划之后
        被改动时不算排布完，由规划动作转为重置。
        """

        if not planning_sources or _planning_fingerprints_diverged(project, planning_sources):
            return False
        return not unplanned_text_remains(project, list(planning_sources))

    def _load_script_artifacts(
        self,
        project_path: Path,
        project_name: str,
        project: dict[str, Any],
        target: WorkflowTarget,
        issues: list[WorkflowBlocker],
        resolver: ArtifactCurrencyResolver | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        path = target.script
        state = ArtifactStatus.CURRENT.value
        if resolver is not None:
            state = self._artifact_state(resolver, ArtifactKey.episode_script(target.episode), path, issues)
            if state not in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}:
                return {"state": state, "path": path}, [], None
        try:
            script: Any = self.pm.load_script_readonly(project_name, path)
        except FileNotFoundError:
            return {"state": "missing", "path": path}, [], None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(WorkflowBlocker(code="invalid_script", path=path, reason=str(exc)))
            return {"state": "blocked", "path": path}, [], None
        if not isinstance(script, dict):
            issues.append(WorkflowBlocker(code="invalid_script", path=path, reason="script must be an object"))
            return {"state": "blocked", "path": path}, [], None
        script_episode = script.get("episode")
        if script_episode != target.episode or isinstance(script_episode, bool):
            issues.append(
                WorkflowBlocker(
                    code="script_episode_mismatch",
                    path=f"{path}.episode",
                    reason=f"script episode must equal target episode {target.episode}",
                )
            )
            return {"state": "blocked", "path": path}, [], None
        try:
            kind = ensure_route_skeleton(script, project.get("content_mode"), project.get("generation_mode"))
        except ValueError as exc:
            issues.append(WorkflowBlocker(code="invalid_project_mode", path="content_mode", reason=str(exc)))
            return {"state": "blocked", "path": path}, [], None
        raw_items, id_field, _kind = resolve_kind_items(script, kind=kind)
        # 空的正式脚本合法（见 docs/adr/0091）：「非空」只留在各 AI 生成动作的产出验收里。
        if not isinstance(raw_items, list) or not all(isinstance(item, dict) for item in raw_items):
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_collection",
                    path=f"{path}.{kind}",
                    reason=f"{kind} must be an array of objects",
                )
            )
            return {"state": "blocked", "path": path}, [], kind
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_items):
            resource_id = item.get(id_field)
            if not isinstance(resource_id, str) or not resource_id:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"{id_field} must be a non-empty string",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
            if kind != "video_units" and STORYBOARD_ITEM_ID_PATTERN.fullmatch(resource_id) is None:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"invalid {id_field}: {resource_id}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
            if resource_id in seen_ids:
                issues.append(
                    WorkflowBlocker(
                        code="duplicate_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"duplicate {id_field}: {resource_id}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
            seen_ids.add(resource_id)
            duration = item.get("duration_seconds")
            duration_max = 300 if kind == "video_units" else 60
            replan_shell = (
                kind == "video_units"
                and item.get("needs_replan") is True
                and not str(item.get("text") or "").strip()
                and duration == 0
            )
            if (
                duration is not None
                and not replan_shell
                and (isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= duration_max)
            ):
                issues.append(
                    WorkflowBlocker(
                        code="invalid_script_structure",
                        path=f"{path}.{kind}[{index}].duration_seconds",
                        reason=f"duration_seconds must be an integer between 1 and {duration_max}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind
        validation = DataValidator(str(self.pm.projects_dir)).validate_episode_payload(
            project_path,
            project,
            script,
            validate_artifacts=False,
        )
        if not validation.valid:
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_structure",
                    path=path,
                    reason="; ".join(validation.errors),
                )
            )
            return {"state": "blocked", "path": path}, [], kind
        return {"state": state, "path": path}, raw_items, kind

    @classmethod
    def _media_collection(
        cls,
        project_path: Path,
        items: list[dict[str, Any]],
        kind: str | None,
        field: str,
        *,
        episode: int,
        resolver: ArtifactComparer | None,
        issues: list[WorkflowBlocker],
    ) -> dict[str, Any]:
        collection: dict[str, Any] = _empty_collection()
        if kind is None:
            return collection
        id_field = SKELETONS[kind].id_field
        for item in items:
            resource_id = item.get(id_field)
            if not isinstance(resource_id, str) or not resource_id:
                continue
            if kind == "video_units" and item.get("needs_replan") is True:
                collection["stale_ids"].append(resource_id)
                continue
            artifact_path = get_generated_assets(item).get(field)
            if resolver is not None and isinstance(artifact_path, str) and artifact_path:
                key = (
                    ArtifactKey.episode_storyboard(episode, resource_id)
                    if field == "storyboard_image"
                    else ArtifactKey.episode_video(episode, resource_id)
                    if field == "video_clip"
                    else ArtifactKey.episode_audio(episode, resource_id)
                )
                cls._classify_artifact(
                    collection,
                    resolver=resolver,
                    key=key,
                    artifact_path=artifact_path,
                    resource_id=resource_id,
                    issues=issues,
                )
            else:
                collection["missing_ids"].append(resource_id)
        return collection

    def get_status(self, project_name: str, episode: int | None = None) -> WorkflowStatus:
        project_path = self.pm.get_project_path(project_name)
        try:
            project: Any = self.pm.load_project(project_name)
            if not isinstance(project, dict):
                raise ValueError("project.json must be an object")
        except (OSError, ValueError) as exc:
            failure = load_migration_verdict(project_path)
            if failure is not None:
                return self._migration_blocked_status({}, failure)
            return WorkflowStatus(
                project_revision="",
                source_revision=None,
                project=WorkflowProject(content_mode="unknown", generation_mode="unknown", grid_storyboard=False),
                target=None,
                blockers=[WorkflowBlocker(code="project_data_unavailable", path="project.json", reason=str(exc))],
                content=None,
                gates={},
                artifacts={},
                next_action=_action(WorkflowActionType.NONE, "project data cannot be read"),
            )
        failure = load_migration_verdict(project_path)
        if failure is not None:
            return self._migration_blocked_status(project, failure)
        shared = self._shared_facts(project_path, project)
        status = self._get_status(project_name, project, project_path, episode, shared)
        status.migration_report = load_migration_report(project_path)
        return status

    def get_episode_next_steps(self, project_name: str) -> list[EpisodeNextStep]:
        """按账本顺序给出每一集的下一步。

        项目整体不可用（迁移失败、项目数据读不出、存在 blockers）时没有集层的下一步，返回空列表；
        此时项目层的制作状态会说明原因。
        """

        project_path = self.pm.get_project_path(project_name)
        try:
            project: Any = self.pm.load_project(project_name)
        except FileNotFoundError:
            raise
        except (OSError, ValueError):
            return []
        if not isinstance(project, dict) or load_migration_verdict(project_path) is not None:
            return []
        shared = self._shared_facts(project_path, project)
        if shared.blockers:
            return []
        steps: list[EpisodeNextStep] = []
        for pair in shared.episodes:
            status = self._episode_status(project_name, project, project_path, shared, pair)
            steps.append(
                EpisodeNextStep(
                    episode=pair[0],
                    plan_stale=status.content is not None and status.content.episode_plan_stale,
                    next_action=status.next_action,
                )
            )
        return steps

    def get_project_summary(
        self,
        project_name: str,
        *,
        preloaded_scripts: Mapping[str, dict[str, Any]] | None = None,
        currency: ProjectSummaryCurrency = "verified",
    ) -> ProjectSummary:
        """项目在广度视图上的投影（见 ``ProjectSummary``）。

        ``preloaded_scripts`` 按 ``episodes[].script_file`` 原值作 key，命中即复用调用方
        （项目列表把同一份剧本喂给封面解析）已经读过的那份，一次列表请求每集只读一次剧本。

        ``currency`` 选产物判定口径：``verified`` 逐件与规范状态比对，代价是每件产物都要
        重建基线（读清单、哈希分镜图与其引用图）；``registered`` 一次读入清单，只判断登记
        与在场，不读产物内容（在场检查只探一个字节）。列表页取后者，其余取前者。
        """

        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        failure = load_migration_verdict(project_path)
        if failure is not None:
            return self._migration_blocked_summary(project, self._episodes(project, []), failure)
        episodes = self._episodes(project, [])
        try:
            resolver: ArtifactComparer | None = (
                RegisteredArtifactResolver(project_path, project)
                if currency == "registered"
                else ArtifactCurrencyResolver(project_path)
            )
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError):
            # 清单读不出来时不退回「文件存在即产物存在」：本投影一律按 0 件可用报告，
            # 与工作台把它记成 artifact_currency_unavailable 阻断同一口径。
            resolver = None
        assets = self._asset_counts(project_path, project, resolver)
        episode_summaries = [
            self._episode_summary(
                project_name,
                project,
                project_path,
                number,
                entry,
                resolver=resolver,
                preloaded_scripts=preloaded_scripts,
            )
            for number, entry in episodes
        ]
        return ProjectSummary(
            needs_repair=False,
            repair_reason=None,
            assets=assets,
            episodes_summary=_episodes_summary(episode_summaries),
            episodes=episode_summaries,
        )

    def _asset_counts(
        self,
        project_path: Path,
        project: dict[str, Any],
        resolver: ArtifactComparer | None,
    ) -> dict[str, ArtifactCount]:
        sheets = self._asset_sheets(project_path, project, [], resolver)
        return {
            asset_type: ArtifactCount.of(
                sheets.get(asset_type, _empty_collection()),
                total=_asset_bucket_total(project, spec.bucket_key),
            )
            for asset_type, spec in ASSET_SPECS.items()
        }

    def _episode_summary(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        number: int,
        entry: dict[str, Any],
        *,
        resolver: ArtifactComparer | None,
        preloaded_scripts: Mapping[str, dict[str, Any]] | None,
    ) -> EpisodeSummary:
        script_status = self._episode_script_status(project, project_path, number, entry, resolver)
        items: list[dict[str, Any]] = []
        kind: str | None = None
        if script_status == "generated":
            script = self._summary_script(project_name, entry, preloaded_scripts)
            if script is not None:
                try:
                    kind = ensure_route_skeleton(script, project.get("content_mode"), project.get("generation_mode"))
                except ValueError:
                    kind = None
                if kind is not None:
                    raw_items, _id_field, _kind = resolve_kind_items(script, kind=kind)
                    items = (
                        [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
                    )
        storyboards = (
            ArtifactCount.of(
                self._media_collection(
                    project_path,
                    items,
                    kind,
                    "storyboard_image",
                    episode=number,
                    resolver=resolver,
                    issues=[],
                ),
                total=len(items),
            )
            if project.get("generation_mode") == "storyboard"
            else ArtifactCount.zero()
        )
        videos = ArtifactCount.of(
            self._media_collection(
                project_path,
                items,
                kind,
                "video_clip",
                episode=number,
                resolver=resolver,
                issues=[],
            ),
            total=len(items),
        )
        return EpisodeSummary(
            episode=number,
            script_status=script_status,
            status=_episode_production_status(
                script_status,
                storyboards,
                videos,
                has_edit_timeline=EditTimelineStore(self.pm, project_name).has_documents(number),
            ),
            item_count=len(items),
            duration_seconds=script_duration_total(kind, items) if kind is not None else 0,
            storyboards=storyboards,
            videos=videos,
        )

    def _summary_script(
        self,
        project_name: str,
        entry: dict[str, Any],
        preloaded_scripts: Mapping[str, dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        script_file = entry.get("script_file")
        if not isinstance(script_file, str) or not script_file:
            return None
        if preloaded_scripts is not None and script_file in preloaded_scripts:
            return preloaded_scripts[script_file]
        try:
            return self.pm.load_script_readonly(project_name, script_file)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            # 列出项目不因一集剧本损坏而失败：该集按 0 件产物报告，损坏本身由工作台的
            # 制作状态查询报成 invalid_script 阻断。
            return None

    def _episode_script_status(
        self,
        project: dict[str, Any],
        project_path: Path,
        number: int,
        entry: dict[str, Any],
        resolver: ArtifactComparer | None,
    ) -> EpisodeScriptStatus:
        """由 script_plan 与正式脚本的产物态派生该集的脚本进度。

        账本标 stale 的集（重新规划后原文范围已失效）回到 none：它的下游要重做。
        """

        if entry.get("ledger_status") == "stale":
            return "none"
        script_file = entry.get("script_file")
        if resolver is not None and isinstance(script_file, str) and script_file:
            state = self._artifact_state(resolver, ArtifactKey.episode_script(number), script_file, [])
            if state in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}:
                return "generated"
        script_plan = script_review.script_plan_path(project_path, project, number)
        if script_plan is None:
            return "none"
        if script_review.script_plan_quarantined(project_path, project, number):
            # 草稿在场即已分段：首轮拆分失败时正式文件从未写过，报 none 会把用户
            # 路由回源文审阅页，见不到草稿详情与修复入口。
            return "segmented"
        if resolver is None:
            return "none"
        state = self._artifact_state(
            resolver, ArtifactKey.episode_script_plan(number), script_plan.relative_to(project_path).as_posix(), []
        )
        return "segmented" if state in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value} else "none"

    @classmethod
    def _migration_blocked_summary(
        cls,
        project: dict[str, Any],
        episodes: list[tuple[int, dict[str, Any]]],
        failure: MigrationFailureRecord,
    ) -> ProjectSummary:
        """升级失败的项目照常列出，但一件产物都不报可用。

        产物清单是唯一的产物口径，而它对未升级的数据不可读——报「有几件可用」就要
        退回按文件是否存在计数，恰是本口径要退场的那一套。用户仍看到项目、集数与
        待修复原因，只读入口照常打开。
        """

        summaries = [
            EpisodeSummary(
                episode=number,
                script_status="none",
                status="draft",
                item_count=0,
                duration_seconds=0,
                storyboards=ArtifactCount.zero(),
                videos=ArtifactCount.zero(),
            )
            for number, _entry in episodes
        ]
        return ProjectSummary(
            needs_repair=True,
            repair_reason=failure.reason,
            assets={
                asset_type: ArtifactCount(
                    total=_asset_bucket_total(project, spec.bucket_key),
                    available=0,
                    stale=0,
                )
                for asset_type, spec in ASSET_SPECS.items()
            },
            episodes_summary=_episodes_summary(summaries),
            episodes=summaries,
        )

    def _shared_facts(self, project_path: Path, project: dict[str, Any]) -> _SharedWorkflowFacts:
        """一次查询内各集共用的事实。

        阻断项只收「项目整体不可用」：创作类型或生成模式读不出（无从判定任何内容），或产物清单读不出。
        其余数据问题进 ``issues``，只在现状里陈述，不挡其他内容与操作（见 ``docs/adr/0091``）。
        """
        mode = project.get("content_mode")
        generation_mode = project.get("generation_mode")
        blockers: list[WorkflowBlocker] = []
        issues: list[WorkflowBlocker] = []
        if not isinstance(mode, str) or mode not in {"narration", "drama", "ad"}:
            blockers.append(
                WorkflowBlocker(code="invalid_content_mode", path="content_mode", reason="unsupported mode")
            )
        if not isinstance(generation_mode, str) or generation_mode not in {"storyboard", "reference_video"}:
            blockers.append(
                WorkflowBlocker(code="invalid_generation_mode", path="generation_mode", reason="unsupported route")
            )
        grid_storyboard = project.get("grid_storyboard")
        if grid_storyboard is not None and not isinstance(grid_storyboard, bool):
            issues.append(
                WorkflowBlocker(
                    code="invalid_grid_storyboard",
                    path="grid_storyboard",
                    reason="grid_storyboard must be a boolean",
                )
            )
        if mode == "ad":
            target_duration = project.get("target_duration")
            if not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_target_duration",
                        path="target_duration",
                        reason="ad target_duration must be a positive integer",
                    )
                )
            if grid_storyboard is True:
                issues.append(
                    WorkflowBlocker(
                        code="invalid_grid_storyboard",
                        path="grid_storyboard",
                        reason="ad workflow does not support grid storyboards",
                    )
                )
        # ``get_status`` refuses an unmigrated project before reaching here, so the
        # only way to arrive without a resolver is a damaged sidecar — a blocker,
        # never permission to classify artifacts by filesystem existence instead.
        currency: ArtifactCurrencyResolver | None = None
        try:
            currency = ArtifactCurrencyResolver(project_path)
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError) as exc:
            blockers.append(
                WorkflowBlocker(
                    code="artifact_currency_unavailable",
                    path=".arcreel_artifacts.json",
                    reason=str(exc),
                )
            )
        asset_validation = DataValidator(str(self.pm.projects_dir)).validate_asset_definitions(project)
        if not asset_validation.valid:
            issues.append(
                WorkflowBlocker(
                    code="invalid_asset_definitions",
                    path="project.json",
                    reason="; ".join(asset_validation.errors),
                )
            )
        source = self._source_revision(project_path, project, str(mode), issues)
        planning_sources = planning_docs(project, source) if mode != "ad" else ()
        planning_complete = self._planning_complete(project, planning_sources)
        sheets = self._asset_sheets(project_path, project, issues, currency)
        episodes = self._episodes(project, issues)
        return _SharedWorkflowFacts(
            source=source,
            planning_sources=planning_sources,
            planning_complete=planning_complete,
            sheets=sheets,
            episodes=episodes,
            currency=currency,
            blockers=tuple(blockers),
            issues=tuple(issues),
            whole_source=whole_source_present(planning_sources),
        )

    def _get_status(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        episode: int | None,
        shared: _SharedWorkflowFacts,
    ) -> WorkflowStatus:
        mode = project.get("content_mode")
        if episode is not None and (isinstance(episode, bool) or episode < 1):
            raise WorkflowRequestError("episode must be a positive integer")
        if mode == "ad" and episode not in {None, 1}:
            raise WorkflowRequestError("ad workflow only has episode 1")
        if shared.blockers:
            return self._response(
                project,
                shared,
                target=None,
                content=None,
                operations={},
                gates={},
                artifacts=self._base_artifacts(project, shared),
                next_action=_action(WorkflowActionType.NONE, "workflow is blocked"),
            )
        if mode == "ad":
            selected = next((pair for pair in shared.episodes if pair[0] == 1), (1, {}))
            return self._episode_status(project_name, project, project_path, shared, selected)
        if episode is None:
            return self._next_episode_status(project_name, project, project_path, shared)
        selected = next((pair for pair in shared.episodes if pair[0] == episode), None)
        if selected is not None:
            return self._episode_status(project_name, project, project_path, shared, selected)
        status = self._next_episode_status(project_name, project, project_path, shared)
        status.issues.append(
            WorkflowBlocker(
                code="episode_unavailable",
                path=f"episodes.{episode}",
                reason="requested episode is not in the episode ledger",
            )
        )
        return status

    def _next_episode_status(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        shared: _SharedWorkflowFacts,
    ) -> WorkflowStatus:
        """没有指定集时：取账本顺序中第一个未完成、且集规划状态不是 stale 的集。

        其余集都完成时回到项目层：整本源文还有未切分的原文就继续分集规划；有 stale 集时停在
        第一个 stale 集上陈述现状，不给动作；否则全部完成。
        """
        if not shared.episodes:
            return self._project_status(project, shared)
        first: WorkflowStatus | None = None
        first_stale: WorkflowStatus | None = None
        any_complete = False
        for pair in shared.episodes:
            status = self._episode_status(project_name, project, project_path, shared, pair)
            first = first or status
            if status.content is not None and status.content.episode_plan_stale:
                first_stale = first_stale or status
                continue
            if status.content is None or not status.content.episode_complete:
                return status
            any_complete = True
        assert first is not None
        if shared.whole_source and not shared.planning_complete:
            next_action = self._planning_action(project, shared, "source text remains unplanned")
        elif first_stale is not None:
            reason = "remaining episodes await replanning" if any_complete else "every episode awaits replanning"
            return first_stale.model_copy(
                update={"next_action": _action(WorkflowActionType.NONE, reason), "next_alternatives": []}
            )
        else:
            content = first.content.model_copy(update={"project_complete": True}) if first.content else None
            return first.model_copy(
                update={
                    "content": content,
                    "next_action": _action(WorkflowActionType.NONE, ALL_EPISODES_COMPLETE_REASON),
                    "next_alternatives": [],
                }
            )
        return first.model_copy(update={"next_action": next_action, "next_alternatives": []})

    def _planning_action(
        self, project: dict[str, Any], shared: _SharedWorkflowFacts, reason: str
    ) -> WorkflowNextAction:
        """继续分集规划的动作：规划器会拒绝接续时（切出集缺位置记录、源文已改动）改为从头重置。"""
        if legacy_cut_episode_ids(project):
            return _action(
                WorkflowActionType.RESET_EPISODE_PLANNING,
                "episode ledger lacks source range records",
            )
        if _planning_fingerprints_diverged(project, shared.planning_sources):
            return _action(
                WorkflowActionType.RESET_EPISODE_PLANNING,
                "source files changed after episode planning",
            )
        return _action(WorkflowActionType.PLAN_EPISODES, reason)

    @staticmethod
    def _project_content(project: Mapping[str, Any], shared: _SharedWorkflowFacts) -> WorkflowContent:
        is_ad = project.get("content_mode") == "ad"
        products = project.get("products")
        return WorkflowContent(
            episode_count=len(shared.episodes),
            whole_source="not_applicable" if is_ad else ("present" if shared.whole_source else "absent"),
            source_remaining=not is_ad and shared.whole_source and not shared.planning_complete,
            ad_inputs=("present" if ad_inputs_present(project) else "absent") if is_ad else "not_applicable",
            products_without_selling_points=(
                [
                    name
                    for name, item in products.items()
                    if isinstance(item, Mapping) and not item.get("selling_points")
                ]
                if is_ad and isinstance(products, Mapping)
                else []
            ),
        )

    @staticmethod
    def _base_artifacts(project: Mapping[str, Any], shared: _SharedWorkflowFacts) -> dict[str, dict[str, Any]]:
        is_ad = project.get("content_mode") == "ad"
        return {
            "asset_sheets": shared.sheets,
            "script_plan": {"state": "not_applicable" if is_ad else "missing"},
            "script": {"state": "missing"},
            "storyboards": _empty_collection(),
            "videos": _empty_collection(),
            "audio": _empty_collection(),
        }

    def _project_status(self, project: dict[str, Any], shared: _SharedWorkflowFacts) -> WorkflowStatus:
        """没有集的项目：有整本源文则 AI 分集规划，否则上传原文；两者都可以改为新建一集。"""
        if shared.whole_source:
            next_action = self._planning_action(project, shared, "episode ledger has no episodes")
        else:
            next_action = _action(WorkflowActionType.COLLECT_PROJECT_INPUT, "no episodes and no source text yet")
        return self._response(
            project,
            shared,
            target=None,
            content=self._project_content(project, shared),
            operations={
                WorkflowActionType.PLAN_EPISODES: admit_plan_episodes(
                    project.get("content_mode"),
                    whole_source=shared.whole_source,
                    replan_pending=replan_candidate(project) is not None,
                )
            },
            gates={},
            artifacts=self._base_artifacts(project, shared),
            next_action=next_action,
            next_alternatives=[_action(WorkflowActionType.CREATE_EPISODE, "an episode can also be created by hand")],
        )

    def _edit_timeline_ids(
        self,
        project_name: str,
        episode: int,
        artifacts: dict[str, dict[str, Any]],
        issues: list[WorkflowBlocker],
    ) -> list[str] | None:
        """「剪辑」一步的完成判据：该集至少有一条剪辑时间线。结果同时写进 ``artifacts``。

        目录读取失败或有时间线文件无法解析时记一条 issue 并返回 None。
        """

        try:
            documents = EditTimelineStore(self.pm, project_name).list_documents(episode, strict=True)
        except (OSError, EditTimelineError) as exc:
            issues.append(
                WorkflowBlocker(
                    code=INVALID_EDIT_TIMELINES_CODE, path=f"edit_timelines/episode_{episode}", reason=str(exc)
                )
            )
            artifacts["edit_timelines"] = {"timeline_ids": []}
            return None
        ids = [document.id for document in documents]
        artifacts["edit_timelines"] = {"timeline_ids": ids}
        return ids

    @staticmethod
    def _episode_target(number: int, entry: Mapping[str, Any], issues: list[WorkflowBlocker]) -> WorkflowTarget | None:
        script_path = entry.get("script_file")
        if not isinstance(script_path, str) or not script_path:
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_binding",
                    path=f"episodes.{number}.script_file",
                    reason="script_file must be a non-empty string",
                )
            )
            return None
        script_filename = ProjectManager.normalize_script_filename(script_path)
        if "/" in script_filename or "\\" in script_filename:
            issues.append(
                WorkflowBlocker(
                    code="invalid_script_path",
                    path=f"episodes.{number}.script_file",
                    reason="script_file must resolve to a bare filename under scripts/",
                )
            )
            return None
        return WorkflowTarget(
            episode=number,
            script=script_path,
            script_filename=script_filename,
            source=episode_source_relpath(number),
        )

    @staticmethod
    def _episode_drafts(project_path: Path, project: dict[str, Any], number: int) -> list[WorkflowDraft]:
        """目标集上在场的草稿：脚本规划草稿，以及参考生视频的提示词编写草稿。"""
        kinds: list[str] = []
        script_plan_kind = script_review.script_plan_quarantine_kind(project)
        if script_plan_kind is not None:
            kinds.append(script_plan_kind)
        if is_reference_video_project(project):
            kinds.append(QUARANTINE_KIND_PROMPT_AUTHORING)
        drafts: list[WorkflowDraft] = []
        for kind in kinds:
            if not quarantine_exists(project_path, number, kind):
                continue
            draft = read_quarantine(project_path, number, kind)
            drafts.append(
                WorkflowDraft(
                    kind=kind,
                    path=quarantine_path(project_path, number, kind).relative_to(project_path).as_posix(),
                    # 信封读不出的草稿同样要先修好才能处置。
                    needs_repair=draft is None or bool(draft.violations),
                )
            )
        return drafts

    @staticmethod
    def _stale_episode_plan(
        project_path: Path, project: dict[str, Any], number: int, entry: Mapping[str, Any]
    ) -> tuple[bool, str | None]:
        """集规划状态为 stale 且脚本规划尚未重建：返回 ``(是否 stale, 重建基线 revision)``。

        重建完成（正式 script_plan 已不是 stale 时的那一份，或已显式记下重建完成）后按常规内容陈述。
        """
        if entry.get("ledger_status") != "stale":
            return False, None
        if script_review.STALE_SCRIPT_PLAN_REVISION_FIELD not in entry:
            return True, None
        stale_revision = entry.get(script_review.STALE_SCRIPT_PLAN_REVISION_FIELD)
        rebuilt_revision = entry.get(script_review.STALE_SCRIPT_PLAN_REBUILT_REVISION_FIELD)
        path = script_review.script_plan_path(project_path, project, number)
        live_revision = script_review.content_fingerprint(path) if path is not None else None
        if live_revision is None or (live_revision == stale_revision and rebuilt_revision != live_revision):
            return True, stale_revision if isinstance(stale_revision, str) else None
        return False, None

    def _script_plan_artifact(
        self,
        project_path: Path,
        project: dict[str, Any],
        number: int,
        currency: ArtifactCurrencyResolver | None,
        issues: list[WorkflowBlocker],
    ) -> dict[str, Any]:
        path = script_review.script_plan_path(project_path, project, number)
        revision = script_review.content_fingerprint(path) if path is not None else None
        state = ArtifactStatus.CURRENT.value if revision is not None else ArtifactStatus.MISSING.value
        if currency is not None and path is not None:
            state = self._artifact_state(
                currency,
                ArtifactKey.episode_script_plan(number),
                path.relative_to(project_path).as_posix(),
                issues,
            )
        return {
            "state": state,
            "path": str(path.relative_to(project_path)) if path is not None else None,
            "revision": revision,
        }

    @staticmethod
    def _reference_admission(project: dict[str, Any], items: list[dict[str, Any]], kind: str | None) -> Any:
        """本集条目的引用准入，与生成入口同一判定（ADR 0073）：正文单元从正文派生，分镜条目读引用字段。"""
        catalog = build_reference_catalog(project)
        if kind != "video_units":
            return admit_storyboard_items(catalog, items)
        references: list[tuple[str, str]] = []
        unregistered: list[str] = []
        for item in items:
            text = item.get("text")
            if not isinstance(text, str) or not text:
                continue
            resources, missing = derive_references_from_text(text, project)
            references.extend((resource.type, resource.name) for resource in resources)
            unregistered.extend(missing)
        return admit_references(catalog, references=references, unregistered=unregistered)

    def _referenced_sheet_facts(
        self,
        project: dict[str, Any],
        items: list[dict[str, Any]],
        kind: str | None,
        shared: _SharedWorkflowFacts,
        issues: list[WorkflowBlocker],
    ) -> tuple[list[str], list[str], list[str]]:
        """本集引用的资产图现状：(待生成且可生成, 过期, 缺描述)。

        待生成与资产图批量的集范围同一判定：缺描述的不算；衍生的本体没有可用资产图、也不在同批时不算。
        """
        missing: list[str] = []
        stale: list[str] = []
        without_description: list[str] = []
        accepted_sheets: set[tuple[str, str]] = set()
        for asset in sorted(
            episode_referenced_assets(project, ({kind: items} if kind else None)),
            key=lambda asset: (asset.owner is not None, asset.asset_type, asset.name),
        ):
            spec = ASSET_SPECS[asset.asset_type]
            owner = project[spec.bucket_key][asset.owner or asset.name]
            entry = (
                derivative_table(owner)[split_derivative_artifact_id(asset.name)[1]]
                if asset.owner is not None
                else owner
            )
            description = entry.get("description")
            if not isinstance(description, str) or not description.strip():
                without_description.append(asset.name)
                continue
            path = entry.get(spec.sheet_field)
            sheet_state = (
                self._artifact_state(
                    shared.currency,
                    (
                        derivative_artifact_key(
                            *map(asset_name_comparison_key, split_derivative_artifact_id(asset.name))
                        )
                        if asset.owner is not None
                        else ArtifactKey.asset_sheet(asset.asset_type, asset_name_comparison_key(asset.name))
                    ),
                    path,
                    issues,
                )
                if shared.currency is not None and isinstance(path, str) and path
                else ArtifactStatus.MISSING.value
            )
            if sheet_state == ArtifactStatus.STALE.value:
                stale.append(asset.name)
            if sheet_state != ArtifactStatus.MISSING.value:
                continue
            if asset.owner is not None:
                owner_sheets = shared.sheets[asset.asset_type]
                if (asset.asset_type, asset.owner) not in accepted_sheets and asset.owner not in (
                    owner_sheets["current_ids"] + owner_sheets["stale_ids"]
                ):
                    continue
            missing.append(asset.name)
            accepted_sheets.add((asset.asset_type, asset.name))
        return missing, stale, without_description

    def _episode_status(
        self,
        project_name: str,
        project: dict[str, Any],
        project_path: Path,
        shared: _SharedWorkflowFacts,
        selected: tuple[int, dict[str, Any]],
    ) -> WorkflowStatus:
        """一集的内容现状、各 AI 操作的准入与建议的下一步（顺序见 ``docs/adr/0091`` 与 Spec）。"""
        number, entry = selected
        mode = project.get("content_mode")
        is_ad = mode == "ad"
        generation_mode = project.get("generation_mode")
        grid = project.get("grid_storyboard") is True and generation_mode == "storyboard"
        issues = list(shared.issues)
        artifacts = self._base_artifacts(project, shared)
        gates: dict[str, dict[str, Any]] = {
            "script_plan_review": {"state": "not_applicable" if is_ad else "pending", "revision": None}
        }
        content = self._project_content(project, shared)
        operations: dict[str, OperationAdmission] = {
            WorkflowActionType.PLAN_EPISODES: admit_plan_episodes(
                mode, whole_source=shared.whole_source, replan_pending=replan_candidate(project) is not None
            ),
        }

        def respond(
            target: WorkflowTarget | None,
            next_action: WorkflowNextAction,
            alternatives: list[WorkflowNextAction] | None = None,
        ) -> WorkflowStatus:
            return self._response(
                project,
                shared,
                target=target,
                content=content,
                operations=operations,
                gates=gates,
                artifacts=artifacts,
                next_action=next_action,
                next_alternatives=alternatives or [],
                issues=issues,
            )

        target = self._episode_target(number, entry, issues)
        if target is None:
            return respond(None, _action(WorkflowActionType.NONE, "episode script binding is invalid"))

        episode_source = not is_ad and episode_source_present(project_path, number, entry)
        content.episode_source = "not_applicable" if is_ad else ("present" if episode_source else "absent")
        content.drafts = self._episode_drafts(project_path, project, number)
        stale, stale_revision = (
            (False, None) if is_ad else self._stale_episode_plan(project_path, project, number, entry)
        )
        content.episode_plan_stale = stale
        content.expected_stale_script_plan_revision = stale_revision

        script_plan_state: str | None = None
        review_pending = False
        if not is_ad:
            artifacts["script_plan"] = self._script_plan_artifact(
                project_path, project, number, shared.currency, issues
            )
            if script_review.script_plan_quarantined(project_path, project, number):
                artifacts["script_plan"]["state"] = "blocked"
            elif stale:
                artifacts["script_plan"]["state"] = "stale"
            script_plan_state = artifacts["script_plan"]["state"]
            review = script_review.review_status(project_path, project, number)
            gates["script_plan_review"] = {
                "state": "confirmed" if review == "confirmed" else "pending",
                "revision": artifacts["script_plan"].get("revision"),
            }
            # 已有正式脚本在用时，重跑的规划未确认只陈述为待确认，不进下一步；集规划 stale 的集
            # 旧脚本已不可用，重建的规划仍须确认。
            formal_in_use = entry.get("ledger_status") != "stale" and script_review.prompt_authoring_generated(
                project_path, project, number
            )
            review_pending = (
                script_plan_state in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}
                and review != "confirmed"
                and not formal_in_use
            )

        script_artifact, items, kind = self._load_script_artifacts(
            project_path, project_name, project, target, issues, shared.currency
        )
        artifacts["script"] = script_artifact
        formal_present = script_artifact["state"] in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}
        content.formal_script = (
            "present" if formal_present else "invalid" if script_artifact["state"] == "blocked" else "absent"
        )
        pending_ids: list[str] = []
        replan_ids: list[str] = []
        without_sheet: list[str] = []
        missing_sheets: list[str] = []
        if formal_present:
            content.script_item_count = len(items)
            pending_ids = pending_authoring_entry_ids(items, kind)
            id_field = SKELETONS[kind].id_field if kind in SKELETONS else None
            replan_ids = [
                str(item.get(id_field))
                for item in items
                if kind == "video_units" and id_field is not None and item.get("needs_replan") is True
            ]
            admission = self._reference_admission(project, items, kind)
            without_sheet = [name for _asset_type, name in admission.without_sheet]
            content.referenced_assets_without_sheet = without_sheet
            content.unregistered_references = list(admission.unregistered)
            missing_sheets, content.referenced_asset_sheets_stale, content.referenced_assets_without_description = (
                self._referenced_sheet_facts(project, items, kind, shared, issues)
            )
            artifacts["storyboards"] = (
                self._media_collection(
                    project_path,
                    items,
                    kind,
                    "storyboard_image",
                    episode=number,
                    resolver=shared.currency,
                    issues=issues,
                )
                if generation_mode == "storyboard"
                else _not_applicable_collection()
            )
            artifacts["videos"] = self._media_collection(
                project_path,
                items,
                kind,
                "video_clip",
                episode=number,
                resolver=shared.currency,
                issues=issues,
            )
            # 旁白配音只作为信息报告，不参与下一步：缺 TTS 不拦剪辑，补 TTS 由用户显式发起（见
            # generate_narration_audio）；后期配音项目不需要 TTS，不报缺口。读不出的配音状态只落在集合的
            # state 上，不进 issues。
            artifacts["audio"] = (
                self._media_collection(
                    project_path,
                    items,
                    kind,
                    "narration_audio",
                    episode=number,
                    resolver=shared.currency,
                    issues=[],
                )
                if mode == "narration"
                and generation_mode == "storyboard"
                and project_narration_delivery(project) == USE_TTS
                else _not_applicable_collection()
            )
        elif not is_ad:
            artifacts["storyboards"] = (
                _empty_collection() if generation_mode == "storyboard" else _not_applicable_collection()
            )
        content.pending_authoring_ids = pending_ids
        content.needs_replan_ids = replan_ids
        # 剪辑时间线只陈述这一类内容：视频是否齐全不影响列出已有的几条。
        timeline_ids = self._edit_timeline_ids(project_name, number, artifacts, issues) if formal_present else []
        videos = artifacts["videos"]
        operations[WorkflowActionType.CREATE_EDIT_TIMELINE] = admit_edit_timeline(
            available_videos=len(videos.get("current_ids", [])) + len(videos.get("stale_ids", []))
        )
        content.episode_complete = (
            formal_present
            and videos.get("state") != "blocked"
            and is_episode_complete(ArtifactCount.of(videos, total=len(items)), has_edit_timeline=bool(timeline_ids))
        )

        operations[WorkflowActionType.PREPARE_SCRIPT_PLAN] = admit_script_plan(mode, episode_source=episode_source)
        operations[WorkflowActionType.GENERATE_SCRIPT] = admit_ad_script(
            mode,
            formal_script=formal_present,
            ad_inputs=ad_inputs_present(project),
        )
        operations[WorkflowActionType.AUTHOR_PROMPTS] = admit_author_prompts(
            formal_script=formal_present,
            pending_ids=pending_ids,
            draft_pending=any(draft.kind == QUARANTINE_KIND_PROMPT_AUTHORING for draft in content.drafts),
        )

        episode_args = {"episode_id": number}
        blank = _action(WorkflowActionType.START_BLANK_SCRIPT, "write the formal script by hand", args=episode_args)
        if stale:
            return respond(
                target,
                _action(WorkflowActionType.NONE, "the episode plan is stale; its script plan awaits a rebuild"),
            )
        if content.drafts:
            draft = content.drafts[0]
            return respond(
                target,
                _action(
                    WorkflowActionType.RESOLVE_DRAFT,
                    "a draft awaits repair" if draft.needs_repair else "an editable draft awaits completion",
                    args={**episode_args, "draft_kind": draft.kind, "needs_repair": draft.needs_repair},
                ),
            )
        if content.formal_script == "invalid":
            return respond(target, _action(WorkflowActionType.NONE, "the formal script is invalid"))
        if not formal_present:
            if is_ad:
                if operations[WorkflowActionType.GENERATE_SCRIPT].admitted:
                    return respond(
                        target,
                        _action(WorkflowActionType.GENERATE_SCRIPT, "the ad has no script yet", args=episode_args),
                        [blank],
                    )
                return respond(
                    target,
                    _action(WorkflowActionType.COLLECT_PROJECT_INPUT, "the ad needs a creative brief or products"),
                    [blank],
                )
            if script_plan_state == ArtifactStatus.MISSING.value:
                if episode_source:
                    preprocessor = workflow_rule(str(mode), str(generation_mode)).preprocessor
                    return respond(
                        target,
                        _action(
                            WorkflowActionType.PREPARE_SCRIPT_PLAN,
                            "the episode has neither a script plan nor a formal script",
                            args={**episode_args, "preprocessor": preprocessor},
                        ),
                        [blank],
                    )
                return respond(
                    target,
                    _action(
                        WorkflowActionType.START_BLANK_SCRIPT,
                        "the episode has no episode source to plan from",
                        args=episode_args,
                    ),
                    [
                        _action(
                            WorkflowActionType.PROVIDE_EPISODE_SOURCE,
                            "episode source enables AI script planning",
                            args=episode_args,
                        )
                    ],
                )
            if script_plan_state == ArtifactStatus.BLOCKED.value:
                return respond(target, _action(WorkflowActionType.NONE, "formal script_plan currency is blocked"))
            return respond(
                target,
                _action(
                    WorkflowActionType.CONFIRM_SCRIPT_PLAN,
                    "formal script_plan awaits content review"
                    if review_pending
                    else "confirming the script_plan materializes the missing final script",
                    args=episode_args,
                    requires_confirmation=True,
                ),
            )
        if review_pending:
            return respond(
                target,
                _action(
                    WorkflowActionType.CONFIRM_SCRIPT_PLAN,
                    "formal script_plan awaits content review",
                    args=episode_args,
                    requires_confirmation=True,
                ),
            )
        if not items:
            return respond(
                target,
                _action(WorkflowActionType.ADD_SCRIPT_ITEMS, "the formal script is empty", args=episode_args),
            )
        if pending_ids:
            return respond(
                target,
                _action(
                    WorkflowActionType.AUTHOR_PROMPTS,
                    "script entries still need prompts",
                    args=episode_args,
                    ids=pending_ids,
                ),
            )
        if replan_ids:
            return respond(
                target,
                _action(
                    WorkflowActionType.REPAIR_VIDEO_UNITS,
                    "video units need replanning before generation",
                    args=episode_args,
                    ids=replan_ids,
                ),
            )
        if missing_sheets:
            return respond(
                target,
                _action(
                    WorkflowActionType.GENERATE_ASSET_SHEETS,
                    "assets referenced by this episode need sheets",
                    args=episode_args,
                    ids=missing_sheets,
                ),
            )
        if artifacts["storyboards"].get("state") == "blocked" or artifacts["videos"].get("state") == "blocked":
            return respond(target, _action(WorkflowActionType.NONE, "media artifact currency is blocked"))
        if generation_mode == "storyboard" and artifacts["storyboards"]["missing_ids"]:
            return respond(
                target,
                _action(
                    WorkflowActionType.GENERATE_GRID if grid else WorkflowActionType.GENERATE_STORYBOARDS,
                    "storyboard images are missing",
                    args=episode_args,
                    ids=artifacts["storyboards"]["missing_ids"],
                ),
            )
        if artifacts["videos"]["missing_ids"]:
            return respond(
                target,
                _action(
                    WorkflowActionType.GENERATE_VIDEOS,
                    "video clips are missing",
                    args=episode_args,
                    ids=artifacts["videos"]["missing_ids"],
                ),
            )
        if timeline_ids is None:
            return respond(target, _action(WorkflowActionType.NONE, "edit timelines cannot be read"))
        if not timeline_ids:
            return respond(
                target,
                _action(WorkflowActionType.CREATE_EDIT_TIMELINE, "episode has no edit timeline", args=episode_args),
            )
        return respond(target, _action(WorkflowActionType.NONE, EPISODE_COMPLETE_REASON))

    @staticmethod
    def _response(
        project: dict[str, Any],
        shared: _SharedWorkflowFacts,
        *,
        target: WorkflowTarget | None,
        content: WorkflowContent | None,
        operations: Mapping[str, OperationAdmission],
        gates: dict[str, dict[str, Any]],
        artifacts: dict[str, dict[str, Any]],
        next_action: WorkflowNextAction,
        next_alternatives: list[WorkflowNextAction] | None = None,
        issues: list[WorkflowBlocker] | None = None,
    ) -> WorkflowStatus:
        source = shared.source
        return WorkflowStatus(
            project_revision=_project_revision(project),
            source_revision=source.revision if source is not None else None,
            project=WorkflowProject(
                content_mode=str(project.get("content_mode")),
                generation_mode=str(project.get("generation_mode")),
                grid_storyboard=project.get("grid_storyboard") is True,
            ),
            target=target,
            blockers=list(shared.blockers),
            issues=list(shared.issues) if issues is None else issues,
            content=content,
            operations={
                str(operation): WorkflowOperation(state=admission.state, reason=admission.reason)
                for operation, admission in operations.items()
            },
            gates=gates,
            artifacts=artifacts,
            next_action=next_action,
            next_alternatives=next_alternatives or [],
        )

    @classmethod
    def _migration_blocked_status(cls, project: Mapping[str, Any], failure: MigrationFailureRecord) -> WorkflowStatus:
        """Report the failure instead of a state derived from unmigrated data.

        Deriving the real state would mean walking the very inputs the migration
        already refused, so a second failure would replace the explanation the
        user needs with a 500. The project stays readable through the project
        and script endpoints; only the production status short-circuits.
        """

        return WorkflowStatus(
            project_revision=_project_revision(project),
            source_revision=None,
            project=WorkflowProject(
                content_mode=str(project.get("content_mode")),
                generation_mode=str(project.get("generation_mode")),
                grid_storyboard=project.get("grid_storyboard") is True,
            ),
            target=None,
            blockers=[migration_blocker(failure)],
            content=None,
            gates={},
            artifacts={
                "asset_sheets": {},
                "script_plan": {"state": "missing"},
                "script": {"state": "missing"},
                "storyboards": _empty_collection(),
                "videos": _empty_collection(),
                "audio": _empty_collection(),
            },
            next_action=migration_next_action(failure),
        )


def migration_blocker(failure: MigrationFailureRecord) -> WorkflowBlocker:
    """The one blocker a project whose migration failed reports everywhere."""

    return WorkflowBlocker(code=MIGRATION_FAILURE_CODE, path=MIGRATION_FAILURE_FILENAME, reason=failure.reason)


def migration_next_action(failure: MigrationFailureRecord) -> WorkflowNextAction:
    """Repair the reported inputs, then rerun the chain — the only way forward."""

    return _action(
        WorkflowActionType.RETRY_PROJECT_MIGRATION,
        failure.reason,
        args={"details": [detail.model_dump(mode="json") for detail in failure.details]},
    )


__all__ = [
    "ALL_EPISODES_COMPLETE_REASON",
    "EPISODE_COMPLETE_REASON",
    "INVALID_EDIT_TIMELINES_CODE",
    "ArtifactCount",
    "EpisodeNextStep",
    "EpisodeSummary",
    "EpisodesSummary",
    "ProjectSummary",
    "WorkflowActionType",
    "WorkflowBlocker",
    "WorkflowNextAction",
    "WorkflowProject",
    "WorkflowRequestError",
    "WorkflowStateService",
    "WorkflowStatus",
    "WorkflowTarget",
    "is_episode_complete",
    "migration_blocker",
    "migration_next_action",
    "planning_docs",
    "workflow_finished",
]
