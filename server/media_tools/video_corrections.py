"""Scoped correction history for the existing director, with inspected-media evidence."""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from lib.artifacts.version_manager import VersionManager
from lib.final_cut.basis import version_snapshot
from lib.infra.async_thread import run_sync_transaction
from lib.infra.content_digest import canonical_json_digest, sha256_file
from lib.infra.path_safety import safe_join
from lib.project.project_manager import is_reference_video_project
from lib.video_review.corrections import (
    CorrectionScope,
    Evidence,
    MediaReviewVerification,
    Outcome,
    Sha256,
    correction_attempts,
    get_correction_attempt,
    matching_verified_lessons,
    record_correction_outcome,
    related_verified_lessons,
    reserve_correction,
    retire_verified_lesson,
)
from lib.video_review.receipts import verify_frame_receipt
from server.media_tools.context import tool_problem
from server.media_tools.video_versions import project_video_unit_ids
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CorrectionContext(_RequestModel):
    provider: str = Field(min_length=1, max_length=160, description="本次报价中的视频供应商 ID")
    model: str = Field(min_length=1, max_length=160, description="本次报价中的视频模型 ID")
    workflow: str = Field(min_length=1, max_length=500, description="稳定的拍摄方式/问题类别，不放单次台词")
    reference_paths: list[str] = Field(
        default_factory=list, max_length=32, description="项目内参考图片/声音相对路径，按模型输入顺序"
    )

    def resolve(self, project_path: Path) -> CorrectionScope:
        hashes: list[str] = []
        for relative in self.reference_paths:
            # Memory cannot be used to hash credentials or arbitrary project text.
            path = safe_join(project_path, relative, require_file=True)
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a", ".flac"}:
                raise ValueError("Correction references must be image or audio assets")
            hashes.append(sha256_file(path))
        return CorrectionScope(
            provider=self.provider,
            model=self.model,
            reference_hashes=tuple(hashes),
            context_hash=canonical_json_digest({"workflow_version": 1, "workflow": self.workflow.strip()}),
        )


class LookupCorrections(_RequestModel):
    action: Literal["lookup"]
    context: CorrectionContext
    unit_id: str | None = Field(default=None, max_length=160, description="可选；同时返回这个单元的保留尝试记录")


class ReserveCorrection(_RequestModel):
    action: Literal["reserve"]
    context: CorrectionContext
    unit_id: str = Field(min_length=1, max_length=160, description="要修正的视频单元")
    baseline_version: StrictInt = Field(ge=1, description="修正前已经检查的视频版本")
    baseline_receipt: Sha256 = Field(description="inspect_video_units 返回的 review_receipt")
    input_description: str = Field(min_length=1, max_length=16000, description="本次实际提示词及设置，用来识别原样重试")
    correction: str = Field(min_length=1, max_length=2000, description="一次有针对性的改动及预期效果")


class ResolveCorrection(_RequestModel):
    action: Literal["resolve"]
    attempt_id: str = Field(min_length=1, max_length=160, description="reserve 返回的尝试 ID")
    outcome: Outcome = Field(description="failed / unverified / verified；verified 仅代表有证据的画面审阅通过")
    candidate_version: StrictInt | None = Field(default=None, ge=1, description="新生成的视频版本；生成本身失败可省略")
    before_evidence: Evidence = Field(description="原版问题及时间点")
    after_evidence: Evidence = Field(description="新版结果及时间点；不确定时标 unverified")
    before_receipt: Sha256 | None = Field(default=None, description="原版 review_receipt；verified 必填")
    after_receipt: Sha256 | None = Field(default=None, description="新版 review_receipt；verified 必填")


class RetireCorrection(_RequestModel):
    action: Literal["retire"]
    attempt_id: str = Field(min_length=1, max_length=160, description="不再适用或被用户否决的经验 ID；保留历史")


class VideoCorrectionMemoryRequest(_RequestModel):
    command: Annotated[
        LookupCorrections | ReserveCorrection | ResolveCorrection | RetireCorrection,
        Field(discriminator="action", description="查询经验、预留修正次数、记录结果或停用经验"),
    ]


def _snapshot(project_path: Path, resource_type: str, unit_id: str, version: int) -> Path:
    found = version_snapshot(project_path, VersionManager(project_path), resource_type, unit_id, version)
    if found is None:
        raise ValueError("Video version is unavailable; inspect the saved footage first")
    return found[0]


