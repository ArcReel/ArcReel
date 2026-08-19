"""Contract tests for the shared per-ID generation selection/result module."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.artifact_manifest import (
    ArtifactBlocker,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifestError,
    ArtifactStatus,
)
from lib.generation_queue_client import BatchTaskResult
from lib.generation_result import (
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
from lib.task_failure import FAILURE_CODE_KEYS, encode_failure

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


def test_missing_only_selects_missing_and_reuses_stale(tmp_path: Path) -> None:
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


def test_missing_only_never_regenerates_a_blocked_artifact(tmp_path: Path) -> None:
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


def test_explicit_selection_takes_named_ids_regardless_of_state(tmp_path: Path) -> None:
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


def test_explicit_empty_collection_is_invalid_not_everything(tmp_path: Path) -> None:
    """显式空集合是调用方错误：既不等于「全部」，也不静默变成空批次。"""

    with pytest.raises(ValueError, match="不能为空"):
        select_generation_targets(
            candidates=[_candidate("A")],
            requested_ids=[],
            resolver=_Resolver({}),  # type: ignore[arg-type]
        )


def test_normalize_requested_ids_is_the_single_gate_for_selection_intent() -> None:
    assert normalize_requested_ids(None, field="names") is None
    assert normalize_requested_ids(["B", "A", "B"], field="names") == ["B", "A"]
    with pytest.raises(ValueError, match="不能为空数组"):
        normalize_requested_ids([], field="names")
    with pytest.raises(ValueError, match="必须是 ID 数组"):
        normalize_requested_ids("A", field="names")


def test_missing_only_reselects_a_recorded_path_the_manifest_no_longer_claims() -> None:
    """登记路径指向的文件被删/被移后，比对报 MISSING，该单元判为缺失而不是被永久复用。"""

    resolver = _Resolver({"GONE": ArtifactStatus.MISSING, "KEPT": ArtifactStatus.CURRENT})

    selection = select_generation_targets(
        candidates=[
            _candidate("GONE", path="videos/gone.mp4"),
            _candidate("KEPT", path="videos/kept.mp4"),
        ],
        requested_ids=None,
        resolver=resolver,  # type: ignore[arg-type]
    )

    assert selection.target_ids == ("GONE",)
    assert [state.unit_id for state in selection.skipped] == ["KEPT"]


def test_missing_only_admits_an_override_leg_without_rechecking_the_filesystem(tmp_path: Path) -> None:
    """另一条可复用的腿（如精确匹配的手动上传）独立成立，判定只看这条腿本身。"""

    resolver = _Resolver({"A": ArtifactStatus.MISSING})

    selection = select_generation_targets(
        candidates=[_candidate("A", path="videos/gone.mp4")],
        requested_ids=None,
        resolver=resolver,  # type: ignore[arg-type]
        reusable_override=lambda _candidate: True,
    )

    assert selection.target_ids == ()
    assert [state.unit_id for state in selection.skipped] == ["A"]


def test_missing_only_does_not_recheck_the_filesystem(tmp_path: Path) -> None:
    """只信比对结论：磁盘上没有同名文件也不改变判定。"""

    resolver = _Resolver({"A": ArtifactStatus.CURRENT, "B": ArtifactStatus.MISSING})

    selection = select_generation_targets(
        candidates=[_candidate("A"), _candidate("B")],
        requested_ids=None,
        resolver=resolver,  # type: ignore[arg-type]
    )

    assert selection.target_ids == ("B",)
    assert [state.unit_id for state in selection.skipped] == ["A"]


def test_observe_artifact_status_separates_unobservable_from_missing() -> None:
    key = ArtifactKey.episode_video(1, "A")
    resolver = _Resolver({"A": ArtifactStatus.CURRENT})

    # 没有 key 可比对的单元：该轴不可观测，与「缺失」不是一回事。
    assert observe_artifact_status(
        resolver=resolver,  # type: ignore[arg-type]
        key=None,
        artifact_path="videos/a.mp4",
    ) == (None, None)
    assert (
        observe_artifact_status(
            resolver=resolver,  # type: ignore[arg-type]
            key=key,
            artifact_path=None,
        )[0]
        is ArtifactStatus.MISSING
    )

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


def test_every_registered_failure_code_resolves_to_a_declared_action() -> None:
    """动作表与 task_failure 的编码表同源：新增 failure code 不登记就会被这里挡住。

    未登记的 code 只会退化成 ``retry``——安全但不够锐利，所以要求显式登记。
    """

    unmapped = sorted(set(FAILURE_CODE_KEYS) - set(_TASK_FAILURE_ACTIONS))
    assert unmapped == [], f"这些 failure code 未在 _TASK_FAILURE_ACTIONS 登记下一步动作: {unmapped}"

    unknown = sorted(set(_TASK_FAILURE_ACTIONS) - set(FAILURE_CODE_KEYS))
    assert unknown == [], f"这些动作映射的 code 已不在 FAILURE_CODE_KEYS 中: {unknown}"


# --- batch recording -------------------------------------------------------


def _batch(resource_id: str, *, status: str = "succeeded", **kwargs: object) -> BatchTaskResult:
    return BatchTaskResult(resource_id=resource_id, task_id=f"task-{resource_id}", status=status, **kwargs)  # type: ignore[arg-type]


def test_recording_a_batch_reports_task_provider_and_artifact_axes_separately() -> None:
    """一次成功的任务不等于产物 current：三条轴各自如实报告。"""

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
        resolver=resolver,  # type: ignore[arg-type]
    )

    item = builder.build().items[0]
    assert item.task_state is GenerationTaskState.SUCCEEDED
    assert item.artifact_status is ArtifactStatus.STALE
    assert item.artifact_path == "videos/a.mp4"
    assert item.provider_checkpoint == ProviderCheckpoint(submitted=True, provider_job_id="job-1")


def test_a_failed_batch_item_keeps_the_old_artifact_and_its_status() -> None:
    """失败不动旧产物：报告里保留旧文件路径与旧状态，付过的钱不被抹掉。"""

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
    """等待被打断（超时/worker 离线）时任务在 worker 侧仍非终态——报告为
    INTERRUPTED + WAIT_FOR_TASK，不是 FAILED + RETRY，否则调用方会对一个可能仍在跑、
    还会正常落地的任务盲目重提交造成重复付费。"""

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
    """``task_id=""`` 标记 enqueue 调用本身炸了、从未进队——task_state 已经区分为
    NOT_QUEUED，problem code 也要跟着区分为 enqueue 专属码，不能落进
    ``problem_from_task_failure`` 的默认 TASK_FAILED：下游分不清"请求从没进队"和
    "任务跑了、供应商判失败"，给不出正确的重试路径（重触发 enqueue vs 排查供应商）。"""

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
    """队列侧 resource_id 与契约 unit ID 不同名时（如资产的 <type>/<name>）按映射记账。"""

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
    """``states`` 缺该 unit 时退回的默认 state 没有 artifact_key；产物轴退化为
    不可观测（None）而不是崩掉整批——resolver 是否激活不改变"缺 key 就没法比对"这一事实，
    崩溃只会连累这批里其它已正确记账的条目。"""

    builder = GenerationResultBuilder("generate_assets", GenerationSelectionMode.MISSING_ONLY)

    record_batch_outcomes(
        builder,
        successes=[_batch("张三", result={"file_path": "characters/张三.png"})],
        failures=[],
        states={},  # unit 不在映射里 -> _state() 退回无 artifact_key 的默认 state
        resolver=_Resolver({}),  # type: ignore[arg-type]
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
    """默认摘要不含原始枚举值或 Python 类名/函数名——机器标识只在结构化层。"""
    builder = GenerationResultBuilder("generate_video_selected", GenerationSelectionMode.MISSING_ONLY)
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
    assert "点名视频生成" in text


def test_every_machine_identifier_has_a_product_language_label() -> None:
    """标签表对枚举全覆盖：漏一个成员，摘要就会静默丢掉那条信息。"""
    assert set(_ACTION_LABELS) == set(GenerationAction)
    assert set(_ARTIFACT_STATUS_LABELS) == set(ArtifactStatus)


def test_every_generation_entry_point_has_a_product_language_label() -> None:
    """每个生成入口都登记了产品语言名，摘要抬头才不会回落到中性措辞。"""
    from server.agent_runtime.sdk_tools import (
        enqueue_assets,
        enqueue_grid,
        enqueue_image_edits,
        enqueue_narration_audio,
        enqueue_storyboards,
        enqueue_videos,
    )

    operations = {
        enqueue_assets._OPERATION,
        enqueue_grid._OPERATION,
        enqueue_image_edits._OPERATION,
        enqueue_narration_audio._OPERATION,
        enqueue_storyboards._OPERATION,
        enqueue_videos._EPISODE_OPERATION,
        enqueue_videos._SCENE_OPERATION,
        enqueue_videos._ALL_OPERATION,
        enqueue_videos._SELECTED_OPERATION,
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
    """抬头用生成入口的产品语言名，不直出工具名。"""
    builder = GenerationResultBuilder("generate_video_episode", GenerationSelectionMode.MISSING_ONLY)
    builder.succeed("E1S01", task_id="t1", artifact_path="videos/E1S01.mp4")
    text = render_generation_result(builder.build())

    assert "generate_video_episode" not in text
    assert text.startswith("整集视频生成：")
