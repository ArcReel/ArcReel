"""Project-local correction budgets and evidence-backed lessons, not model training.

The media-review caller supplies receipts derived from inspected immutable video
versions. These synchronous transactions must run through ``run_sync_transaction``
when called from an async request.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import portalocker
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from lib.infra.content_digest import canonical_json_digest
from lib.infra.json_io import atomic_write_json

MAX_ATTEMPTS = 2
MAX_HISTORY = 128
MAX_LESSONS = 32
MAX_UNITS = 256
MAX_FINGERPRINTS_PER_UNIT = 32
MAX_LEDGER_BYTES = 2 * 1024 * 1024
LEDGER_FILENAME = "video_corrections.json"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Version = Annotated[StrictInt, Field(ge=1)]
ShortText = Annotated[str, Field(min_length=1, max_length=160)]
EvidenceText = Annotated[str, Field(min_length=1, max_length=1000)]
Evidence = Annotated[tuple[EvidenceText, ...], Field(max_length=8)]
Outcome = Literal["failed", "unverified", "verified"]


class CorrectionLedgerError(ValueError):
    """A correction cannot safely change the project ledger."""


class _LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CorrectionScope(_LedgerModel):
    provider: ShortText
    model: ShortText
    reference_hashes: tuple[Sha256, ...] = Field(max_length=32)
    context_hash: Sha256


class MediaReviewVerification(_LedgerModel):
    """Server-derived receipts for media actually delivered for frame review."""

    source: Literal["frame_review"] = "frame_review"
    passed: StrictBool
    original_version: Version
    candidate_version: Version
    before_review_hash: Sha256
    after_review_hash: Sha256


class CorrectionAttempt(_LedgerModel):
    attempt_id: ShortText
    unit_id: ShortText
    baseline_version: Version
    baseline_max_version: Version = Field(default_factory=lambda data: data["baseline_version"])
    root_baseline_version: Version
    scope: CorrectionScope
    input_hash: Sha256
    correction: str = Field(min_length=1, max_length=2000)
    fingerprint: Sha256
    status: Literal["reserved", "failed", "unverified", "verified"] = "reserved"
    created_at: str = Field(min_length=1, max_length=40)
    candidate_version: Version | None = None
    before_evidence: Evidence = ()
    after_evidence: Evidence = ()
    verification: MediaReviewVerification | None = None

    @model_validator(mode="after")
    def _check_outcome(self) -> CorrectionAttempt:
        if self.baseline_max_version < self.baseline_version:
            raise ValueError("Maximum existing version must include the baseline")
        if self.candidate_version is not None and self.candidate_version <= self.baseline_max_version:
            raise ValueError("Candidate must be newer than every version present at reservation")
        if self.status in {"unverified", "verified"} and self.candidate_version is None:
            raise ValueError("A candidate version is required")
        if self.status != "reserved" and (not self.before_evidence or not self.after_evidence):
            raise ValueError("Before and after evidence are required")
        if self.status == "verified":
            proof = self.verification
            if (
                proof is None
                or not proof.passed
                or proof.original_version != self.baseline_version
                or proof.candidate_version != self.candidate_version
                or proof.before_review_hash == proof.after_review_hash
            ):
                raise ValueError("Passing media review receipts must match both immutable versions")
        elif self.verification is not None:
            raise ValueError("Only verified outcomes accept passing media review receipts")
        return self


class VerifiedLesson(_LedgerModel):
    attempt: CorrectionAttempt

    @model_validator(mode="after")
    def _check_verified(self) -> VerifiedLesson:
        if self.attempt.status != "verified":
            raise ValueError("A lesson requires a verified correction")
        return self


class _UnitBudget(_LedgerModel):
    unit_id: ShortText
    root_baseline_version: Version
    attempts: StrictInt = Field(ge=0, le=MAX_ATTEMPTS)
    resolved_version: Version | None = None
    fingerprints: tuple[Sha256, ...] = Field(max_length=MAX_FINGERPRINTS_PER_UNIT)


class _CorrectionLedger(_LedgerModel):
    schema_version: Literal[1] = 1
    budgets: tuple[_UnitBudget, ...] = Field(default=(), max_length=MAX_UNITS)
    history: tuple[CorrectionAttempt, ...] = Field(default=(), max_length=MAX_HISTORY)
    lessons: tuple[VerifiedLesson, ...] = Field(default=(), max_length=MAX_LESSONS)


def _check_state_path(path: Path) -> None:
    if path.is_symlink() or path.is_junction():
        raise CorrectionLedgerError("Correction state must remain inside its project")


@contextmanager
def _locked_ledger(project_dir: Path) -> Generator[Path]:
    directory = project_dir / ".arcreel"
    _check_state_path(directory)
    directory.mkdir(exist_ok=True)
    path = directory / LEDGER_FILENAME
    lock_path = directory / f".{LEDGER_FILENAME}.lock"
    _check_state_path(path)
    _check_state_path(lock_path)
    with portalocker.Lock(lock_path, mode="a+b", timeout=5):
        _check_state_path(directory)
        _check_state_path(path)
        yield path


def _load(path: Path) -> _CorrectionLedger:
    if not path.exists():
        return _CorrectionLedger()
    with path.open("rb") as handle:
        data = handle.read(MAX_LEDGER_BYTES + 1)
    if len(data) > MAX_LEDGER_BYTES:
        raise CorrectionLedgerError("Correction ledger exceeds its size limit")
    return _CorrectionLedger.model_validate_json(data)


def _save(path: Path, ledger: _CorrectionLedger) -> None:
    data = ledger.model_dump(mode="json")
    if len(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_LEDGER_BYTES:
        raise CorrectionLedgerError("Correction ledger exceeds its size limit")
    atomic_write_json(path, data)


def reserve_correction(
    project_dir: Path,
    *,
    unit_id: str,
    baseline_version: int,
    scope: CorrectionScope,
    input_hash: str,
    correction: str,
    baseline_max_version: int | None = None,
) -> CorrectionAttempt:
    """Durably consume one of two automatic attempts before generation begins.

    Model/input/baseline changes and verified success do not reset the unit budget.
    Compact budget records survive history eviction; at capacity reservations fail
    without discarding them.
    """
    if len(correction) > 2000:
        raise CorrectionLedgerError("Correction exceeds its size limit")
    correction = " ".join(correction.split())
    fingerprint = canonical_json_digest(
        {
            "provider": scope.provider,
            "model": scope.model,
            "reference_hashes": scope.reference_hashes,
            "input_hash": input_hash,
        }
    )
    attempt = CorrectionAttempt(
        attempt_id=uuid4().hex,
        unit_id=unit_id,
        baseline_version=baseline_version,
        baseline_max_version=baseline_version if baseline_max_version is None else baseline_max_version,
        root_baseline_version=baseline_version,
        scope=scope,
        input_hash=input_hash,
        correction=correction,
        fingerprint=fingerprint,
        created_at=datetime.now(UTC).isoformat(),
    )
    with _locked_ledger(project_dir) as path:
        ledger = _load(path)
        if any(item.unit_id == unit_id and item.status == "reserved" for item in ledger.history):
            raise CorrectionLedgerError("A correction for this unit is already pending")
        budget = next((item for item in ledger.budgets if item.unit_id == unit_id), None)
        if budget is None:
            if len(ledger.budgets) >= MAX_UNITS:
                raise CorrectionLedgerError("Correction unit capacity reached; automatic repair is stopped")
            budget = _UnitBudget(unit_id=unit_id, root_baseline_version=baseline_version, attempts=0, fingerprints=())
        if fingerprint in budget.fingerprints:
            raise CorrectionLedgerError("Identical generation inputs have already been attempted")
        if len(budget.fingerprints) >= MAX_FINGERPRINTS_PER_UNIT:
            raise CorrectionLedgerError("Correction history capacity reached; automatic repair is stopped")
        if budget.attempts >= MAX_ATTEMPTS:
            raise CorrectionLedgerError("Two automatic correction attempts exhausted; human review is required")
        attempt = attempt.model_copy(update={"root_baseline_version": budget.root_baseline_version})
        budget = budget.model_copy(
            update={
                "attempts": budget.attempts + 1,
                "resolved_version": None,
                "fingerprints": (*budget.fingerprints, fingerprint),
            }
        )
        history = list(ledger.history)
        if len(history) >= MAX_HISTORY:
            removable = next((i for i, item in enumerate(history) if item.status != "reserved"), None)
            if removable is None:
                raise CorrectionLedgerError("Pending correction capacity reached; automatic repair is stopped")
            history.pop(removable)
        updated = ledger.model_copy(
            update={
                "budgets": (*[item for item in ledger.budgets if item.unit_id != unit_id], budget),
                "history": (*history, attempt),
            }
        )
        _save(path, updated)
    return attempt


def record_correction_outcome(
    project_dir: Path,
    *,
    attempt_id: str,
    outcome: Outcome,
    candidate_version: int | None = None,
    before_evidence: tuple[str, ...],
    after_evidence: tuple[str, ...],
    verification: MediaReviewVerification | None = None,
) -> CorrectionAttempt:
    """Record evidence; only a passing, version-bound media review becomes a lesson.

    An unverified candidate may subsequently pass or fail review using the same
    candidate version. Failed and verified outcomes are immutable; identical retries
    are safe.
    """
    if outcome not in {"failed", "unverified", "verified"}:
        raise CorrectionLedgerError("Unknown correction outcome")
    with _locked_ledger(project_dir) as path:
        ledger = _load(path)
        original = next((item for item in ledger.history if item.attempt_id == attempt_id), None)
        if original is None:
            raise CorrectionLedgerError("Unknown correction attempt")
        updated = CorrectionAttempt.model_validate(
            {
                **original.model_dump(),
                "status": outcome,
                "candidate_version": candidate_version,
                "before_evidence": before_evidence,
                "after_evidence": after_evidence,
                "verification": verification,
            }
        )
        if original == updated:
            return original
        if original.status != "reserved" and not (
            original.status == "unverified"
            and outcome in {"failed", "verified"}
            and original.candidate_version == candidate_version
        ):
            raise CorrectionLedgerError("Recorded correction outcomes are immutable")
        lessons = ledger.lessons
        budgets = ledger.budgets
        history = tuple(updated if item.attempt_id == attempt_id else item for item in ledger.history)
        if outcome == "verified":
            lessons = (*lessons, VerifiedLesson(attempt=updated))[-MAX_LESSONS:]
        cycle = [
            item
            for item in history
            if item.unit_id == original.unit_id and item.root_baseline_version == original.root_baseline_version
        ]
        verified_versions = [item.candidate_version for item in cycle if item.status == "verified"]
        if verified_versions and not any(item.status == "reserved" for item in cycle):
            resolved_version = max(version for version in verified_versions if version is not None)
            budgets = tuple(
                item.model_copy(update={"resolved_version": resolved_version})
                if item.unit_id == original.unit_id and item.root_baseline_version == original.root_baseline_version
                else item
                for item in budgets
            )
        _save(
            path,
            ledger.model_copy(
                update={
                    "history": history,
                    "budgets": budgets,
                    "lessons": lessons,
                }
            ),
        )
    return updated


def matching_verified_lessons(
    project_dir: Path, *, scope: CorrectionScope, limit: int = 3
) -> tuple[VerifiedLesson, ...]:
    """Return at most three recent lessons matching the exact project/input scope."""
    if type(limit) is not int or not 1 <= limit <= 3:
        raise CorrectionLedgerError("Lesson limit must be between one and three")
    with _locked_ledger(project_dir) as path:
        ledger = _load(path)
    return tuple(item for item in reversed(ledger.lessons) if item.attempt.scope == scope)[:limit]


def correction_attempts(project_dir: Path, *, unit_id: str) -> tuple[CorrectionAttempt, ...]:
    """Read retained attempts for a unit, including pending work after a restart."""
    with _locked_ledger(project_dir) as path:
        ledger = _load(path)
    return tuple(item for item in ledger.history if item.unit_id == unit_id)


def get_correction_attempt(project_dir: Path, *, attempt_id: str) -> CorrectionAttempt:
    """Read a retained attempt before validating its media review receipts."""
    with _locked_ledger(project_dir) as path:
        ledger = _load(path)
    attempt = next((item for item in ledger.history if item.attempt_id == attempt_id), None)
    if attempt is None:
        attempt = next((item.attempt for item in ledger.lessons if item.attempt.attempt_id == attempt_id), None)
    if attempt is None:
        raise CorrectionLedgerError("Unknown correction attempt")
    return attempt


def retire_verified_lesson(project_dir: Path, *, attempt_id: str) -> bool:
    """Stop reusing a rejected lesson without rewriting its recorded outcome."""
    with _locked_ledger(project_dir) as path:
        ledger = _load(path)
        lessons = tuple(item for item in ledger.lessons if item.attempt.attempt_id != attempt_id)
        if lessons == ledger.lessons:
            return False
        _save(path, ledger.model_copy(update={"lessons": lessons}))
    return True