def _execute(request: VideoCorrectionMemoryRequest, scope: ProjectScope, services: Services) -> dict[str, Any]:
    project = services.projects.load_project(scope.project_name)
    project_path = services.projects.get_project_path(scope.project_name)
    known = project_video_unit_ids(services.projects, scope.project_name, project)
    resource_type = "reference_videos" if is_reference_video_project(project) else "videos"
    command = request.command
    unit_id = command.unit_id if isinstance(command, (LookupCorrections, ReserveCorrection)) else None
    if unit_id is not None and unit_id not in known:
        raise ValueError("Video unit does not belong to this project")
    if isinstance(command, LookupCorrections):
        context = command.context.resolve(project_path)
        lessons = matching_verified_lessons(project_path, scope=context)
        related = (
            related_verified_lessons(project_path, scope=context, limit=3 - len(lessons)) if len(lessons) < 3 else ()
        )
        return {
            "lessons": [lesson.model_dump(mode="json") for lesson in lessons],
            "related_lessons": [
                {
                    "lesson": lesson.model_dump(mode="json"),
                    "applicability": "unverified_for_current_context",
                    "changed_scope": [
                        label
                        for label, changed in (
                            ("workflow", lesson.attempt.scope.context_hash != context.context_hash),
                            ("references", lesson.attempt.scope.reference_hashes != context.reference_hashes),
                        )
                        if changed
                    ],
                }
                for lesson in related
            ],
            "attempts": [
                attempt.model_dump(mode="json")
                for attempt in correction_attempts(project_path, unit_id=command.unit_id)
            ]
            if command.unit_id
            else [],
            "verification_scope": "sampled_frames_only; audio and artistic approval are unverified",
        }
    if isinstance(command, ReserveCorrection):
        verify_frame_receipt(
            project_path,
            command.baseline_receipt,
            unit_id=command.unit_id,
            version=command.baseline_version,
            video=_snapshot(project_path, resource_type, command.unit_id, command.baseline_version),
        )
        versions = VersionManager(project_path).get_versions(resource_type, command.unit_id).get("versions", [])
        latest_version = max((record["version"] for record in versions), default=command.baseline_version)
        attempt = reserve_correction(
            project_path,
            unit_id=command.unit_id,
            baseline_version=command.baseline_version,
            scope=command.context.resolve(project_path),
            input_hash=canonical_json_digest(command.input_description.strip()),
            correction=command.correction,
            baseline_max_version=latest_version,
        )
        return {"attempt": attempt.model_dump(mode="json")}
    original = get_correction_attempt(project_path, attempt_id=command.attempt_id)
    if original.unit_id not in known:
        raise ValueError("Video unit does not belong to this project")
    if isinstance(command, RetireCorrection):
        return {"retired": retire_verified_lesson(project_path, attempt_id=command.attempt_id)}
    proof = None
    if command.candidate_version is not None:
        _snapshot(project_path, resource_type, original.unit_id, command.candidate_version)
    if command.outcome == "verified":
        if not command.before_receipt or not command.after_receipt or command.candidate_version is None:
            raise ValueError("Verified corrections require inspection receipts for both versions")
        before = verify_frame_receipt(
            project_path,
            command.before_receipt,
            unit_id=original.unit_id,
            version=original.baseline_version,
            video=_snapshot(project_path, resource_type, original.unit_id, original.baseline_version),
        )
        after = verify_frame_receipt(
            project_path,
            command.after_receipt,
            unit_id=original.unit_id,
            version=command.candidate_version,
            video=_snapshot(project_path, resource_type, original.unit_id, command.candidate_version),
        )
        if before.content_hash == after.content_hash:
            raise ValueError("An unchanged video cannot prove a correction")
        found = version_snapshot(
            project_path, VersionManager(project_path), resource_type, original.unit_id, command.candidate_version
        )
        if found is None:
            raise ValueError("Candidate video version is unavailable")
        record = found[1]
        if (
            not record.get("execution_task_id")
            or not record.get("execution_request_digest")
            or record.get("execution_provider_id") != original.scope.provider
            or record.get("execution_provider_model_id") != original.scope.model
        ):
            raise ValueError("Candidate generation provenance does not match the reserved provider/model")
        proof = MediaReviewVerification(
            passed=True,
            original_version=original.baseline_version,
            candidate_version=command.candidate_version,
            before_review_hash=command.before_receipt,
            after_review_hash=command.after_receipt,
        )
    attempt = record_correction_outcome(
        project_path,
        attempt_id=command.attempt_id,
        outcome=command.outcome,
        candidate_version=command.candidate_version,
        before_evidence=command.before_evidence,
        after_evidence=command.after_evidence,
        verification=proof,
    )
    return {"attempt": attempt.model_dump(mode="json")}


async def video_correction_memory(
    request: ToolRequest[VideoCorrectionMemoryRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[dict[str, Any]]:
    try:
        return ToolOutcome(value=await run_sync_transaction(_execute, request.value, scope, services))
    except ValueError as exc:
        return tool_problem(str(exc), code="correction_refused")
    except OSError:
        return tool_problem(
            "Correction history or inspected media is unavailable; nothing was regenerated",
            code="correction_evidence_unavailable",
        )
