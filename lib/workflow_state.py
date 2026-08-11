"""Authoritative, side-effect-free workflow status for ArcReel projects."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib import script_review
from lib.asset_types import ASSET_SPECS
from lib.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    compute_source_fingerprints,
    discover_sources,
    normalize_source_text,
    parse_positive_episode_num,
)
from lib.episode_paths import episode_script_relpath
from lib.path_safety import safe_exists
from lib.project_manager import ProjectManager
from lib.reference_video.ad_units import ad_stale_unit_ids
from lib.script_models import get_generated_assets
from lib.script_skeleton import SKELETONS, ensure_route_skeleton
from lib.source_revision import SourceRevisionResult, SourceScope, compute_source_revision

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
    "AUDIO",
    "EXPORT_READY",
]


class WorkflowProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_mode: str
    generation_mode: str
    grid_storyboard: bool


class WorkflowTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode: int
    script: str
    source: str


class WorkflowBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    reason: str


class WorkflowNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
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
    inventory: dict[str, Any]
    sheets: dict[str, dict[str, Any]]
    episodes: list[tuple[int, dict[str, Any]]]
    blockers: tuple[WorkflowBlocker, ...]


def _project_revision(project: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(project), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256-v1:{hashlib.sha256(encoded).hexdigest()}"


def _action(
    action_type: str, reason: str, *, args: dict[str, Any] | None = None, ids: list[str] | None = None
) -> WorkflowNextAction:
    return WorkflowNextAction(
        type=action_type,
        args=args or {},
        requested_ids=ids or [],
        reason=reason,
    )


def _empty_collection() -> dict[str, list[str]]:
    return {"current_ids": [], "missing_ids": [], "stale_ids": []}


def _not_applicable_collection() -> dict[str, Any]:
    return {"state": "not_applicable", **_empty_collection()}


class WorkflowStateService:
    """Calculate the first unmet workflow condition from durable project facts."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

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
                if isinstance(path, str) and safe_exists(project_path, path):
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
    def _planning_complete(project_path: Path, project: dict[str, Any], source: SourceRevisionResult | None) -> bool:
        if source is None or not source.files:
            return False
        recorded_fingerprints = project.get(SOURCE_FINGERPRINTS_KEY)
        if isinstance(recorded_fingerprints, Mapping) and recorded_fingerprints:
            current_fingerprints = compute_source_fingerprints(discover_sources(project_path))
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
        try:
            source_dir = project_path / "source"
            if source_dir.is_symlink():
                return False
            matching_paths = [
                path
                for path in source_dir.iterdir()
                if unicodedata.normalize("NFC", f"source/{path.name}") == canonical_rel
            ]
            if len(matching_paths) != 1 or matching_paths[0].is_symlink():
                return False
            path = matching_paths[0]
            path.resolve(strict=True).relative_to(project_path.resolve())
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return False
        return offset >= len(normalize_source_text(text))

    def _load_script_artifacts(
        self,
        project_path: Path,
        project_name: str,
        project: dict[str, Any],
        target: WorkflowTarget,
        blockers: list[WorkflowBlocker],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None, dict[str, Any]]:
        path = target.script
        try:
            script: Any = self.pm.load_script(project_name, path)
        except FileNotFoundError:
            return {"state": "missing", "path": path}, [], None, {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(WorkflowBlocker(code="invalid_script", path=path, reason=str(exc)))
            return {"state": "blocked", "path": path}, [], None, {}
        if not isinstance(script, dict):
            blockers.append(WorkflowBlocker(code="invalid_script", path=path, reason="script must be an object"))
            return {"state": "blocked", "path": path}, [], None, {}
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
        return {"state": "current", "path": path}, raw_items, kind, script

    @staticmethod
    def _ad_reference_videos(
        project_path: Path,
        script: dict[str, Any],
        blockers: list[WorkflowBlocker],
    ) -> dict[str, Any]:
        collection: dict[str, Any] = _empty_collection()
        raw_units = script.get("reference_units")
        if raw_units is None or raw_units == []:
            return {"state": "missing", **collection}
        if not isinstance(raw_units, list):
            blockers.append(
                WorkflowBlocker(
                    code="invalid_reference_units",
                    path="reference_units",
                    reason="reference_units must be an array",
                )
            )
            return {"state": "blocked", **collection}

        stale_ids = set(ad_stale_unit_ids(script, raw_units))
        invalid = False
        for index, unit in enumerate(raw_units):
            if not isinstance(unit, dict) or not isinstance(unit.get("unit_id"), str) or not unit["unit_id"]:
                blockers.append(
                    WorkflowBlocker(
                        code="invalid_reference_unit",
                        path=f"reference_units[{index}]",
                        reason="reference units must be named objects",
                    )
                )
                invalid = True
                continue
            unit_id = unit["unit_id"]
            video_path = get_generated_assets(unit).get("video_clip")
            if not isinstance(video_path, str) or not safe_exists(project_path, video_path):
                collection["missing_ids"].append(unit_id)
            elif unit_id in stale_ids:
                collection["stale_ids"].append(unit_id)
            else:
                collection["current_ids"].append(unit_id)

        if invalid:
            state = "blocked"
        elif collection["stale_ids"]:
            state = "stale"
        elif collection["missing_ids"] or not collection["current_ids"]:
            state = "missing"
        else:
            state = "current"
        return {"state": state, **collection}

    @staticmethod
    def _media_collection(
        project_path: Path,
        items: list[dict[str, Any]],
        kind: str | None,
        field: str,
    ) -> dict[str, list[str]]:
        collection = _empty_collection()
        if kind is None:
            return collection
        id_field = SKELETONS[kind].id_field
        for item in items:
            resource_id = item.get(id_field)
            if not isinstance(resource_id, str) or not resource_id:
                continue
            artifact_path = get_generated_assets(item).get(field)
            if isinstance(artifact_path, str) and safe_exists(project_path, artifact_path):
                collection["current_ids"].append(resource_id)
            else:
                collection["missing_ids"].append(resource_id)
        return collection

    def get_status(self, project_name: str, episode: int | None = None) -> WorkflowStatus:
        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        shared = self._shared_facts(project_path, project)
        return self._get_status(project_name, project, project_path, episode, shared)

    def _shared_facts(self, project_path: Path, project: dict[str, Any]) -> _SharedWorkflowFacts:
        mode = project.get("content_mode")
        generation_mode = project.get("generation_mode")
        blockers: list[WorkflowBlocker] = []
        if mode not in {"narration", "drama", "ad"}:
            blockers.append(
                WorkflowBlocker(code="invalid_content_mode", path="content_mode", reason="unsupported mode")
            )
        if generation_mode not in {"storyboard", "reference_video"}:
            blockers.append(
                WorkflowBlocker(code="invalid_generation_mode", path="generation_mode", reason="unsupported route")
            )
        source, inventory = self._source_inventory(project_path, project, str(mode), blockers)
        sheets = self._asset_sheets(project_path, project, blockers)
        episodes = self._episodes(project, blockers)
        return _SharedWorkflowFacts(
            source=source,
            inventory=inventory,
            sheets=sheets,
            episodes=episodes,
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
            raise ValueError("episode must be a positive integer")
        if mode == "ad" and episode not in {None, 1}:
            raise ValueError("ad workflow only has episode 1")
        generation_mode = project.get("generation_mode")
        grid = bool(project.get("grid_storyboard")) and generation_mode == "storyboard"
        blockers = list(shared.blockers)
        source = shared.source
        inventory = shared.inventory
        sheets = shared.sheets
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
                script_path = episode_script_relpath(number)
            target = WorkflowTarget(
                episode=number,
                script=script_path,
                source=f"source/episode_{number}.txt",
            )

        state: WorkflowStateName
        next_action: WorkflowNextAction
        if blockers:
            state = "PROJECT_INPUT"
            next_action = _action("none", "workflow is blocked")
        elif mode != "ad" and (source is None or not source.files):
            state = "PROJECT_INPUT"
            next_action = _action("collect_project_input", "source text is required")
        elif mode != "ad" and inventory.get("state") != "current":
            state = "ASSET_INVENTORY"
            next_action = _action(
                "analyze_assets",
                "asset inventory is missing or out of date",
                args={"scope": {"kind": "all", "files": []}, "source_revision": source.revision if source else None},
            )
        elif mode != "ad" and selected is None:
            state = "EPISODE_PLAN"
            next_action = _action("plan_episodes", "episode ledger has no target episode")
        else:
            if target is None:  # defensive; ad always supplies episode 1
                state = "EPISODE_PLAN"
                next_action = _action("plan_episodes", "target episode is unavailable")
            else:
                preprocessor = (
                    "split-reference-video-units"
                    if generation_mode == "reference_video"
                    else "split-narration-segments"
                    if mode == "narration"
                    else "normalize-drama-script"
                )
                if mode != "ad" and selected is not None and selected[1].get("ledger_status") == "stale":
                    artifacts["step1"] = {"state": "stale"}
                    state = "STEP1_CONTENT"
                    next_action = _action(
                        "prepare_step1",
                        "target episode was replanned and its downstream artifacts are stale",
                        args={"episode": target.episode, "preprocessor": preprocessor},
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
                            "draft_selling_points", "products need selling points", ids=pending_points
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                else:
                    step1_path = script_review.step1_path(project_path, project, target.episode)
                    revision = script_review.content_fingerprint(step1_path) if step1_path is not None else None
                    artifacts["step1"] = {
                        "state": "current" if revision is not None else "missing",
                        "path": str(step1_path.relative_to(project_path)) if step1_path is not None else None,
                        "revision": revision,
                    }
                    if revision is None:
                        state = "STEP1_CONTENT"
                        next_action = _action(
                            "prepare_step1",
                            "target episode has no formal step1",
                            args={"episode": target.episode, "preprocessor": preprocessor},
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)
                    review = script_review.review_status(project_path, project, target.episode)
                    gates["step1_review"] = {
                        "state": "confirmed" if review == "confirmed" else "pending",
                        "revision": revision,
                    }
                    if review != "confirmed":
                        state = "STEP1_REVIEW"
                        next_action = _action(
                            "confirm_step1",
                            "formal step1 awaits content review",
                            args={"episode": target.episode},
                        )
                        return self._response(project, source, target, state, blockers, gates, artifacts, next_action)

                script_artifact, items, kind, script = self._load_script_artifacts(
                    project_path, project_name, project, target, blockers
                )
                artifacts["script"] = script_artifact
                if blockers:
                    state = "FINAL_SCRIPT"
                    next_action = _action("none", "script is blocked")
                elif script_artifact["state"] == "missing":
                    state = "FINAL_SCRIPT"
                    next_action = _action(
                        "generate_script", "target episode has no final script", args={"episode": target.episode}
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
                            "generate_asset_sheets", "asset definitions need sheets", ids=missing_sheets
                        )
                    else:
                        artifacts["storyboards"] = (
                            self._media_collection(project_path, items, kind, "storyboard_image")
                            if generation_mode == "storyboard"
                            else _not_applicable_collection()
                        )
                        artifacts["videos"] = (
                            self._ad_reference_videos(project_path, script, blockers)
                            if mode == "ad" and generation_mode == "reference_video"
                            else self._media_collection(project_path, items, kind, "video_clip")
                        )
                        artifacts["audio"] = (
                            self._media_collection(project_path, items, kind, "narration_audio")
                            if mode == "narration" and generation_mode == "storyboard"
                            else _not_applicable_collection()
                        )
                        if blockers:
                            state = "VIDEO"
                            next_action = _action("none", "video metadata is blocked")
                        elif generation_mode == "storyboard" and artifacts["storyboards"]["missing_ids"]:
                            missing = artifacts["storyboards"]["missing_ids"]
                            state = "STORYBOARD"
                            next_action = _action(
                                "generate_grid" if grid else "generate_storyboards",
                                "storyboard images are missing",
                                args={"episode": target.episode},
                                ids=missing,
                            )
                        elif (
                            artifacts["videos"]["missing_ids"]
                            or artifacts["videos"]["stale_ids"]
                            or (
                                mode == "ad"
                                and generation_mode == "reference_video"
                                and artifacts["videos"].get("state") != "current"
                            )
                        ):
                            missing = artifacts["videos"]["missing_ids"] + artifacts["videos"]["stale_ids"]
                            state = "VIDEO"
                            next_action = _action(
                                "generate_videos",
                                "video clips are missing",
                                args={"episode": target.episode},
                                ids=missing,
                            )
                        elif (
                            mode == "narration"
                            and generation_mode == "storyboard"
                            and artifacts["audio"]["missing_ids"]
                        ):
                            missing = artifacts["audio"]["missing_ids"]
                            state = "AUDIO"
                            next_action = _action(
                                "generate_narration_audio",
                                "narration audio is missing",
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
                                    not in {"EPISODE_PLAN", "EXPORT_READY"}
                                ),
                                None,
                            )
                            if later_status is not None:
                                return later_status
                            if not self._planning_complete(project_path, project, source):
                                state = "EPISODE_PLAN"
                                next_action = _action("plan_episodes", "source text remains unplanned")
                            else:
                                state = "EXPORT_READY"
                                next_action = _action("export", "all required artifacts are usable")
                        elif mode != "ad" and not self._planning_complete(project_path, project, source):
                            state = "EPISODE_PLAN"
                            next_action = _action("plan_episodes", "source text remains unplanned")
                        else:
                            state = "EXPORT_READY"
                            next_action = _action("export", "all required artifacts are usable")

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
                grid_storyboard=bool(project.get("grid_storyboard")),
            ),
            target=target,
            state=state,
            blockers=blockers,
            gates=gates,
            artifacts=artifacts,
            next_action=next_action,
        )


__all__ = [
    "WorkflowBlocker",
    "WorkflowNextAction",
    "WorkflowProject",
    "WorkflowStateService",
    "WorkflowStatus",
    "WorkflowTarget",
]
