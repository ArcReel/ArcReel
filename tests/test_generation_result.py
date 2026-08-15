"""Contract tests for the shared per-ID generation selection/result module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lib.artifact_manifest import (
    ArtifactBlocker,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifestError,
    ArtifactStatus,
)
from lib.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationCandidate,
    GenerationItemResult,
    GenerationItemState,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    GenerationTaskState,
    ProviderCheckpoint,
    artifact_is_reusable,
    observe_artifact_status,
    problem_from_task_failure,
    provider_checkpoint_from_task,
    render_generation_result,
    select_generation_targets,
)
from lib.task_failure import encode_failure

pytestmark = pytest.mark.unit


class _Resolver:
    """A Manifest double driven by a per-key status table."""

    def __init__(self, statuses: dict[str, ArtifactStatus], *, raises: set[str] | None = None) -> None:
        self._statuses = statuses
        self._raises = raises or set()

    def compare(self, key: ArtifactKey, *, artifact_path: str | None = None) -> ArtifactComparison:
        unit = key.components[-1]
        if unit in self._raises:
            raise ArtifactManifestError(f"sidecar for {unit} is unreadable")
        return ArtifactComparison(status=self._statuses[unit], artifact_path=artifact_path or "")


def _candidate(unit_id: str, *, path: str | None = "videos/x.mp4") -> GenerationCandidate:
    return GenerationCandidate(
        unit_id=unit_id,
        artifact_key=ArtifactKey.episode_video(1, unit_id),
        artifact_path=path,
    )


# --- selection -------------------------------------------------------------


def test_missing_only_selects_missing_and_reuses_stale() -> None:
    """stale 可用即复用：只有 missing 进 targets，stale 与 current 都进 skipped。"""

    resolver = _Resolver(
        {
            "A": ArtifactStatus.MISSING,
            "B": ArtifactStatus.STALE,
            "C": ArtifactStatus.CURRENT,
        }
    )

    selection = select_generation_targets(
        candidates=[_candidate("A"), _candidate("B"), _candidate("C")],
        requested_ids=None,
        resolver=resolver,  # type: ignore[arg-type]
    )

    assert selection.mode is GenerationSelectionMode.MISSING_ONLY
    assert selection.target_ids == ("A",)
    assert [state.unit_id for state in selection.skipped] == ["B", "C"]
    assert selection.unavailable == ()


def test_missing_only_never_regenerates_a_blocked_artifact() -> None:
    """产物状态读不出来时报为独立缺口，绝不当作 missing 去重付一次费。"""

    resolver = _Resolver({"A": ArtifactStatus.MISSING}, raises={"B"})

    selection = select_generation_targets(
        candidates=[_candidate("A"), _candidate("B")],
        requested_ids=None,
        resolver=resolver,  # type: ignore[arg-type]
    )

    assert selection.target_ids == ("A",)
    assert [state.unit_id for state in selection.unavailable] == ["B"]

    result = GenerationResultBuilder.from_selection("probe", selection).build()
    assert result.blocked == ["B"]
    problem = result.items[0].problem
    assert problem is not None
    assert problem.code == GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE
    assert problem.action is GenerationAction.REPAIR_ARTIFACT_STATE


def test_explicit_selection_takes_named_ids_regardless_of_state() -> None:
    """点名即强制：current 的 ID 照样进 targets，未命中的 ID 单列为 unmatched。"""

    resolver = _Resolver({"A": ArtifactStatus.CURRENT, "B": ArtifactStatus.MISSING})

    selection = select_generation_targets(
        candidates=[_candidate("A"), _candidate("B")],
        requested_ids=["A", "ZZ"],
        resolver=resolver,  # type: ignore[arg-type]
    )

    assert selection.mode is GenerationSelectionMode.EXPLICIT
    assert selection.target_ids == ("A",)
    assert selection.unmatched_ids == ("ZZ",)
    assert selection.skipped == ()


def test_explicit_empty_list_is_an_empty_selection_not_everything() -> None:
    selection = select_generation_targets(
        candidates=[_candidate("A")],
        requested_ids=[],
        resolver=None,
    )

    assert selection.mode is GenerationSelectionMode.EXPLICIT
    assert selection.target_ids == ()


def test_missing_only_without_manifest_falls_back_to_path_presence() -> None:
    """Manifest 未激活时产物状态不可观测，只能按「剧本里有没有登记路径」判缺。"""

    selection = select_generation_targets(
        candidates=[_candidate("A", path=None), _candidate("B")],
        requested_ids=None,
        resolver=None,
    )

    assert selection.target_ids == ("A",)
    assert [state.status for state in selection.skipped] == [None]


def test_observe_artifact_status_separates_unobservable_from_missing() -> None:
    key = ArtifactKey.episode_video(1, "A")

    assert observe_artifact_status(resolver=None, key=key, artifact_path="videos/a.mp4") == (None, None)
    assert observe_artifact_status(resolver=None, key=key, artifact_path=None)[0] is ArtifactStatus.MISSING

    status, blocker = observe_artifact_status(
        resolver=_Resolver({}, raises={"A"}),  # type: ignore[arg-type]
        key=key,
        artifact_path="videos/a.mp4",
    )
    assert status is ArtifactStatus.BLOCKED
    assert isinstance(blocker, ArtifactBlocker)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ArtifactStatus.CURRENT, True),
        (ArtifactStatus.STALE, True),
        (ArtifactStatus.MISSING, False),
        (ArtifactStatus.BLOCKED, False),
    ],
)
def test_artifact_is_reusable_treats_stale_as_usable(status: ArtifactStatus, expected: bool) -> None:
    state = GenerationTargetState(candidate=_candidate("A"), status=status)
    assert artifact_is_reusable(state, manifest_active=True) is expected


# --- result identity -------------------------------------------------------


def test_requested_is_exactly_the_union_of_the_three_outcome_sets() -> None:
    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)
    builder.succeed("A", task_id="t1")
    builder.fail("B", problem=problem_from_task_failure("boom"), task_id="t2")
    builder.block(
        "C",
        problem=GenerationProblem(
            code=GenerationProblemCode.UNIT_NOT_FOUND,
            detail="missing",
            action=GenerationAction.FIX_INPUT,
        ),
    )
    builder.skip_unit("D", artifact_path="videos/d.mp4", artifact_status=ArtifactStatus.STALE)

    result = builder.build()

    assert set(result.requested) == {"A", "B", "C"}
    assert set(result.requested) == set(result.succeeded) | set(result.failed) | set(result.blocked)
    assert not set(result.succeeded) & set(result.failed)
    assert not set(result.succeeded) & set(result.blocked)
    assert not set(result.failed) & set(result.blocked)
    # 复用的单元刻意留在 requested 之外：它既没做也没失败。
    assert [entry.unit_id for entry in result.skipped] == ["D"]
    assert "D" not in result.requested
    assert result.ok is False


def test_a_unit_cannot_be_recorded_twice() -> None:
    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)
    builder.succeed("A")
    with pytest.raises(ValueError, match="already recorded"):
        builder.block(
            "A",
            problem=GenerationProblem(
                code=GenerationProblemCode.UNIT_NOT_FOUND,
                detail="x",
                action=GenerationAction.NONE,
            ),
        )


def test_the_batch_model_rejects_sets_that_do_not_match_their_items() -> None:
    with pytest.raises(ValidationError):
        GenerationBatchResult(
            operation="probe",
            selection=GenerationSelectionMode.EXPLICIT,
            requested=["A", "B"],
            succeeded=["A"],
            failed=[],
            blocked=[],
            items=[GenerationItemResult(unit_id="A", state=GenerationItemState.SUCCEEDED)],
        )


def test_a_failed_item_must_carry_a_problem_and_a_succeeded_one_must_not() -> None:
    with pytest.raises(ValidationError):
        GenerationItemResult(unit_id="A", state=GenerationItemState.FAILED)
    with pytest.raises(ValidationError):
        GenerationItemResult(
            unit_id="A",
            state=GenerationItemState.SUCCEEDED,
            problem=GenerationProblem(
                code=GenerationProblemCode.TASK_FAILED,
                detail="x",
                action=GenerationAction.RETRY,
            ),
        )


def test_the_contract_survives_a_json_round_trip() -> None:
    builder = GenerationResultBuilder("probe", GenerationSelectionMode.MISSING_ONLY)
    builder.succeed("A", task_id="t1", artifact_status=ArtifactStatus.CURRENT)
    result = builder.build()

    assert GenerationBatchResult.model_validate(result.model_dump(mode="json")) == result


# --- three status axes -----------------------------------------------------


def test_a_succeeded_task_can_still_report_a_stale_artifact() -> None:
    """任务成功 ≠ 产物匹配当前依据：两条轴各报各的，不合并。"""

    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)
    builder.succeed(
        "A",
        task_id="t1",
        artifact_path="videos/a.mp4",
        artifact_status=ArtifactStatus.STALE,
        provider_checkpoint=ProviderCheckpoint(submitted=True, provider_id="p", provider_job_id="j"),
    )
    item = builder.build().items[0]

    assert item.state is GenerationItemState.SUCCEEDED
    assert item.task_state is GenerationTaskState.SUCCEEDED
    assert item.artifact_status is ArtifactStatus.STALE
    assert item.provider_checkpoint is not None and item.provider_checkpoint.submitted is True


def test_a_failed_commit_keeps_the_old_artifact_and_never_claims_current() -> None:
    """正式文件落盘 / Manifest 更新失败时该 ID 记为 failed，旧的付费产物原样保留。"""

    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)
    builder.fail(
        "A",
        problem=GenerationProblem(
            code=GenerationProblemCode.POST_PROCESSING_FAILED,
            detail="commit failed after the provider returned the image",
            action=GenerationAction.RETRY,
        ),
        artifact_path="videos/a.mp4",
        artifact_status=ArtifactStatus.STALE,
        task_id="t1",
        task_state=GenerationTaskState.SUCCEEDED,
        provider_checkpoint=ProviderCheckpoint(submitted=True, provider_id="p", provider_job_id="j"),
    )
    item = builder.build().items[0]

    assert item.state is GenerationItemState.FAILED
    # 任务本身跑成功了（钱已花），但产物没有被标成 current。
    assert item.task_state is GenerationTaskState.SUCCEEDED
    assert item.artifact_status is ArtifactStatus.STALE
    assert item.artifact_path == "videos/a.mp4"


def test_provider_checkpoint_is_absent_when_the_task_row_says_nothing() -> None:
    assert provider_checkpoint_from_task(None) is None
    assert provider_checkpoint_from_task({}) is None

    checkpoint = provider_checkpoint_from_task({"provider_id": "vidu", "provider_job_id": "job-1"})
    assert checkpoint == ProviderCheckpoint(submitted=True, provider_id="vidu", provider_job_id="job-1")

    unsubmitted = provider_checkpoint_from_task({"provider_id": "vidu", "provider_job_id": None})
    assert unsubmitted is not None and unsubmitted.submitted is False


# --- problem mapping -------------------------------------------------------


def test_a_registered_failure_code_keeps_its_code_and_gets_a_sharper_action() -> None:
    problem = problem_from_task_failure(encode_failure("reference_duration_confirmation_required"))

    assert problem.code == "reference_duration_confirmation_required"
    assert problem.action is GenerationAction.CONFIRM_REQUEST_DURATION


def test_unparseable_provider_text_keeps_its_text_and_stays_retryable() -> None:
    problem = problem_from_task_failure("HTTP 503 from upstream")

    assert problem.code == GenerationProblemCode.TASK_FAILED
    assert problem.detail == "HTTP 503 from upstream"
    assert problem.action is GenerationAction.RETRY


def test_a_cancelled_task_reports_cancellation_rather_than_failure() -> None:
    problem = problem_from_task_failure("stopped", cancelled=True)

    assert problem.code == GenerationProblemCode.TASK_CANCELLED


# --- rendering -------------------------------------------------------------


def test_the_rendered_text_is_only_a_projection_of_the_payload() -> None:
    builder = GenerationResultBuilder("generate_videos", GenerationSelectionMode.MISSING_ONLY)
    builder.succeed("A", task_id="t1", artifact_path="videos/a.mp4")
    builder.fail("B", problem=problem_from_task_failure("boom"), task_id="t2")
    builder.skip_unit("C", artifact_path="videos/c.mp4", artifact_status=ArtifactStatus.STALE)
    result = builder.build()

    text = render_generation_result(result, log=["注意"])

    assert "generate_videos summary: 1 succeeded, 1 failed, 0 blocked, 1 reused" in text
    assert "注意" in text
    for unit_id in ("A", "B", "C"):
        assert unit_id in text
