"""Durable repair budgets, immutable outcomes and scoped verified lessons."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.video_review.corrections import (
    LEDGER_FILENAME,
    MAX_HISTORY,
    MAX_LEDGER_BYTES,
    MAX_LESSONS,
    CorrectionAttempt,
    CorrectionLedgerError,
    CorrectionScope,
    MediaReviewVerification,
    correction_attempts,
    get_correction_attempt,
    matching_verified_lessons,
    record_correction_outcome,
    related_verified_lessons,
    reserve_correction,
    retire_verified_lesson,
)
from tests.fakes import fail_file_replace


def _scope(context_hash: str = "a" * 64) -> CorrectionScope:
    return CorrectionScope(provider="provider", model="model", reference_hashes=("b" * 64,), context_hash=context_hash)


def _reserve(
    project: Path,
    *,
    unit: str = "E1S01",
    correction: str = "Keep camera still",
    baseline: int = 1,
    input_hash: str = "c" * 64,
) -> CorrectionAttempt:
    return reserve_correction(
        project,
        unit_id=unit,
        baseline_version=baseline,
        scope=_scope(),
        input_hash=input_hash,
        correction=correction,
    )


def _fail(project: Path, attempt: CorrectionAttempt) -> CorrectionAttempt:
    return record_correction_outcome(
        project,
        attempt_id=attempt.attempt_id,
        outcome="failed",
        before_evidence=("Camera shakes in the original frames",),
        after_evidence=("Generation failed before producing a candidate",),
    )


def _verify(project: Path, attempt: CorrectionAttempt) -> CorrectionAttempt:
    return record_correction_outcome(
        project,
        attempt_id=attempt.attempt_id,
        outcome="verified",
        candidate_version=attempt.baseline_version + 1,
        before_evidence=("Camera shakes in the original frames",),
        after_evidence=("Candidate frame review passes camera criterion",),
        verification=MediaReviewVerification(
            passed=True,
            original_version=attempt.baseline_version,
            candidate_version=attempt.baseline_version + 1,
            before_review_hash="d" * 64,
            after_review_hash="e" * 64,
        ),
    )


def _ledger_path(project: Path) -> Path:
    return project / ".arcreel" / LEDGER_FILENAME


def test_changed_context_input_and_baseline_do_not_reset_two_attempt_budget(tmp_path: Path) -> None:
    first = _reserve(tmp_path)
    _fail(tmp_path, first)
    second = reserve_correction(
        tmp_path,
        unit_id=first.unit_id,
        baseline_version=2,
        scope=_scope("f" * 64),
        input_hash="f" * 64,
        correction="Remove motion blur",
    )
    _fail(tmp_path, second)

    with pytest.raises(CorrectionLedgerError, match="Two automatic"):
        _reserve(tmp_path, correction="Another approach", baseline=3, input_hash="a" * 64)

    data = json.loads(_ledger_path(tmp_path).read_text())
    assert [item["status"] for item in data["history"]] == ["failed", "failed"]
    assert second.root_baseline_version == first.baseline_version
    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()


def test_identical_correction_and_input_are_rejected_even_after_success(tmp_path: Path) -> None:
    first = _reserve(tmp_path)
    _verify(tmp_path, first)

    with pytest.raises(CorrectionLedgerError, match="Identical generation inputs"):
        _reserve(tmp_path, correction=" Keep   camera still ", baseline=2)

    next_attempt = _reserve(tmp_path, correction="Maintain character expression", baseline=2, input_hash="f" * 64)
    assert next_attempt.root_baseline_version == 1
    _verify(tmp_path, next_attempt)
    with pytest.raises(CorrectionLedgerError, match="Two automatic"):
        _reserve(tmp_path, correction="Try after another success", baseline=3, input_hash="a" * 64)


def test_reworded_correction_and_context_cannot_repeat_identical_generation_inputs(tmp_path: Path) -> None:
    _fail(tmp_path, _reserve(tmp_path))

    with pytest.raises(CorrectionLedgerError, match="Identical generation inputs"):
        reserve_correction(
            tmp_path,
            unit_id="E1S01",
            baseline_version=2,
            scope=_scope("f" * 64),
            input_hash="c" * 64,
            correction="Try stabilizing the camera again, phrased differently",
        )

    assert len(correction_attempts(tmp_path, unit_id="E1S01")) == 1


@pytest.mark.parametrize(
    "scope_update",
    [{"provider": "other-provider"}, {"model": "other-model"}, {"reference_hashes": ("f" * 64,)}],
)
def test_changed_generation_provider_model_or_references_are_distinct_inputs(
    tmp_path: Path, scope_update: dict
) -> None:
    first = _reserve(tmp_path)
    _fail(tmp_path, first)

    second = reserve_correction(
        tmp_path,
        unit_id="E1S01",
        baseline_version=1,
        scope=_scope().model_copy(update=scope_update),
        input_hash="c" * 64,
        correction=first.correction,
    )

    assert second.fingerprint != first.fingerprint
    assert len(correction_attempts(tmp_path, unit_id="E1S01")) == 2


def test_candidate_must_be_generated_after_the_reservation_snapshot(tmp_path: Path) -> None:
    attempt = reserve_correction(
        tmp_path,
        unit_id="E1S01",
        baseline_version=1,
        baseline_max_version=3,
        scope=_scope(),
        input_hash="c" * 64,
        correction="Keep camera still",
    )
    assert get_correction_attempt(tmp_path, attempt_id=attempt.attempt_id).baseline_max_version == 3
    with pytest.raises(ValidationError, match="newer than every version"):
        _verify(tmp_path, attempt)

    verified = record_correction_outcome(
        tmp_path,
        attempt_id=attempt.attempt_id,
        outcome="verified",
        candidate_version=4,
        before_evidence=("Original camera shakes",),
        after_evidence=("Newly generated candidate is stable",),
        verification=MediaReviewVerification(
            passed=True,
            original_version=1,
            candidate_version=4,
            before_review_hash="d" * 64,
            after_review_hash="e" * 64,
        ),
    )
    assert matching_verified_lessons(tmp_path, scope=_scope())[0].attempt == verified


def test_parallel_reservations_cannot_duplicate_generation(tmp_path: Path) -> None:
    def reserve() -> CorrectionAttempt | CorrectionLedgerError:
        try:
            return _reserve(tmp_path)
        except CorrectionLedgerError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reserve(), range(2)))

    assert sum(isinstance(item, CorrectionAttempt) for item in results) == 1
    assert sum(isinstance(item, CorrectionLedgerError) for item in results) == 1
    assert len(json.loads(_ledger_path(tmp_path).read_text())["history"]) == 1


def test_unverified_candidate_requires_version_bound_media_review_to_become_a_lesson(tmp_path: Path) -> None:
    first = _reserve(tmp_path)
    recorded = record_correction_outcome(
        tmp_path,
        attempt_id=first.attempt_id,
        outcome="unverified",
        candidate_version=2,
        before_evidence=("Original camera movement",),
        after_evidence=("Candidate generated, review pending",),
    )
    assert recorded.status == "unverified"
    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()

    with pytest.raises(ValidationError, match="Passing media review"):
        record_correction_outcome(
            tmp_path,
            attempt_id=first.attempt_id,
            outcome="verified",
            candidate_version=2,
            before_evidence=("Original camera movement",),
            after_evidence=("It looks better",),
        )
    verified = _verify(tmp_path, first)
    assert matching_verified_lessons(tmp_path, scope=_scope())[0].attempt == verified
    assert _verify(tmp_path, first) == verified
    assert len(json.loads(_ledger_path(tmp_path).read_text())["lessons"]) == 1


@pytest.mark.parametrize(
    "proof_update",
    [{"passed": False}, {"original_version": 3}, {"candidate_version": 3}, {"after_review_hash": "d" * 64}],
)
def test_mismatched_or_failed_media_review_cannot_create_a_lesson(tmp_path: Path, proof_update: dict) -> None:
    attempt = _reserve(tmp_path)
    proof = MediaReviewVerification.model_validate(
        {
            "passed": True,
            "original_version": 1,
            "candidate_version": 2,
            "before_review_hash": "d" * 64,
            "after_review_hash": "e" * 64,
            **proof_update,
        }
    )
    before = _ledger_path(tmp_path).read_bytes()

    with pytest.raises(ValidationError, match="Passing media review"):
        record_correction_outcome(
            tmp_path,
            attempt_id=attempt.attempt_id,
            outcome="verified",
            candidate_version=2,
            before_evidence=("Original",),
            after_evidence=("Candidate",),
            verification=proof,
        )

    assert _ledger_path(tmp_path).read_bytes() == before
    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()


def test_failed_outcome_cannot_be_rewritten_as_verified(tmp_path: Path) -> None:
    attempt = _reserve(tmp_path)
    _fail(tmp_path, attempt)

    with pytest.raises(CorrectionLedgerError, match="immutable"):
        _verify(tmp_path, attempt)

    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()


def test_pending_attempt_is_recoverable_and_unverified_candidate_can_fail_review(tmp_path: Path) -> None:
    attempt = _reserve(tmp_path)
    assert get_correction_attempt(tmp_path, attempt_id=attempt.attempt_id) == attempt
    with pytest.raises(CorrectionLedgerError, match="Unknown correction attempt"):
        get_correction_attempt(tmp_path, attempt_id="missing-attempt")
    assert correction_attempts(tmp_path, unit_id=attempt.unit_id) == (attempt,)
    assert correction_attempts(tmp_path, unit_id="another-unit") == ()
    record_correction_outcome(
        tmp_path,
        attempt_id=attempt.attempt_id,
        outcome="unverified",
        candidate_version=2,
        before_evidence=("Original camera movement",),
        after_evidence=("Review pending",),
    )
    failed = record_correction_outcome(
        tmp_path,
        attempt_id=attempt.attempt_id,
        outcome="failed",
        candidate_version=2,
        before_evidence=("Original camera movement",),
        after_evidence=("Reviewed candidate still shakes",),
    )

    assert correction_attempts(tmp_path, unit_id=attempt.unit_id) == (failed,)
    assert failed.status == "failed"
    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()


def test_retiring_a_lesson_preserves_history_and_cannot_be_undone_by_outcome_retry(tmp_path: Path) -> None:
    attempt = _reserve(tmp_path)
    verified = _verify(tmp_path, attempt)

    assert retire_verified_lesson(tmp_path, attempt_id=attempt.attempt_id) is True
    assert get_correction_attempt(tmp_path, attempt_id=attempt.attempt_id) == verified
    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()
    assert _verify(tmp_path, attempt) == verified
    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()
    assert retire_verified_lesson(tmp_path, attempt_id=attempt.attempt_id) is False


def test_verified_lessons_remain_project_and_exact_context_scoped(tmp_path: Path) -> None:
    for index in range(MAX_LESSONS + 2):
        _verify(tmp_path, _reserve(tmp_path, unit=f"shot-{index}"))
    other_project = tmp_path / "other-project"
    other_project.mkdir()

    lessons = matching_verified_lessons(tmp_path, scope=_scope())
    assert [item.attempt.unit_id for item in lessons] == ["shot-33", "shot-32", "shot-31"]
    assert len(json.loads(_ledger_path(tmp_path).read_text())["lessons"]) == MAX_LESSONS
    assert matching_verified_lessons(other_project, scope=_scope()) == ()
    assert matching_verified_lessons(tmp_path, scope=_scope("f" * 64)) == ()
    changed_model = _scope().model_copy(update={"model": "another-model"})
    assert matching_verified_lessons(tmp_path, scope=changed_model) == ()


def test_related_lessons_are_bounded_and_exclude_other_renderers_projects_and_retired_evidence(tmp_path: Path) -> None:
    attempts = [_verify(tmp_path, _reserve(tmp_path, unit=f"shot-{index}")) for index in range(5)]
    _fail(tmp_path, _reserve(tmp_path, unit="failed"))
    _reserve(tmp_path, unit="pending")
    retire_verified_lesson(tmp_path, attempt_id=attempts[-1].attempt_id)
    context = _scope("f" * 64)

    assert [item.attempt.unit_id for item in related_verified_lessons(tmp_path, scope=context)] == [
        "shot-3",
        "shot-2",
        "shot-1",
    ]
    assert related_verified_lessons(tmp_path, scope=_scope()) == ()
    assert related_verified_lessons(tmp_path, scope=context.model_copy(update={"model": "other"})) == ()
    assert related_verified_lessons(tmp_path, scope=context.model_copy(update={"provider": "other"})) == ()
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    assert related_verified_lessons(other_project, scope=context) == ()


def test_detail_history_eviction_does_not_restore_exhausted_budget(tmp_path: Path) -> None:
    _fail(tmp_path, _reserve(tmp_path))
    _fail(tmp_path, _reserve(tmp_path, correction="Second attempt", input_hash="f" * 64))
    for index in range(MAX_HISTORY):
        _fail(tmp_path, _reserve(tmp_path, unit=f"shot-{index}"))

    data = json.loads(_ledger_path(tmp_path).read_text())
    assert len(data["history"]) == MAX_HISTORY
    assert all(item["unit_id"] != "E1S01" for item in data["history"])
    with pytest.raises(CorrectionLedgerError, match="Two automatic"):
        _reserve(tmp_path, correction="Try after history eviction", input_hash="a" * 64)


def test_verified_lesson_remains_retrievable_for_retirement_after_history_eviction(tmp_path: Path) -> None:
    attempt = _reserve(tmp_path)
    verified = _verify(tmp_path, attempt)
    for index in range(MAX_HISTORY):
        _fail(tmp_path, _reserve(tmp_path, unit=f"shot-{index}"))

    assert correction_attempts(tmp_path, unit_id=attempt.unit_id) == ()
    assert get_correction_attempt(tmp_path, attempt_id=attempt.attempt_id) == verified
    assert retire_verified_lesson(tmp_path, attempt_id=attempt.attempt_id) is True
    assert matching_verified_lessons(tmp_path, scope=_scope()) == ()


@pytest.mark.parametrize("data", [{"schema_version": 2}, {"schema_version": 1, "unknown": True}])
def test_unknown_persisted_schema_is_rejected_without_overwriting(tmp_path: Path, data: dict) -> None:
    path = _ledger_path(tmp_path)
    path.parent.mkdir()
    path.write_text(json.dumps(data))
    before = path.read_bytes()

    with pytest.raises(ValidationError):
        _reserve(tmp_path)

    assert path.read_bytes() == before


def test_oversized_ledger_and_request_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorrectionLedgerError, match="size limit"):
        _reserve(tmp_path, correction="x" * 2001)
    path = _ledger_path(tmp_path)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b" " * (MAX_LEDGER_BYTES + 1))

    with pytest.raises(CorrectionLedgerError, match="size limit"):
        matching_verified_lessons(tmp_path, scope=_scope())


def test_atomic_replace_failure_preserves_the_previous_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _reserve(tmp_path)
    _fail(tmp_path, first)
    before = _ledger_path(tmp_path).read_bytes()

    with monkeypatch.context() as fault:
        fail_file_replace(fault, _ledger_path(tmp_path), message="disk full")
        with pytest.raises(OSError, match="disk full"):
            _reserve(tmp_path, correction="Second attempt", input_hash="f" * 64)

    assert _ledger_path(tmp_path).read_bytes() == before
    assert _reserve(tmp_path, correction="Second attempt", input_hash="f" * 64).root_baseline_version == 1


def test_state_directory_cannot_escape_the_project_through_a_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    project = tmp_path / "project"
    outside.mkdir()
    project.mkdir()
    (project / ".arcreel").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CorrectionLedgerError, match="inside its project"):
        _reserve(project)

    assert list(outside.iterdir()) == []
