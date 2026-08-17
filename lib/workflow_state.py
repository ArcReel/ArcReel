"""Authoritative, side-effect-free workflow status for ArcReel projects."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib import script_review
from lib.artifact_activation import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.data_validator import DataValidator
from lib.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    SourceDoc,
    compute_source_fingerprints,
    episodes_without_source_range,
    mismatched_source_fingerprints,
    normalize_source_text,
    parse_positive_episode_num,
)
from lib.path_safety import safe_exists
from lib.project_manager import ProjectManager
from lib.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MIGRATION_FAILURE_FILENAME,
    MigrationFailureRecord,
    load_migration_failure,
)
from lib.project_schema import project_schema_is_current
from lib.script_models import get_generated_assets
from lib.script_skeleton import SKELETONS, STORYBOARD_ITEM_ID_PATTERN, ensure_route_skeleton
from lib.source_revision import SourceRevisionResult, SourceScope, compute_source_revision
from lib.version_manager import VersionManager
from lib.workflow_rules import workflow_rule

WorkflowStateName = Literal[
    "PROJECT_INPUT",
    "SELLING_POINTS",
    "ASSET_INVENTORY",
    "EPISODE_PLAN",
    "STEP1_CONTENT",
    "STEP1_REVIEW",
    "FINAL_SCRIPT",
    "ASSET_SHEETS",
    "STORYBOARD",
    "VIDEO",
    "EXPORT_READY",
]


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

    三个来源合成同一份取值：本模块按编排阶段给出的动作、``lib.workflow_plan`` 投影时
    额外注入的动作，以及批量准入被拒时原样交回的 ``lib.generation_result.GenerationAction``。
    消费方（前端联合类型、profile 受控动作表、动作译文）一律从本枚举派生，新增成员即
    自动进入各处覆盖检查，不必再手抄一份清单。
    """

    # 本模块按编排阶段给出的动作
    NONE = "none"
    COLLECT_PROJECT_INPUT = "collect_project_input"
    DRAFT_SELLING_POINTS = "draft_selling_points"
    ANALYZE_ASSETS = "analyze_assets"
    PLAN_EPISODES = "plan_episodes"
    RESET_EPISODE_PLANNING = "reset_episode_planning"
    PREPARE_STEP1 = "prepare_step1"
    CONFIRM_STEP1 = "confirm_step1"
    GENERATE_SCRIPT = "generate_script"
    GENERATE_ASSET_SHEETS = "generate_asset_sheets"
    GENERATE_STORYBOARDS = "generate_storyboards"
    GENERATE_GRID = "generate_grid"
    REPAIR_VIDEO_UNITS = "repair_video_units"
    GENERATE_VIDEOS = "generate_videos"
    EXPORT = "export"

    # 数据升级失败的项目在任何阶段都只报这一个动作
    RETRY_PROJECT_MIGRATION = "retry_project_migration"

    # ``build_workflow_plan`` 投影时注入的动作
    PATCH_EPISODE_SCRIPT = "patch_episode_script"
    CHOOSE_NARRATION_DELIVERY = "choose_narration_delivery"

    # ``GenerationAction`` 闭集；批量准入与任务失败把它原样交回成 next_action
    RETRY = "retry"
    RESUME = "resume"
    FIX_INPUT = "fix_input"
    GENERATE_DEPENDENCY = "generate_dependency"
    GENERATE_TTS = "generate_tts"
    REGENERATE_TTS = "regenerate_tts"
    WAIT_FOR_TASK = "wait_for_task"
    REPLAN_UNIT = "replan_unit"
    CONFIRM_REQUEST_DURATION = "confirm_request_duration"
    CONFIGURE_PROVIDER = "configure_provider"
    REPAIR_ARTIFACT_STATE = "repair_artifact_state"


class WorkflowNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WorkflowActionType
    args: dict[str, Any] = Field(default_factory=dict)
    requested_ids: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    reason: str


