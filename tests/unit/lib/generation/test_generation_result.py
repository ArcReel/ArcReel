"""Contract tests for the shared per-ID generation selection/result module."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.artifacts.artifact_manifest import (
    ArtifactBlocker,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifestError,
    ArtifactStatus,
)
from lib.generation.generation_queue_client import BatchTaskResult
from lib.generation.generation_result import (
    _ACTION_LABELS,
    _ARTIFACT_STATUS_LABELS,
    _OPERATION_LABELS,
    _TASK_FAILURE_ACTIONS,
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
    GenerationWarning,
    ProviderCheckpoint,
    artifact_is_reusable,
    normalize_requested_ids,
    observe_artifact_status,
    problem_from_task_failure,
    provider_checkpoint_from_task,
    record_batch_outcomes,
    render_generation_result,
    select_generation_targets,
)
from lib.generation.task_failure import FAILURE_CODE_KEYS, encode_failure


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


def test_missing_only_selects_missing_and_reuses_stale(tmp_path: Path) -> None:
    """Reuse usable stale artifacts: only missing enters targets; stale/current both enter skipped."""

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
        resolver=resolver,
    )

    assert selection.mode is GenerationSelectionMode.MISSING_ONLY
    assert selection.target_ids == ("A",)
    assert [state.unit_id for state in selection.skipped] == ["B", "C"]
    assert selection.unavailable == ()


def test_missing_only_never_regenerates_a_blocked_artifact(tmp_path: Path) -> None:
    """Unreadable states are independent gaps, never missing targets that incur duplicate payment."""

    resolver = _Resolver({"A": ArtifactStatus.MISSING}, raises={"B"})

    selection = select_generation_targets(
        candidates=[_candidate("A"), _candidate("B")],
        requested_ids=None,
        resolver=resolver,
    )

    assert selection.target_ids == ("A",)
    assert [state.unit_id for state in selection.unavailable] == ["B"]

    result = GenerationResultBuilder.from_selection("probe", selection).build()
    assert result.blocked == ["B"]
    problem = result.items[0].problem
    assert problem is not None
    assert problem.code == GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE
    assert problem.action is GenerationAction.REPAIR_ARTIFACT_STATE


def test_explicit_selection_takes_named_ids_regardless_of_state(tmp_path: Path) -> None:
    """Explicit IDs force generation even when current; unmatched IDs are separate."""

    resolver = _Resolver({"A": ArtifactStatus.CURRENT, "B": ArtifactStatus.MISSING})

    selection = select_generation_targets(
        candidates=[_candidate("A"), _candidate("B")],
        requested_ids=["A", "ZZ"],
        resolver=resolver,
    )

    assert selection.mode is GenerationSelectionMode.EXPLICIT
    assert selection.target_ids == ("A",)
    assert selection.unmatched_ids == ("ZZ",)
    assert selection.skipped == ()


def test_explicit_empty_collection_is_invalid_not_everything(tmp_path: Path) -> None:
    """Explicit empty sets are caller errors, neither all nor silently empty batches."""

    with pytest.raises(ValueError, match="不能为空"):
        select_generation_targets(
            candidates=[_candidate("A")],
            requested_ids=[],
            resolver=_Resolver({}),
        )


def test_normalize_requested_ids_is_the_single_gate_for_selection_intent() -> None:
    assert normalize_requested_ids(None, field="names") is None
    assert normalize_requested_ids(["B", "A", "B"], field="names") == ["B", "A"]
    with pytest.raises(ValueError, match="不能为空数组"):
        normalize_requested_ids([], field="names")
    with pytest.raises(ValueError, match="必须是 ID 数组"):
        normalize_requested_ids("A", field="names")


def test_missing_only_reselects_a_recorded_path_the_manifest_no_longer_claims() -> None:
    """Deleted/moved registered files compare MISSING and mark units absent, never permanently reusable."""

    resolver = _Resolver({"GONE": ArtifactStatus.MISSING, "KEPT": ArtifactStatus.CURRENT})

    selection = select_generation_targets(
        candidates=[
            _candidate("GONE", path="videos/gone.mp4"),
            _candidate("KEPT", path="videos/kept.mp4"),
        ],
        requested_ids=None,
        resolver=resolver,
    )

    assert selection.target_ids == ("GONE",)
    assert [state.unit_id for state in selection.skipped] == ["KEPT"]


def test_missing_only_does_not_recheck_the_filesystem(tmp_path: Path) -> None:
    """Trust comparison outcomes only; absent same-named disk files do not change them."""

    resolver = _Resolver({"A": ArtifactStatus.CURRENT, "B": ArtifactStatus.MISSING})

    selection = select_generation_targets(
        candidates=[_candidate("A"), _candidate("B")],
        requested_ids=None,
        resolver=resolver,
    )

    assert selection.target_ids == ("B",)
    assert [state.unit_id for state in selection.skipped] == ["A"]


def test_observe_artifact_status_separates_unobservable_from_missing() -> None:
    key = ArtifactKey.episode_video(1, "A")
    resolver = _Resolver({"A": ArtifactStatus.CURRENT})

    # Units without comparison keys are unobservable on this axis, not missing.
    assert observe_artifact_status(
        resolver=resolver,
        key=None,
        artifact_path="videos/a.mp4",
    ) == (None, None)
    assert (
        observe_artifact_status(
            resolver=resolver,
            key=key,
            artifact_path=None,
        )[0]
        is ArtifactStatus.MISSING
    )

    status, blocker = observe_artifact_status(
        resolver=_Resolver({}, raises={"A"}),
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
def test_artifact_is_reusable_treats_stale_as_usable(status: ArtifactStatus, expected: bool, tmp_path: Path) -> None:
    state = GenerationTargetState(candidate=_candidate("A"), status=status)
    assert artifact_is_reusable(state) is expected


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
    # Reused units stay outside requested because they neither ran nor failed.
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
    """Successful tasks do not imply artifacts match current bases; report both axes separately."""

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
    assert item.provider_checkpoint is not None
    assert item.provider_checkpoint.submitted is True


def test_a_failed_commit_keeps_the_old_artifact_and_never_claims_current() -> None:
    """Formal-write/Manifest-update failures mark IDs failed while retaining old paid artifacts unchanged."""

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
    # Tasks succeeded and money was spent, but artifacts were not marked current.
    assert item.task_state is GenerationTaskState.SUCCEEDED
    assert item.artifact_status is ArtifactStatus.STALE
    assert item.artifact_path == "videos/a.mp4"


def test_provider_checkpoint_is_absent_when_the_task_row_says_nothing() -> None:
    assert provider_checkpoint_from_task(None) is None
    assert provider_checkpoint_from_task({}) is None

    checkpoint = provider_checkpoint_from_task({"provider_id": "vidu", "provider_job_id": "job-1"})
    assert checkpoint == ProviderCheckpoint(submitted=True, provider_id="vidu", provider_job_id="job-1")

    unsubmitted = provider_checkpoint_from_task({"provider_id": "vidu", "provider_job_id": None})
    assert unsubmitted is not None
    assert unsubmitted.submitted is False


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


def test_every_registered_failure_code_resolves_to_a_declared_action() -> None:
    """Action tables share task_failure’s code source; new unregistered failure codes fail here.

    Unregistered codes only degrade to ``retry``, safe but imprecise, so require explicit registration.
    """

    unmapped = sorted(set(FAILURE_CODE_KEYS) - set(_TASK_FAILURE_ACTIONS))
    assert unmapped == [], f"这些 failure code 未在 _TASK_FAILURE_ACTIONS 登记下一步动作: {unmapped}"

    unknown = sorted(set(_TASK_FAILURE_ACTIONS) - set(FAILURE_CODE_KEYS))
    assert unknown == [], f"这些动作映射的 code 已不在 FAILURE_CODE_KEYS 中: {unknown}"


# --- batch recording -------------------------------------------------------


def _batch(resource_id: str, *, status: str = "succeeded", **kwargs: object) -> BatchTaskResult:
    return BatchTaskResult(resource_id=resource_id, task_id=f"task-{resource_id}", status=status, **kwargs)


def test_recording_a_batch_reports_task_provider_and_artifact_axes_separately() -> None:
    """Task success is not artifact currency; truthfully report all three axes."""

    resolver = _Resolver({"A": ArtifactStatus.STALE})
    states = {
        "A": GenerationTargetState(candidate=_candidate("A"), status=ArtifactStatus.MISSING),
    }
    builder = GenerationResultBuilder("probe", GenerationSelectionMode.MISSING_ONLY)

    record_batch_outcomes(
        builder,
        successes=[_batch("A", result={"file_path": "videos/a.mp4"}, task={"provider_job_id": "job-1"})],
        failures=[],
        states=states,
        resolver=resolver,
    )

    item = builder.build().items[0]
    assert item.task_state is GenerationTaskState.SUCCEEDED
    assert item.artifact_status is ArtifactStatus.STALE
    assert item.artifact_path == "videos/a.mp4"
    assert item.provider_checkpoint == ProviderCheckpoint(submitted=True, provider_job_id="job-1")


def test_a_target_left_behind_by_an_interrupted_enqueue_is_reported_as_not_queued() -> None:
    """Partial sequential enqueue reports created-task outcomes and untouched targets as not queued/unbilled, with codes distinct from provider failures."""

    builder = GenerationResultBuilder("probe", GenerationSelectionMode.MISSING_ONLY)

    record_batch_outcomes(
        builder,
        successes=[_batch("A", result={"file_path": "videos/a.mp4"})],
        failures=[
            BatchTaskResult(
                resource_id="B", task_id="", status="failed", error="queue unavailable", enqueue_interrupted=True
            )
        ],
    )

    result = builder.build()
    assert result.succeeded == ["A"]
    assert result.failed == ["B"]
    left_behind = next(item for item in result.items if item.unit_id == "B")
    assert left_behind.problem is not None
    assert left_behind.problem.code == GenerationProblemCode.ENQUEUE_INTERRUPTED
    assert left_behind.task_state is GenerationTaskState.NOT_QUEUED
    assert left_behind.task_id is None


def test_a_succeeded_batch_item_carries_the_worker_warnings() -> None:
    """worker result.warnings enters structured contracts unchanged; Agents/task APIs read identical entries."""

    clamp_warning = {"key": "ref_too_many_images", "params": {"count": 8, "model": "viduq2", "max_count": 7}}
    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)

    record_batch_outcomes(
        builder,
        successes=[_batch("A", result={"file_path": "storyboards/scene_A.png", "warnings": [clamp_warning]})],
        failures=[],
    )

    result = builder.build()
    item = result.items[0]
    assert item.state is GenerationItemState.SUCCEEDED
    assert item.warnings == [GenerationWarning(key="ref_too_many_images", params=clamp_warning["params"])]
    assert GenerationBatchResult.model_validate(result.model_dump(mode="json")) == result


def test_malformed_worker_warnings_are_skipped_not_fatal() -> None:
    """Warnings are advisory; skip malformed entries without failing whole reports."""

    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)

    record_batch_outcomes(
        builder,
        successes=[
            _batch(
                "A",
                result={
                    "file_path": "storyboards/scene_A.png",
                    "warnings": [
                        "not a dict",
                        {"params": {}},
                        {"key": "", "params": {}},
                        {"key": "ref_single_ref_only"},
                    ],
                },
            ),
            _batch("B", result={"file_path": "storyboards/scene_B.png", "warnings": "nope"}),
        ],
        failures=[],
    )

    items = {item.unit_id: item for item in builder.build().items}
    assert items["A"].warnings == [GenerationWarning(key="ref_single_ref_only", params={})]
    assert items["B"].warnings == []


def test_the_rendered_summary_spells_out_item_warnings() -> None:
    """Summaries localize warning copy beneath entries rather than exposing keys."""

    builder = GenerationResultBuilder("generate_storyboards", GenerationSelectionMode.EXPLICIT)
    builder.succeed(
        "E1S02",
        task_id="t1",
        artifact_path="storyboards/scene_E1S02.png",
        warnings=[GenerationWarning(key="ref_too_many_images", params={"count": 8, "model": "viduq2", "max_count": 7})],
    )

    text = render_generation_result(builder.build())

    assert "✓ E1S02 → storyboards/scene_E1S02.png\n    ⚠️ 参考图数量 8 超出 viduq2 上限 7，已取前 7 张" in text
    assert "ref_too_many_images" not in text


def test_a_warning_cannot_carry_params_that_shadow_translate_arguments() -> None:
    """Constructed warnings must render; reject params colliding with translator parameter names."""

    with pytest.raises(ValidationError):
        GenerationWarning(key="ref_too_many_images", params={"key": "x"})
    with pytest.raises(ValidationError):
        GenerationWarning(key="ref_too_many_images", params={"locale": "en"})


def test_worker_warnings_whose_params_shadow_translate_arguments_are_skipped() -> None:
    """Malformed but valid JSON params discard that warning only, retaining others and full reports."""

    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)

    record_batch_outcomes(
        builder,
        successes=[
            _batch(
                "A",
                result={
                    "file_path": "storyboards/scene_A.png",
                    "warnings": [
                        {"key": "ref_too_many_images", "params": {"key": "x", "locale": "en"}},
                        {"key": "ref_single_ref_only", "params": {}},
                    ],
                },
            )
        ],
        failures=[],
    )

    items = {item.unit_id: item for item in builder.build().items}
    assert items["A"].warnings == [GenerationWarning(key="ref_single_ref_only", params={})]


def test_a_failed_batch_item_keeps_the_old_artifact_and_its_status() -> None:
    """Failures preserve old artifacts: reports retain old paths/states and never erase paid results."""

    states = {
        "A": GenerationTargetState(candidate=_candidate("A", path="videos/old.mp4"), status=ArtifactStatus.STALE),
    }
    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)

    record_batch_outcomes(
        builder,
        successes=[],
        failures=[_batch("A", status="cancelled", error="stopped")],
        states=states,
    )

    item = builder.build().items[0]
    assert item.state is GenerationItemState.FAILED
    assert item.task_state is GenerationTaskState.CANCELLED
    assert item.artifact_path == "videos/old.mp4"
    assert item.artifact_status is ArtifactStatus.STALE


def test_a_wait_interrupted_batch_item_is_reported_distinctly_from_a_real_failure() -> None:
    """Interrupted waits (timeout/offline workers) leave tasks nonterminal on workers; report
    INTERRUPTED + WAIT_FOR_TASK, never FAILED + RETRY, which invites duplicate paid submissions
    while original tasks may still run and publish normally."""

    states = {
        "A": GenerationTargetState(candidate=_candidate("A", path="videos/old.mp4"), status=ArtifactStatus.STALE),
    }
    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)

    record_batch_outcomes(
        builder,
        successes=[],
        failures=[_batch("A", status="interrupted", error="timed out waiting for task 't1' after 3600.0s")],
        states=states,
    )

    item = builder.build().items[0]
    assert item.state is GenerationItemState.FAILED
    assert item.task_state is GenerationTaskState.INTERRUPTED
    assert item.problem is not None
    assert item.problem.code == GenerationProblemCode.TASK_INTERRUPTED
    assert item.problem.action == GenerationAction.WAIT_FOR_TASK


def test_a_never_queued_failure_gets_the_enqueue_failed_code_not_task_failed() -> None:
    """``task_id=""`` means enqueue itself failed before queuing. task_state already says NOT_QUEUED;
    use enqueue-specific problem codes, not ``problem_from_task_failure`` default TASK_FAILED.
    Otherwise downstream cannot distinguish never-queued requests from executed provider failures
    or choose correct recovery: enqueue again versus investigate providers."""

    builder = GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT)

    record_batch_outcomes(
        builder,
        successes=[],
        failures=[BatchTaskResult(resource_id="A", task_id="", status="failed", error="queue is down")],
    )

    item = builder.build().items[0]
    assert item.task_state is GenerationTaskState.NOT_QUEUED
    assert item.problem is not None
    assert item.problem.code == GenerationProblemCode.ENQUEUE_FAILED


def test_recording_maps_queue_resource_ids_onto_contract_unit_ids() -> None:
    """Queue resource_id differing from contract IDs (e.g. asset <type>/<name>) uses mapping-based accounting."""

    builder = GenerationResultBuilder("generate_assets", GenerationSelectionMode.MISSING_ONLY)

    record_batch_outcomes(
        builder,
        successes=[_batch("张三")],
        failures=[],
        unit_id_of=lambda name: f"character/{name}",
        fallback_path=lambda name: f"characters/{name}.png",
    )

    item = builder.build().items[0]
    assert item.unit_id == "character/张三"
    assert item.artifact_path == "characters/张三.png"


def test_recording_a_success_with_no_known_state_does_not_crash_under_an_active_manifest() -> None:
    """Missing ``states`` units use default states without artifact_key; artifact axes become unobservable
    (None), never crash batches. Resolver activation cannot change absent-key incomparability;
    crashing would harm other correctly accounted entries."""

    builder = GenerationResultBuilder("generate_assets", GenerationSelectionMode.MISSING_ONLY)

    record_batch_outcomes(
        builder,
        successes=[_batch("张三", result={"file_path": "characters/张三.png"})],
        failures=[],
        states={},  # Missing mapping units make _state() return defaults without artifact_key.
        resolver=_Resolver({}),
    )

    item = builder.build().items[0]
    assert item.artifact_status is None
    assert item.artifact_path == "characters/张三.png"


# --- rendering -------------------------------------------------------------


def test_the_rendered_text_is_only_a_projection_of_the_payload() -> None:
    builder = GenerationResultBuilder("generate_videos", GenerationSelectionMode.MISSING_ONLY)
    builder.succeed("A", task_id="t1", artifact_path="videos/a.mp4")
    builder.fail("B", problem=problem_from_task_failure("boom"), task_id="t2")
    builder.skip_unit("C", artifact_path="videos/c.mp4", artifact_status=ArtifactStatus.STALE)
    result = builder.build()

    text = render_generation_result(result, log=["注意"])

    assert "成功 1 件、失败 1 件、受阻 0 件、复用 1 件" in text
    assert "注意" in text
    for unit_id in ("A", "B", "C"):
        assert unit_id in text


def test_rendered_summary_uses_product_language_not_raw_enums() -> None:
    """Default summaries omit raw enums/Python class/function names; machine identifiers stay structured."""
    builder = GenerationResultBuilder("generate_videos", GenerationSelectionMode.MISSING_ONLY)
    builder.fail(
        "E1S01",
        problem=GenerationProblem(
            code=GenerationProblemCode.UNIT_NOT_FOUND,
            detail="剧本中未找到该 ID",
            action=GenerationAction.FIX_INPUT,
        ),
    )
    builder.block(
        "E1S02",
        problem=GenerationProblem(
            code=GenerationProblemCode.ACTIVE_TASK_CONFLICT,
            detail="该 ID 有在途任务",
            action=GenerationAction.WAIT_FOR_TASK,
        ),
    )
    builder.skip_unit("E1S03", artifact_path="videos/c.mp4", artifact_status=ArtifactStatus.STALE)
    result = builder.build()

    text = render_generation_result(result)

    assert "generation_unit_not_found" not in text
    assert "generation_active_task_conflict" not in text
    assert "fix_input" not in text
    assert "wait_for_task" not in text
    assert "GenerationProblemCode" not in text
    assert "GenerationAction" not in text

    assert "需修正输入" in text
    assert "等待进行中任务完成" in text
    assert "比当前内容旧" in text
    assert "视频生成" in text


def test_every_machine_identifier_has_a_product_language_label() -> None:
    """Label tables cover every enum member, preventing silent summary information loss."""
    assert set(_ACTION_LABELS) == set(GenerationAction)
    assert set(_ARTIFACT_STATUS_LABELS) == set(ArtifactStatus)


def test_every_generation_entry_point_has_a_product_language_label() -> None:
    """Every generation entry registers product-language names so headers avoid generic fallback wording."""
    from server.media_tools import (
        final_cuts,
        grid,
        image_edits,
        narration_audio,
        storyboards,
        videos,
    )
    from server.services.admission.asset_sheet_batch import ASSET_BATCH_OPERATION

    operations = {
        ASSET_BATCH_OPERATION,
        final_cuts._OPERATION,
        grid._OPERATION,
        image_edits._OPERATION,
        narration_audio._OPERATION,
        storyboards._OPERATION,
        videos._OPERATION,
    }
    for operation in operations:
        label = _OPERATION_LABELS.get(operation, "")
        assert label, f"{operation} 未登记产品语言名"

        builder = GenerationResultBuilder(operation, GenerationSelectionMode.MISSING_ONLY)
        builder.succeed("E1S01", task_id="t1", artifact_path="videos/E1S01.mp4")
        text = render_generation_result(builder.build())

        assert text.startswith(f"{label}：")
        assert operation not in text


def test_the_summary_header_names_the_operation_in_product_language() -> None:
    """Headers use product-language entry names, never raw tool names."""
    builder = GenerationResultBuilder("generate_videos", GenerationSelectionMode.MISSING_ONLY)
    builder.succeed("E1S01", task_id="t1", artifact_path="videos/E1S01.mp4")
    text = render_generation_result(builder.build())

    assert "generate_videos" not in text
    assert text.startswith("视频生成：")