class WorkflowStatus(BaseModel):
    """Shared response model serialized unchanged by REST and MCP adapters."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    project_revision: str
    source_revision: str | None
    project: WorkflowProject
    target: WorkflowTarget | None
    state: WorkflowStateName
    blockers: list[WorkflowBlocker]
    gates: dict[str, dict[str, Any]]
    artifacts: dict[str, dict[str, Any]]
    next_action: WorkflowNextAction


@dataclass(frozen=True)
class _SharedWorkflowFacts:
    source: SourceRevisionResult | None
    planning_sources: tuple[SourceDoc, ...]
    planning_complete: bool
    inventory: dict[str, Any]
    sheets: dict[str, dict[str, Any]]
    episodes: list[tuple[int, dict[str, Any]]]
    currency: ArtifactCurrencyResolver | None
    blockers: tuple[WorkflowBlocker, ...]


def _project_revision(project: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(project), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256-v1:{hashlib.sha256(encoded).hexdigest()}"


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


def planning_docs(source: SourceRevisionResult | None) -> tuple[SourceDoc, ...]:
    """把修订号计算那一次读取的原文转成账本坐标系里的源文档。

    源文在一次状态查询里只读一遍：``compute_source_revision`` 已经把每份源文读进内存，
    分集排布所需的归一化全文由那一次读取派生，不再回磁盘重读。
    """

    if source is None or source.blockers:
        return ()
    return tuple(
        SourceDoc(rel_path=f"source/{document.name}", text=normalize_source_text(document.text))
        for document in source.documents
    )


def _planning_fingerprints_diverged(project: Mapping[str, Any], sources: tuple[SourceDoc, ...]) -> bool:
    recorded = project.get(SOURCE_FINGERPRINTS_KEY)
    if not isinstance(recorded, Mapping) or not recorded:
        return False
    return bool(mismatched_source_fingerprints(recorded, list(sources)))


def _new_source_precedes_cursor(project: Mapping[str, Any], sources: tuple[SourceDoc, ...]) -> bool:
    recorded = project.get(SOURCE_FINGERPRINTS_KEY)
    cursor = project.get("planning_cursor")
    if not isinstance(recorded, Mapping) or not isinstance(cursor, Mapping):
        return False
    cursor_file = cursor.get("source_file")
    if not isinstance(cursor_file, str):
        return False
    canonical_cursor = unicodedata.normalize("NFC", cursor_file)
    canonical_recorded = {
        unicodedata.normalize("NFC", recorded_path) for recorded_path in recorded if isinstance(recorded_path, str)
    }
    cursor_indexes = [
        index
        for index, source in enumerate(sources)
        if unicodedata.normalize("NFC", source.rel_path) == canonical_cursor
    ]
    if len(cursor_indexes) != 1:
        return False
    return any(
        unicodedata.normalize("NFC", source.rel_path) not in canonical_recorded
        for source in sources[: cursor_indexes[0] + 1]
    )


def _empty_collection() -> dict[str, list[str]]:
    return {"current_ids": [], "missing_ids": [], "stale_ids": []}


def _not_applicable_collection() -> dict[str, Any]:
    return {"state": "not_applicable", **_empty_collection()}


class WorkflowStateService:
    """Calculate the first unmet workflow condition from durable project facts."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    @staticmethod
    def _artifact_state(
        resolver: ArtifactCurrencyResolver,
        key: ArtifactKey,
        artifact_path: str,
        blockers: list[WorkflowBlocker],
    ) -> str:
        try:
            comparison = resolver.compare(key, artifact_path=artifact_path)
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError) as exc:
            blockers.append(
                WorkflowBlocker(
                    code="artifact_currency_unavailable",
                    path=artifact_path,
                    reason=str(exc),
                )
            )
            return ArtifactStatus.BLOCKED.value
        if comparison.status is ArtifactStatus.BLOCKED:
            assert comparison.blocker is not None
            blockers.append(
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
        resolver: ArtifactCurrencyResolver,
        key: ArtifactKey,
        artifact_path: str,
        resource_id: str,
        blockers: list[WorkflowBlocker],
        missing_fallback: Callable[[], bool] | None = None,
    ) -> None:
        state = cls._artifact_state(resolver, key, artifact_path, blockers)
        if state == ArtifactStatus.MISSING.value and missing_fallback is not None:
            try:
                if missing_fallback():
                    state = ArtifactStatus.CURRENT.value
            except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError) as exc:
                blockers.append(
                    WorkflowBlocker(
                        code="artifact_currency_unavailable",
                        path=artifact_path,
                        reason=str(exc),
                    )
                )
                state = ArtifactStatus.BLOCKED.value
        if state == ArtifactStatus.BLOCKED.value:
            collection["state"] = "blocked"
        else:
            collection[f"{state}_ids"].append(resource_id)

    def _source_inventory(
        self,
        project_path: Path,
        project: dict[str, Any],
        mode: str,
        blockers: list[WorkflowBlocker],
    ) -> tuple[SourceRevisionResult | None, dict[str, Any]]:
        if mode == "ad":
            return None, {"state": "not_applicable"}

        source = compute_source_revision(project_path, project, SourceScope(kind="all"))
        blockers.extend(WorkflowBlocker(code=item.code, path=item.path, reason=item.reason) for item in source.blockers)
        marker: object = None
        workflow = project.get("workflow")
        if workflow is not None and not isinstance(workflow, Mapping):
            blockers.append(
                WorkflowBlocker(
                    code="invalid_workflow",
                    path="workflow",
                    reason="workflow must be an object",
                )
            )
        elif isinstance(workflow, Mapping):
            marker = workflow.get("asset_inventory")

        artifact: dict[str, Any] = {"state": "missing"}
        if marker is None:
            return source, artifact
        if not isinstance(marker, Mapping):
            blockers.append(
                WorkflowBlocker(
                    code="invalid_asset_inventory",
                    path="workflow.asset_inventory",
                    reason="asset inventory marker must be an object",
                )
            )
            return source, {"state": "blocked"}
        try:
            recorded_scope = SourceScope.model_validate(marker.get("scope"))
        except ValueError as exc:
            blockers.append(
                WorkflowBlocker(
                    code="invalid_source_scope",
                    path="workflow.asset_inventory.scope",
                    reason=str(exc),
                )
            )
            return source, {"state": "blocked"}

        artifact["recorded_scope"] = recorded_scope.model_dump(mode="json")
        artifact["recorded_revision"] = marker.get("source_revision")
        if recorded_scope.kind != "all":
            artifact["state"] = "partial"
            return source, artifact
        if source.blockers:
            artifact["state"] = "blocked"
        elif marker.get("source_revision") == source.revision:
            artifact["state"] = "current"
        else:
            artifact["state"] = "stale"
        return source, artifact

    def _asset_sheets(
        self,
        project_path: Path,
        project: dict[str, Any],
        blockers: list[WorkflowBlocker],
        resolver: ArtifactCurrencyResolver | None,
    ) -> dict[str, dict[str, Any]]:
        collections: dict[str, dict[str, Any]] = {}
        for asset_type, spec in ASSET_SPECS.items():
            collection: dict[str, Any] = _empty_collection()
            bucket = project.get(spec.bucket_key, {})
            if not isinstance(bucket, Mapping):
                blockers.append(
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
                    blockers.append(
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
                        blockers=blockers,
                    )
                elif project_schema_is_current(project):
                    collection["missing_ids"].append(name)
                elif isinstance(path, str) and safe_exists(project_path, path):
                    collection["current_ids"].append(name)
                else:
                    collection["missing_ids"].append(name)
            collections[asset_type] = collection
        return collections

    @staticmethod
    def _episodes(project: dict[str, Any], blockers: list[WorkflowBlocker]) -> list[tuple[int, dict[str, Any]]]:
        raw = project.get("episodes")
        if not isinstance(raw, list):
            blockers.append(
                WorkflowBlocker(code="invalid_episode_ledger", path="episodes", reason="episodes must be an array")
            )
            return []
        parsed: list[tuple[int, dict[str, Any]]] = []
        seen: set[int] = set()
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_episode_entry",
                        path=f"episodes[{index}]",
                        reason="episode entry must be an object",
                    )
                )
                continue
            number = parse_positive_episode_num(entry.get("episode"))
            if number is None or number in seen:
                blockers.append(
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
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_ledger_status",
                        path=f"episodes[{index}].ledger_status",
                        reason="ledger_status must be a string",
                    )
                )
                continue
            parsed.append((number, entry))
        parsed.sort(key=lambda pair: pair[0])
        return parsed

    @staticmethod
    def _target(
        mode: str,
        episodes: list[tuple[int, dict[str, Any]]],
        requested_episode: int | None,
    ) -> tuple[int, dict[str, Any]] | None:
        if mode == "ad":
            return next((pair for pair in episodes if pair[0] == 1), (1, {}))
        if requested_episode is not None:
            return next((pair for pair in episodes if pair[0] == requested_episode), None)
        pending = [pair for pair in episodes if pair[1].get("ledger_status") in {"planned", "stale"}]
        return (pending or episodes)[0] if (pending or episodes) else None

    @staticmethod
    def _planning_action(project: dict[str, Any], reason: str) -> WorkflowNextAction:
        if episodes_without_source_range(project):
            return _action(
                WorkflowActionType.RESET_EPISODE_PLANNING,
                "episode ledger lacks source range records",
                args={"from_episode": 1},
            )
        return _action(WorkflowActionType.PLAN_EPISODES, reason)

    @staticmethod
    def _planning_complete(
        project: dict[str, Any],
        source: SourceRevisionResult | None,
        planning_sources: tuple[SourceDoc, ...],
    ) -> bool:
        """判定源文是否已全部排布完。

        源文只来自 ``planning_sources``——本次请求已经读过一遍的那份，不再回磁盘取。
        """

        if source is None or not source.files:
            return False
        recorded_fingerprints = project.get(SOURCE_FINGERPRINTS_KEY)
        if not isinstance(recorded_fingerprints, Mapping) or not recorded_fingerprints:
            return False
        current_fingerprints = compute_source_fingerprints(list(planning_sources))
        if dict(recorded_fingerprints) != current_fingerprints:
            return False
        cursor = project.get("planning_cursor")
        if not isinstance(cursor, Mapping):
            return False
        rel = cursor.get("source_file")
        offset = cursor.get("offset")
        canonical_rel = unicodedata.normalize("NFC", rel) if isinstance(rel, str) else None
        if canonical_rel != source.files[-1] or not isinstance(offset, int) or isinstance(offset, bool):
            return False
        matching_docs = [doc for doc in planning_sources if unicodedata.normalize("NFC", doc.rel_path) == canonical_rel]
        return len(matching_docs) == 1 and offset >= len(matching_docs[0].text)

    def _load_script_artifacts(
        self,
        project_path: Path,
        project_name: str,
        project: dict[str, Any],
        target: WorkflowTarget,
        blockers: list[WorkflowBlocker],
        resolver: ArtifactCurrencyResolver | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None, dict[str, Any]]:
        path = target.script
        state = ArtifactStatus.CURRENT.value
        if resolver is not None:
            state = self._artifact_state(resolver, ArtifactKey.episode_script(target.episode), path, blockers)
            if state not in {ArtifactStatus.CURRENT.value, ArtifactStatus.STALE.value}:
                return {"state": state, "path": path}, [], None, {}
        try:
            script: Any = self.pm.load_script_readonly(project_name, path)
        except FileNotFoundError:
            return {"state": "missing", "path": path}, [], None, {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(WorkflowBlocker(code="invalid_script", path=path, reason=str(exc)))
            return {"state": "blocked", "path": path}, [], None, {}
        if not isinstance(script, dict):
            blockers.append(WorkflowBlocker(code="invalid_script", path=path, reason="script must be an object"))
            return {"state": "blocked", "path": path}, [], None, {}
        script_episode = script.get("episode")
        if script_episode != target.episode or isinstance(script_episode, bool):
            blockers.append(
                WorkflowBlocker(
                    code="script_episode_mismatch",
                    path=f"{path}.episode",
                    reason=f"script episode must equal target episode {target.episode}",
                )
            )
            return {"state": "blocked", "path": path}, [], None, script
        try:
            kind = ensure_route_skeleton(script, project.get("content_mode"), project.get("generation_mode"))
        except ValueError as exc:
            blockers.append(WorkflowBlocker(code="invalid_project_mode", path="content_mode", reason=str(exc)))
            return {"state": "blocked", "path": path}, [], None, script
        raw_items = script.get(kind)
        if not isinstance(raw_items, list) or not raw_items or not all(isinstance(item, dict) for item in raw_items):
            blockers.append(
                WorkflowBlocker(
                    code="invalid_script_collection",
                    path=f"{path}.{kind}",
                    reason=f"{kind} must be a non-empty array of objects",
                )
            )
            return {"state": "blocked", "path": path}, [], kind, script
        id_field = SKELETONS[kind].id_field
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_items):
            resource_id = item.get(id_field)
            if not isinstance(resource_id, str) or not resource_id:
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"{id_field} must be a non-empty string",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind, script
            if kind != "video_units" and STORYBOARD_ITEM_ID_PATTERN.fullmatch(resource_id) is None:
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"invalid {id_field}: {resource_id}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind, script
            if resource_id in seen_ids:
                blockers.append(
                    WorkflowBlocker(
                        code="duplicate_script_id",
                        path=f"{path}.{kind}[{index}].{id_field}",
                        reason=f"duplicate {id_field}: {resource_id}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind, script
            seen_ids.add(resource_id)
            duration = item.get("duration_seconds")
            duration_max = 300 if kind == "video_units" else 60
            replan_shell = (
                kind == "video_units" and item.get("needs_replan") is True and item.get("shots") == [] and duration == 0
            )
            if (
                duration is not None
                and not replan_shell
                and (isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= duration_max)
            ):
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_script_structure",
                        path=f"{path}.{kind}[{index}].duration_seconds",
                        reason=f"duration_seconds must be an integer between 1 and {duration_max}",
                    )
                )
                return {"state": "blocked", "path": path}, [], kind, script
        validation = DataValidator(str(self.pm.projects_root)).validate_episode_payload(
            project_path,
            project,
            script,
            validate_artifacts=False,
        )
        if not validation.valid:
            blockers.append(
                WorkflowBlocker(
                    code="invalid_script_structure",
                    path=path,
                    reason="; ".join(validation.errors),
                )
            )
            return {"state": "blocked", "path": path}, [], kind, script
        return {"state": state, "path": path}, raw_items, kind, script

    @classmethod
    def _media_collection(
        cls,
        project_path: Path,
        items: list[dict[str, Any]],
        kind: str | None,
        field: str,
        *,
        episode: int,
        resolver: ArtifactCurrencyResolver | None,
        blockers: list[WorkflowBlocker],
        manual_video_versions: VersionManager | None = None,
        manual_video_resource_type: str | None = None,
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
                missing_fallback: Callable[[], bool] | None = None
                if (
                    field == "video_clip"
                    and manual_video_versions is not None
                    and manual_video_resource_type is not None
                ):
                    missing_fallback = partial(
                        manual_video_versions.selected_manual_upload_matches_current_file,
                        manual_video_resource_type,
                        resource_id,
                        artifact_path,
                    )
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
                    blockers=blockers,
                    missing_fallback=missing_fallback,
                )
            elif resolver is not None:
                collection["missing_ids"].append(resource_id)
            elif isinstance(artifact_path, str) and safe_exists(project_path, artifact_path):
                collection["current_ids"].append(resource_id)
            else:
                collection["missing_ids"].append(resource_id)
        return collection

    def get_status(self, project_name: str, episode: int | None = None) -> WorkflowStatus:
        project = self.pm.load_project_readonly(project_name)
        project_path = self.pm.get_project_path(project_name)
        failure = load_migration_failure(project_path)
        if failure is not None:
            return self._migration_blocked_status(project, failure)
        shared = self._shared_facts(project_path, project)
        return self._get_status(project_name, project, project_path, episode, shared)

    def _shared_facts(self, project_path: Path, project: dict[str, Any]) -> _SharedWorkflowFacts:
        mode = project.get("content_mode")
        generation_mode = project.get("generation_mode")
        blockers: list[WorkflowBlocker] = []
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
            blockers.append(
                WorkflowBlocker(
                    code="invalid_grid_storyboard",
                    path="grid_storyboard",
                    reason="grid_storyboard must be a boolean",
                )
            )
        if mode == "ad":
            target_duration = project.get("target_duration")
            if not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_target_duration",
                        path="target_duration",
                        reason="ad target_duration must be a positive integer",
                    )
                )
            if grid_storyboard is True:
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_grid_storyboard",
                        path="grid_storyboard",
                        reason="ad workflow does not support grid storyboards",
                    )
                )
        currency: ArtifactCurrencyResolver | None = None
        if project_schema_is_current(project):
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
        asset_validation = DataValidator(str(self.pm.projects_root)).validate_asset_definitions(project)
        if not asset_validation.valid:
            blockers.append(
                WorkflowBlocker(
                    code="invalid_asset_definitions",
                    path="project.json",
                    reason="; ".join(asset_validation.errors),
                )
            )
        source, inventory = self._source_inventory(project_path, project, str(mode), blockers)
        planning_sources = planning_docs(source) if mode != "ad" else ()
        planning_complete = self._planning_complete(project, source, planning_sources)
        sheets = self._asset_sheets(project_path, project, blockers, currency)
        episodes = self._episodes(project, blockers)
        return _SharedWorkflowFacts(
            source=source,
            planning_sources=planning_sources,
            planning_complete=planning_complete,
            inventory=inventory,
            sheets=sheets,
            episodes=episodes,
            currency=currency,
            blockers=tuple(blockers),
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
        generation_mode = project.get("generation_mode")
        grid = project.get("grid_storyboard") is True and generation_mode == "storyboard"
        blockers = list(shared.blockers)
        source = shared.source
        inventory = shared.inventory
        sheets = shared.sheets
        currency = shared.currency
        artifacts: dict[str, dict[str, Any]] = {
            "asset_inventory": inventory,
            "asset_sheets": sheets,
            "step1": {"state": "not_applicable" if mode == "ad" else "missing"},
            "script": {"state": "missing"},
            "storyboards": _empty_collection(),
            "videos": _empty_collection(),
            "audio": _empty_collection(),
        }
        gates: dict[str, dict[str, Any]] = {
            "step1_review": {"state": "not_applicable" if mode == "ad" else "pending", "revision": None}
        }
        episodes = shared.episodes
        selected = self._target(str(mode), episodes, episode)
        target = None
        if selected is not None:
            number, entry = selected
            script_path = entry.get("script_file")
            if not isinstance(script_path, str) or not script_path:
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_script_binding",
                        path=f"episodes.{number}.script_file",
                        reason="script_file must be a non-empty string",
                    )
                )
            else:
                script_filename = ProjectManager.normalize_script_filename(script_path)
                if "/" in script_filename or "\\" in script_filename:
                    blockers.append(
                        WorkflowBlocker(
                            code="invalid_script_path",
                            path=f"episodes.{number}.script_file",
                            reason="script_file must resolve to a bare filename under scripts/",
                        )
                    )
                target = WorkflowTarget(
                    episode=number,
                    script=script_path,
                    script_filename=script_filename,
                    source=f"source/episode_{number}.txt",
                )

        state: WorkflowStateName
        next_action: WorkflowNextAction
        if blockers:
            state = "PROJECT_INPUT"
            next_action = _action(WorkflowActionType.NONE, "workflow is blocked")
        elif mode != "ad" and (source is None or not source.files):
            state = "PROJECT_INPUT"
            next_action = _action(WorkflowActionType.COLLECT_PROJECT_INPUT, "source text is required")
        elif mode != "ad" and not any(doc.text.strip() for doc in shared.planning_sources):
            state = "PROJECT_INPUT"
            next_action = _action(WorkflowActionType.COLLECT_PROJECT_INPUT, "non-blank source text is required")
        elif mode != "ad" and inventory.get("state") != "current":
            state = "ASSET_INVENTORY"
            next_action = _action(
                WorkflowActionType.ANALYZE_ASSETS,
                "asset inventory is missing or out of date",
                args={
                    "scope": {"kind": "all", "files": []},
                    "expected_source_revision": source.revision if source else None,
                },
            )
        elif mode != "ad" and _planning_fingerprints_diverged(project, shared.planning_sources):
            state = "EPISODE_PLAN"
            next_action = _action(
                WorkflowActionType.RESET_EPISODE_PLANNING,
                "source files changed after episode planning",
                args={"from_episode": 1},
            )
        elif mode != "ad" and _new_source_precedes_cursor(project, shared.planning_sources):
            state = "EPISODE_PLAN"
            next_action = _action(
                WorkflowActionType.RESET_EPISODE_PLANNING,
                "new source text precedes the current planning cursor",
                args={"from_episode": 1},
            )
        elif mode != "ad" and selected is None:
            state = "EPISODE_PLAN"
            if episode is not None and shared.planning_complete:
                blockers.append(
                    WorkflowBlocker(
                        code="episode_unavailable",
                        path=f"episodes.{episode}",
                        reason="requested episode is absent and all source text is already planned",
                    )
                )
                next_action = _action(WorkflowActionType.NONE, "requested episode is unavailable")
            else:
                next_action = self._planning_action(project, "episode ledger has no target episode")
        else:
            if target is None:  # defensive; ad always supplies episode 1
                state = "EPISODE_PLAN"
                next_action = self._planning_action(project, "target episode is unavailable")
            else:
                preprocessor = workflow_rule(str(mode), str(generation_mode)).preprocessor
                if mode != "ad" and selected is not None and selected[1].get("ledger_status") == "stale":
                    step1_path = script_review.step1_path(project_path, project, target.episode)
                    live_revision = script_review.content_fingerprint(step1_path) if step1_path is not None else None
                    stale_entry = selected[1]
                    baseline_is_recorded = script_review.STALE_STEP1_REVISION_FIELD in stale_entry
                    stale_revision = stale_entry.get(script_review.STALE_STEP1_REVISION_FIELD)
                    rebuilt_revision = stale_entry.get(script_review.STALE_STEP1_REBUILT_REVISION_FIELD)
                    if not baseline_is_recorded:
                        artifacts["step1"] = {"state": "stale"}
                        state = "EPISODE_PLAN"
                        next_action = _action(
                            WorkflowActionType.RESET_EPISODE_PLANNING,
                            "legacy stale episode has no rebuild baseline",
                            args={"from_episode": target.episode},
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                    if live_revision is None or (
                        baseline_is_recorded and live_revision == stale_revision and rebuilt_revision != live_revision
                    ):
                        artifacts["step1"] = {"state": "stale"}
                        state = "STEP1_CONTENT"
                        next_action = _action(
                            WorkflowActionType.PREPARE_STEP1,
                            "target episode was replanned and its downstream artifacts are stale",
                            args={
                                "episode": target.episode,
                                "preprocessor": preprocessor,
                                "expected_stale_step1_revision": stale_revision,
                            },
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                if mode == "ad":
                    products = project.get("products", {})
                    pending_points = (
                        [
                            name
                            for name, item in products.items()
                            if isinstance(item, Mapping) and not item.get("selling_points")
                        ]
                        if isinstance(products, Mapping)
                        else []
                    )
                    if pending_points:
                        state = "SELLING_POINTS"
                        next_action = _action(
                            WorkflowActionType.DRAFT_SELLING_POINTS, "products need selling points", ids=pending_points
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                else:
                    step1_path = script_review.step1_path(project_path, project, target.episode)
                    revision = script_review.content_fingerprint(step1_path) if step1_path is not None else None
                    step1_state = ArtifactStatus.CURRENT.value if revision is not None else ArtifactStatus.MISSING.value
                    if currency is not None and step1_path is not None:
                        step1_state = self._artifact_state(
                            currency,
                            ArtifactKey.episode_step1(target.episode),
                            step1_path.relative_to(project_path).as_posix(),
                            blockers,
                        )
                    artifacts["step1"] = {
                        "state": step1_state,
                        "path": str(step1_path.relative_to(project_path)) if step1_path is not None else None,
                        "revision": revision,
                    }
                    if script_review.step1_quarantined(project_path, project, target.episode):
                        quarantine = script_review.step1_quarantine_path(project_path, project, target.episode)
                        assert quarantine is not None
                        artifacts["step1"]["state"] = "blocked"
                        blockers.append(
                            WorkflowBlocker(
                                code="step1_quarantined",
                                path=str(quarantine.relative_to(project_path)),
                                reason="step1 has a quarantined draft that must be repaired and promoted",
                            )
                        )
                        state = "STEP1_REVIEW"
                        next_action = _action(
                            WorkflowActionType.NONE, "quarantined step1 must be repaired before confirmation"
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                    if artifacts["step1"]["state"] == "blocked":
                        state = "STEP1_CONTENT"
                        next_action = _action(WorkflowActionType.NONE, "formal step1 currency is blocked")
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                    if artifacts["step1"]["state"] == "missing":
                        state = "STEP1_CONTENT"
                        next_action = _action(
                            WorkflowActionType.PREPARE_STEP1,
                            "target episode has no formal step1",
                            args={"episode": target.episode, "preprocessor": preprocessor},
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                    review = script_review.review_status(project_path, project, target.episode)
                    if (
                        selected is not None
                        and selected[1].get("ledger_status") == "stale"
                        and script_review.stored_review(project, target.episode).get("fingerprint") is None
                    ):
                        review = "pending_review"
                    gates["step1_review"] = {
                        "state": "confirmed" if review == "confirmed" else "pending",
                        "revision": revision,
                    }
                    if review != "confirmed":
                        state = "STEP1_REVIEW"
                        next_action = _action(
                            WorkflowActionType.CONFIRM_STEP1,
                            "formal step1 awaits content review",
                            args={"episode": target.episode},
                            requires_confirmation=True,
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)

                script_artifact, items, kind, script = self._load_script_artifacts(
                    project_path, project_name, project, target, blockers, currency
                )
                artifacts["script"] = script_artifact
                if (
                    currency is None
                    and mode != "ad"
                    and script_artifact["state"] == "current"
                    and script_review.stored_review(project, target.episode).get("fingerprint") is not None
                ):
                    metadata = script.get("metadata")
                    generated_from = (
                        metadata.get(script_review.SCRIPT_STEP1_REVISION_FIELD)
                        if isinstance(metadata, Mapping)
                        else None
                    )
                    if generated_from != artifacts["step1"].get("revision"):
                        artifacts["script"]["state"] = "stale"
                if blockers:
                    state = "FINAL_SCRIPT"
                    next_action = _action(WorkflowActionType.NONE, "script is blocked")
                elif script_artifact["state"] == "missing" or (
                    currency is None and script_artifact["state"] == "stale"
                ):
                    state = "FINAL_SCRIPT"
                    next_action = _action(
                        WorkflowActionType.GENERATE_SCRIPT,
                        "target episode has no current final script",
                        args={"episode": target.episode},
                    )
                else:
                    missing_sheets = [
                        asset_id
                        for asset_type, collection in sheets.items()
                        if asset_type != "product"
                        for asset_id in collection.get("missing_ids", [])
                    ]
                    if missing_sheets:
                        state = "ASSET_SHEETS"
                        next_action = _action(
                            WorkflowActionType.GENERATE_ASSET_SHEETS,
                            "asset definitions need sheets",
                            ids=missing_sheets,
                        )
                    else:
                        artifacts["storyboards"] = (
                            self._media_collection(
                                project_path,
                                items,
                                kind,
                                "storyboard_image",
                                episode=target.episode,
                                resolver=currency,
                                blockers=blockers,
                            )
                            if generation_mode == "storyboard"
                            else _not_applicable_collection()
                        )
                        artifacts["videos"] = self._media_collection(
                            project_path,
                            items,
                            kind,
                            "video_clip",
                            episode=target.episode,
                            resolver=currency,
                            blockers=blockers,
                            manual_video_versions=VersionManager(project_path) if currency is not None else None,
                            manual_video_resource_type=(
                                "reference_videos" if generation_mode == "reference_video" else "videos"
                            ),
                        )
                        # 旁白 TTS 只作为信息报告，不参与状态推进：缺 TTS 既不是工作流缺口
                        # 也不拦导出，补 TTS 由用户显式发起（见 generate_narration_audio），
                        # 后期路线的旁白根本不需要 TTS。Manifest 读不出某条 TTS 状态时同理——
                        # 传独立的 audio_blockers 而非共享 blockers，不让它触发下面
                        # ``if blockers`` 把状态钉在 VIDEO；不可读事实仍经
                        # ``artifacts["audio"]["state"] == "blocked"`` 报告，只是不拦进度。
                        audio_blockers: list[WorkflowBlocker] = []
                        artifacts["audio"] = (
                            self._media_collection(
                                project_path,
                                items,
                                kind,
                                "narration_audio",
                                episode=target.episode,
                                resolver=currency,
                                blockers=audio_blockers,
                            )
                            if mode == "narration" and generation_mode == "storyboard"
                            else _not_applicable_collection()
                        )
                        if blockers:
                            state = "VIDEO"
                            next_action = _action(WorkflowActionType.NONE, "video metadata is blocked")
                        elif generation_mode == "storyboard" and artifacts["storyboards"]["missing_ids"]:
                            missing = artifacts["storyboards"]["missing_ids"]
                            state = "STORYBOARD"
                            next_action = _action(
                                WorkflowActionType.GENERATE_GRID if grid else WorkflowActionType.GENERATE_STORYBOARDS,
                                "storyboard images are missing",
                                args={"episode": target.episode},
                                ids=missing,
                            )
                        elif replan_ids := [
                            str(item.get(SKELETONS[kind].id_field))
                            for item in items
                            if kind == "video_units" and item.get("needs_replan") is True
                        ]:
                            state = "VIDEO"
                            next_action = _action(
                                WorkflowActionType.REPAIR_VIDEO_UNITS,
                                "video units need replanning before generation",
                                args={"episode": target.episode},
                                ids=replan_ids,
                            )
                        elif artifacts["videos"]["missing_ids"]:
                            missing = artifacts["videos"]["missing_ids"]
                            state = "VIDEO"
                            next_action = _action(
                                WorkflowActionType.GENERATE_VIDEOS,
                                "video clips are missing",
                                args={"episode": target.episode},
                                ids=missing,
                            )
                        elif episode is None and mode != "ad":
                            later_status = next(
                                (
                                    status
                                    for number, _entry in episodes
                                    if number != target.episode
                                    and (
                                        status := self._get_status(project_name, project, project_path, number, shared)
                                    ).state
                                    != "EXPORT_READY"
                                    and not (
                                        status.state == "EPISODE_PLAN" and status.next_action.type == "plan_episodes"
                                    )
                                ),
                                None,
                            )
                            if later_status is not None:
                                return later_status
                            if not shared.planning_complete:
                                state = "EPISODE_PLAN"
                                next_action = self._planning_action(project, "source text remains unplanned")
                            else:
                                state = "EXPORT_READY"
                                next_action = _action(WorkflowActionType.EXPORT, "all required artifacts are usable")
                        elif mode != "ad" and not shared.planning_complete:
                            state = "EPISODE_PLAN"
                            next_action = self._planning_action(project, "source text remains unplanned")
                        else:
                            state = "EXPORT_READY"
                            next_action = _action(WorkflowActionType.EXPORT, "all required artifacts are usable")

        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)

    @staticmethod
    def _response(
        project: dict[str, Any],
        source: SourceRevisionResult | None,
        target: WorkflowTarget | None,
        state: WorkflowStateName,
        blockers: list[WorkflowBlocker],
        gates: dict[str, dict[str, Any]],
        artifacts: dict[str, dict[str, Any]],
        next_action: WorkflowNextAction,
    ) -> WorkflowStatus:
        return WorkflowStatus(
            project_revision=_project_revision(project),
            source_revision=source.revision if source is not None else None,
            project=WorkflowProject(
                content_mode=str(project.get("content_mode")),
                generation_mode=str(project.get("generation_mode")),
                grid_storyboard=project.get("grid_storyboard") is True,
            ),
            target=target,
            state=state,
            blockers=blockers,
            gates=gates,
            artifacts=artifacts,
            next_action=next_action,
        )

    @classmethod
    def _migration_blocked_status(cls, project: Mapping[str, Any], failure: MigrationFailureRecord) -> WorkflowStatus:
        """Report the failure instead of a state derived from unmigrated data.

        Deriving the real state would mean walking the very inputs the migration
        already refused, so a second failure would replace the explanation the
        user needs with a 500. The project stays readable through the project
        and script endpoints; only the production status short-circuits.
        """

        return cls._response(
            dict(project),
            None,
            None,
            "PROJECT_INPUT",
            [migration_blocker(failure)],
            {},
            {
                "asset_inventory": {},
                "asset_sheets": {},
                "step1": {"state": "missing"},
                "script": {"state": "missing"},
                "storyboards": _empty_collection(),
                "videos": _empty_collection(),
                "audio": _empty_collection(),
            },
            migration_next_action(failure),
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
    "WorkflowActionType",
    "WorkflowBlocker",
    "WorkflowNextAction",
    "WorkflowProject",
    "WorkflowRequestError",
    "WorkflowStateService",
    "WorkflowStatus",
    "WorkflowTarget",
    "migration_blocker",
    "migration_next_action",
    "planning_docs",
]
