"""Core script_plan→prompt_authoring content confirmation: applicability, script_plan paths, content fingerprints,
confirmation-state derivation, and the sole formal reference-to-video script_plan write entry
(``script_plan_write_lock`` / ``write_script_plan_locked``).

Gate consumers include Web routers/services (view/edit/confirm structured intermediate state, converting
confirmed content to formal scripts), Agent confirmation tools, and workflow state. State derivation is
pure computation over script_plan files and project dictionaries. Writes additionally hold the per-path
``ProjectManager.file_lock``. All four write paths (Web saves, resplitting, promotion, migration writes)
converge here, centralizing locks, optimistic-concurrency checks, and prompt_authoring draft clearing.

Persist only confirmation fingerprints in project.json ``episodes[i].script_plan_review``. Derive pending /
confirmed by comparing live script_plan fingerprints at read time, following compute-instead-of-store rules.
Rerunning normalization, Agent draft promotion, and Web manual edits all change fingerprints and automatically
require confirmation again, without per-write-path reporting.

Scope: three content/visual two-stage routes with structured script_plan intermediate state:
- drama / narration image-to-video / grid: script_plan_normalized_script.json / script_plan_segments.json;
- reference_video, across narration / drama content_mode: script_plan_reference_units.json.
``script_plan_kind`` centrally determines all three variants, prioritizing reference_video generation mode.
ad has no script_plan and stays outside the gate.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from arcreel_market_core.validation_messages import ValidationMessage
from lib.artifacts.formal_write import formal_write_transaction, project_metadata_lock
from lib.episode.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    episode_drafts_dir,
    episode_script_filename,
)
from lib.infra.content_digest import canonical_json_digest, prefixed, unprefixed
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.infra.path_safety import try_safe_join
from lib.infra.validation_messages import default_translate
from lib.project import project_trash
from lib.project.project_activity import record_project_activity
from lib.project.project_manager import ProjectManager, find_episode, is_reference_video_project
from lib.script.draft_quarantine import (
    DRAFT_OWNER_AGENT,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    draft_owner,
    quarantine_path,
    read_quarantine,
)
from lib.script.reference_video.duration_migration import migrate_unit_durations
from lib.script.script_editor import ScriptEditError, resolve_items
from lib.script.script_models import get_generated_assets
from lib.script.script_plan_entries import (
    ScriptPlanKind as ScriptPlanKind,
)

if TYPE_CHECKING:
    from lib.artifacts.artifact_manifest import ArtifactBasis

logger = logging.getLogger(__name__)

#: Confirmation states: not_applicable = episode outside gate; no_script_plan = applicable, not produced;
#: pending_review = produced but unconfirmed or changed since confirmation; confirmed = confirmed and converted to formal script.
ReviewStatus = Literal["not_applicable", "no_script_plan", "pending_review", "confirmed"]

#: Episode confirmation field: ``{"fingerprint": str, "confirmed_at": ISO8601}``.
REVIEW_FIELD = "script_plan_review"


#: Stale ledger entries record old script_plan fingerprints at replanning submission; changed live fingerprints prove rebuilding for the new ledger.
STALE_SCRIPT_PLAN_REVISION_FIELD = "stale_script_plan_revision"

#: Completion facts for rebuilding stale episodes. Fingerprints may match old content, so content change alone cannot prove execution.
STALE_SCRIPT_PLAN_REBUILT_REVISION_FIELD = "stale_script_plan_rebuilt_revision"

# ScriptPlanKind literals live in lib.script.script_plan_entries and are reexported by this module's top-level import.


def script_plan_kind(project: dict[str, Any]) -> ScriptPlanKind | None:
    """The project's script_plan variant, or None without structured intermediate state (such as ad).

    reference_video is a generation_mode across narration / drama content_mode; prioritize project
    generation mode, otherwise select drama / narration by content_mode. content_mode outside
    SCRIPT_PLAN_FILENAMES (ad) has no script_plan, and reference_video does not apply either.
    The project's two axes uniquely determine its variant, independent of episode number.
    """
    content_mode = project.get("content_mode")
    if content_mode not in SCRIPT_PLAN_FILENAMES:
        return None
    if is_reference_video_project(project):
        return "reference_video"
    return content_mode  # "drama" | "narration" (SCRIPT_PLAN_FILENAMES members)


def is_applicable(project: dict[str, Any]) -> bool:
    """Whether the project has a structured script_plan variant (drama / narration / reference_video) and uses the gate."""
    return script_plan_kind(project) is not None


def script_plan_path(project_path: Path, project: dict[str, Any], episode: int) -> Path | None:
    """Episode structured script_plan intermediate-file path, or None when the gate does not apply."""
    kind = script_plan_kind(project)
    if kind is None:
        return None
    filename = REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME if kind == "reference_video" else SCRIPT_PLAN_FILENAMES[kind]
    return episode_drafts_dir(project_path, episode) / filename


#: script_plan variant → draft origin. Each of three routes has one slot; absence means no draft slot,
#: so confirmation/generation do not block (ad has no structured script_plan and therefore no variant).
_SCRIPT_PLAN_QUARANTINE_KIND: dict[str, str] = {
    "reference_video": QUARANTINE_KIND_SCRIPT_PLAN,
    "drama": QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    "narration": QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
}


def script_plan_quarantine_kind(project: dict[str, Any]) -> str | None:
    """Draft origin for this project's script_plan variant, or None if it has no draft slot."""
    kind = script_plan_kind(project)
    return _SCRIPT_PLAN_QUARANTINE_KIND.get(kind) if kind is not None else None


def script_plan_quarantine_path(project_path: Path, project: dict[str, Any], episode: int) -> Path | None:
    """Episode script_plan draft path, or None if this variant has no draft slot.

    Return paths without existence checks, handled by ``script_plan_quarantined``. Separating them lets
    callers obtain paths for error messages.
    """
    quarantine_kind = script_plan_quarantine_kind(project)
    if quarantine_kind is None:
        return None
    return quarantine_path(project_path, episode, quarantine_kind)


def script_plan_quarantined(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    """Whether this episode has a script_plan draft, blocking the gate and prompt_authoring.

    Pending drafts are independent of formal script_plan content fingerprints. Repair/editable drafts
    leave formal files unchanged, so checking fingerprints alone would incorrectly permit prompt_authoring.
    Draft presence therefore blocks independently.

    Resolve drafts by current project generation mode (``script_plan_quarantine_path``). Ignore legacy
    drafts from other modes, or files with no current writer to clear them would permanently block episodes.
    """
    path = script_plan_quarantine_path(project_path, project, episode)
    return path is not None and path.exists()


def content_fingerprint_of_data(data: object) -> str:
    """Fingerprint parsed JSON as sha256 of canonical dumps, insensitive to key-order/whitespace changes.

    Callers fingerprint already loaded objects (such as premigration snapshots) without second disk reads:
    fingerprints must match the caller's actual content. Share canonicalization with ``content_fingerprint``'s
    JSON branch, changing only path input to parsed objects, so identical content produces identical values.
    """
    return canonical_json_digest(data)


def content_fingerprint(path: Path) -> str | None:
    """Fingerprint script_plan: canonical JSON dump sha256, insensitive to key order/whitespace and changing only
    with semantics; raw-byte sha256 for non-JSON; None for missing files (FileNotFoundError).

    Only missing files degrade to None (no_script_plan and gate permission). Propagate every other OSError,
    including permissions, directory placeholders, or transient I/O, rather than silently treating filesystem
    failures as absent script_plan and incorrectly permitting prompt_authoring.

    Use ``content_fingerprint_of_data`` for in-memory objects such as premigration snapshots, never read
    the same file again here. Concurrent writes between independent reads would be swallowed by this
    function's second read; see that function's docstring.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return hashlib.sha256(raw).hexdigest()
    return content_fingerprint_of_data(parsed)


def published_revision(fingerprint: str | None) -> str | None:
    """Public revision representation with ``sha256-v1:`` prefix, matching other read-interface revisions.

    Persisted baselines, draft metadata, and project records retain bare hexdigests. Prefix only for clients;
    strip client input through ``fingerprint_of_revision`` before comparing fingerprints.
    """
    return prefixed(fingerprint) if fingerprint is not None else None


def fingerprint_of_revision(revision: str | None) -> str | None:
    """Convert client revisions to fingerprints, accepting prefixed and bare hexdigests."""
    return unprefixed(revision) if revision is not None else None


class _UncheckedFingerprint(enum.Enum):
    """Sentinel type for ``write_script_plan_locked``'s ``expected_fingerprint``, distinguishing skipped baseline
    checks from None baselines (formal files absent when writers captured them). Both must be expressible;
    ``None`` alone represents only the latter.
    """

    TOKEN = 0


#: Unchecked-baseline sentinel for writers with no baseline: whole-document resplitting or same-critical-section read-modify-write.
UNCHECKED_FINGERPRINT = _UncheckedFingerprint.TOKEN


class ScriptPlanWriteConflict(Exception):
    """Optimistic-concurrency conflict: formal script_plan no longer matches the writer's baseline fingerprint.

    Carry current disk content (``current_content``, None for invalid JSON) and both fingerprints for
    editors to render conflicts and merge latest content. The single write entry raises after locked
    comparison, preventing later writers from silently overwriting earlier ones.
    """

    def __init__(self, *, expected: str | None, actual: str | None, current_content: dict[str, Any] | None):
        super().__init__(f"script_plan 并发冲突：基线指纹 {expected}，盘上现值指纹 {actual}")
        self.expected = expected
        self.actual = actual
        self.current_content = current_content


def mark_ledger_stale(project_path: Path, project: dict[str, Any], entry: dict[str, Any], episode: int) -> None:
    """Mark an episode's planning state stale, using current script_plan's content fingerprint as rebuild baseline.

    Further edits to already-stale episodes reset the baseline and clear prior rebuild-completion records.
    """
    entry["ledger_status"] = "stale"
    path = script_plan_path(project_path, project, episode)
    entry[STALE_SCRIPT_PLAN_REVISION_FIELD] = content_fingerprint(path) if path is not None else None
    entry.pop(STALE_SCRIPT_PLAN_REBUILT_REVISION_FIELD, None)


class ScriptPlanRebuildCompletionError(ValueError):
    """A stale script_plan rebuild cannot be recorded against the current ledger state."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def complete_stale_script_plan_rebuild(
    pm: ProjectManager,
    project_name: str,
    episode: int,
    expected_stale_revision: str | None,
) -> str:
    """Record preprocessing completion for a stale entry, including byte-identical rebuilds."""
    if isinstance(episode, bool) or episode < 1:
        raise ScriptPlanRebuildCompletionError("invalid_episode", "episode must be a positive integer")

    project_path = pm.get_project_path(project_name)
    committed: dict[str, str] = {}

    def _commit(project: dict[str, Any]) -> None:
        entry = find_episode(project, episode)
        if entry is None or entry.get("ledger_status") != "stale":
            raise ScriptPlanRebuildCompletionError("not_stale", "episode is not awaiting a stale script_plan rebuild")
        if STALE_SCRIPT_PLAN_REVISION_FIELD not in entry:
            raise ScriptPlanRebuildCompletionError("missing_baseline", "stale episode has no rebuild baseline")
        if entry.get(STALE_SCRIPT_PLAN_REVISION_FIELD) != fingerprint_of_revision(expected_stale_revision):
            raise ScriptPlanRebuildCompletionError(
                "baseline_conflict", "stale script_plan baseline changed; refresh workflow status"
            )
        path = script_plan_path(project_path, project, episode)
        revision = content_fingerprint(path) if path is not None else None
        if revision is None:
            raise ScriptPlanRebuildCompletionError("script_plan_missing", "rebuilt script_plan file is missing")
        entry[STALE_SCRIPT_PLAN_REBUILT_REVISION_FIELD] = revision
        committed["revision"] = revision

    pm.update_project(project_name, _commit)
    return committed["revision"]


def assert_base_fingerprint(path: Path, expected: str | _UncheckedFingerprint | None) -> None:
    """Optimistic check: mismatched ``expected`` and current ``path`` raise ``ScriptPlanWriteConflict`` without writes.

    ``expected`` is the file fingerprint captured by the writer (``None`` means absent then).
    ``UNCHECKED_FINGERPRINT`` skips checking. All three variants share these centralized comparison semantics.

    Callers must already hold the path's exclusive lock: comparison outside the subsequent write's critical
    section is merely a stale read.
    """
    if isinstance(expected, _UncheckedFingerprint):
        return
    actual = content_fingerprint(path)
    if expected == actual:
        return
    current = load_json_or_none(path)
    raise ScriptPlanWriteConflict(
        expected=expected,
        actual=actual,
        current_content=current if isinstance(current, dict) else None,
    )


def official_reference_script_plan_path(project_path: Path, episode: int) -> Path:
    """Formal reference-to-video script_plan path (``drafts/episode_N/script_plan_reference_units.json``).

    Unlike ``script_plan_path``, which chooses filenames by project variant and returns None when
    inapplicable, this is the rv write entry's path authority, independent of project dictionaries.
    """
    return episode_drafts_dir(project_path, episode) / REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME


@contextmanager
def formal_script_plan_lock(project_path: Path, episode: int, path: Path) -> Generator[Path]:
    """Write critical section for any formal script_plan variant: create directories, lock per path, yield its path.

    Share ``ProjectManager.file_lock`` with migration read/modify/write, Web saves, resplitting/promotion:
    per-path, interprocess-exclusive, nonreentrant. Enclose every read-then-write operation on formal files
    or drafts entirely here. Separate unlocked reads/writes create concurrent-overwrite windows.

    Callers supply paths by variant (``script_plan_path`` / ``official_reference_script_plan_path``).
    Three variants have different filenames; lock granularity is the file itself, never independently
    invented variant locks.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pm = ProjectManager.for_project_dir(project_path)
    with pm.file_lock(path):
        yield path


@contextmanager
def script_plan_write_lock(project_path: Path, episode: int) -> Generator[Path]:
    """Formal reference-to-video script_plan critical section: named ``formal_script_plan_lock`` entry bound to its path."""
    with formal_script_plan_lock(
        project_path, episode, official_reference_script_plan_path(project_path, episode)
    ) as path:
        yield path


@contextmanager
def formal_script_plan_write_transaction(
    project_path: Path,
    episode: int,
    *paths: Path,
    basis: ArtifactBasis | None = None,
    record_activity: bool = True,
) -> Generator[None]:
    """Commit formal script_plan files and their active Manifest claim as one unit.

    Callers own the canonical per-path lock.  Every Python write path for a
    drama, narration, or reference-video script_plan enters this context so a
    successful write refreshes the same typed claim, while registration
    failure restores every supplied formal file byte-for-byte.

    A committed write moves the project's last activity forward: script_plan
    files are JSON, whose modification time does not count as activity.  Pass
    ``record_activity=False`` for a write that leaves the content unchanged.
    Exits called inside the context pass their own activity recording up to
    this commit: the ledger is project-global and stays out of the rollback
    set, so other writers' records survive a failed commit.
    """

    with project_metadata_lock(project_path), formal_write_transaction(*paths):
        yield
        from lib.artifacts.artifact_activation import (
            register_current_artifact,
            register_current_artifact_if_provable,
        )
        from lib.artifacts.artifact_manifest import ArtifactKey

        # A successful no-op write can still repair a missing claim after a
        # temporarily unavailable source made activation skip this target.
        key = ArtifactKey.episode_script_plan(episode)
        if basis is None:
            register_current_artifact_if_provable(project_path, key)
        else:
            if not paths:
                raise ValueError("a frozen script_plan basis requires its formal artifact path")
            register_current_artifact(
                project_path,
                key,
                artifact_path=paths[0].relative_to(project_path).as_posix(),
                basis=basis,
            )
    if record_activity:
        record_project_activity(project_path)


def delete_script_plan_file(project_path: Path, episode: int, path: Path) -> bool:
    """Move formal script_plan to project trash and unregister its artifact within the same transaction."""

    pm = ProjectManager.for_project_dir(project_path)
    with pm.file_lock(path):
        if not path.exists():
            return False
        trash_script_plan_locked(project_path, episode, path)
    return True


def trash_script_plan_locked(project_path: Path, episode: int, path: Path) -> None:
    """Locked body of :func:`delete_script_plan_file`; callers already hold the file's per-path lock."""
    from lib.script.planning_draft_trash import trash_planning_draft

    record = None
    try:
        with formal_script_plan_write_transaction(project_path, episode, path):
            record = trash_planning_draft(project_path, episode, path)
    except BaseException:
        # On unregistration failure, transactions restored file bytes, so remove records; if restoration also fails, retain records without losing content.
        if record is not None and path.is_file():
            project_trash.purge(project_path, record.id)
        raise


def write_formal_script_plan_locked(
    project_path: Path,
    episode: int,
    path: Path,
    content: dict[str, Any],
    *,
    expected_fingerprint: str | _UncheckedFingerprint | None = UNCHECKED_FINGERPRINT,
    dependent_quarantine: str | None = None,
    clear_dependent_quarantine: bool = True,
    basis: ArtifactBasis | None = None,
) -> bool:
    """**Single formal script_plan write entry**, any variant: baseline OCC check → atomic write → clear invalid
    downstream drafts when content changes. Return whether content changed.

    Callers must already hold the file's exclusive lock (``formal_script_plan_lock`` or same-path
    ``ProjectManager.file_lock``). Locks are nonreentrant: callers inside the critical section cannot
    nest another. With ``dependent_quarantine``, acquire that draft's lock first; universal order is
    downstream draft → formal script_plan. All variants' Web saves, resplitting/renormalization, promotion,
    and migration writes converge here. Agent write bans protect this single locked write entry.

    ``expected_fingerprint`` is the formal fingerprint captured by the writer (``None`` means absent then).
    Mismatched current disk content raises ``ScriptPlanWriteConflict`` without writes, letting later writers
    merge conflict reports instead of silently overwriting earlier content. ``UNCHECKED_FINGERPRINT`` skips
    comparison: resplitting intentionally rebuilds entire documents, while same-critical-section migration /
    confirmation read-modify-write already has no concurrency window.

    ``dependent_quarantine`` names downstream drafts based on this file: only reference-to-video
    prompt_authoring, None for drama / narration with no downstream drafts. Include drafts in the same
    transaction, restoring both byte-for-byte on failure rather than leaving old formal files with cleared
    drafts. Invalidate only changed bases. Migration writes mechanically adopt formats, not edit content;
    callers retain drafts with ``clear_dependent_quarantine=False``.
    """
    assert_base_fingerprint(path, expected_fingerprint)
    previous = load_json_or_none(path)
    changed = previous != content
    quarantine = None if dependent_quarantine is None else quarantine_path(project_path, episode, dependent_quarantine)
    paths = (path,) if quarantine is None else (path, quarantine)
    from lib.script.planning_draft_trash import trash_planning_draft

    record = None
    try:
        with formal_script_plan_write_transaction(project_path, episode, *paths, basis=basis, record_activity=changed):
            atomic_write_json(path, content)
            if changed and clear_dependent_quarantine and quarantine is not None:
                # Invalidate only changed content: move drafts to project trash; transactions record content-change activity after commit.
                record = trash_planning_draft(project_path, episode, quarantine)
    except BaseException:
        # Transactions restored draft bytes, so remove corresponding records.
        # shortcut: If an outer formal_write_transaction wraps drafts and fails after this returns, it restores drafts while records remain (extra recoverable records, no content loss); strict handling creates records in the outermost transaction.
        if record is not None and quarantine is not None and quarantine.is_file():
            project_trash.purge(project_path, record.id)
        raise
    return changed


def write_script_plan_locked(
    project_path: Path,
    episode: int,
    content: dict[str, Any],
    *,
    expected_fingerprint: str | _UncheckedFingerprint | None = UNCHECKED_FINGERPRINT,
    clear_prompt_authoring_quarantine: bool = True,
    basis: ArtifactBasis | None = None,
) -> bool:
    """Formal reference-to-video write entry: named ``write_formal_script_plan_locked`` entry bound to this
    variant's path and downstream prompt_authoring draft.
    """
    return write_formal_script_plan_locked(
        project_path,
        episode,
        official_reference_script_plan_path(project_path, episode),
        content,
        expected_fingerprint=expected_fingerprint,
        dependent_quarantine=QUARANTINE_KIND_PROMPT_AUTHORING,
        clear_dependent_quarantine=clear_prompt_authoring_quarantine,
        basis=basis,
    )


def write_script_plan(
    project_path: Path,
    episode: int,
    content: dict[str, Any],
    *,
    expected_fingerprint: str | _UncheckedFingerprint | None = UNCHECKED_FINGERPRINT,
    clear_prompt_authoring_quarantine: bool = True,
    basis: ArtifactBasis | None = None,
    before_lock: Callable[[], None] | None = None,
) -> bool:
    """Run the reference script_plan transaction in global lock order: prompt_authoring draft, then formal script_plan."""
    prompt_authoring_path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    pm = ProjectManager.for_project_dir(project_path)
    if before_lock is not None:
        before_lock()
    with pm.file_lock(prompt_authoring_path), script_plan_write_lock(project_path, episode):
        return write_script_plan_locked(
            project_path,
            episode,
            content,
            expected_fingerprint=expected_fingerprint,
            clear_prompt_authoring_quarantine=clear_prompt_authoring_quarantine,
            basis=basis,
        )


def stored_review(project: dict[str, Any], episode: int) -> dict[str, Any]:
    """Stored episode confirmation (``episodes[i].script_plan_review``), or empty dict if absent/malformed."""
    ep = find_episode(project, episode)
    review = ep.get(REVIEW_FIELD) if ep else None
    return review if isinstance(review, dict) else {}


@dataclass(frozen=True, slots=True)
class OverwrittenScriptEntry:
    """A formal entry removed by overwrite confirmation, its artifacts unrecoverable within the project, and their ownership."""

    entry_id: str
    has_storyboard: bool
    has_video: bool
    has_narration_audio: bool
    has_end_frame: bool
    grid_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.entry_id,
            "has_storyboard": self.has_storyboard,
            "has_video": self.has_video,
            "has_narration_audio": self.has_narration_audio,
            "has_end_frame": self.has_end_frame,
            "grid_id": self.grid_id,
        }


@dataclass(frozen=True, slots=True)
class FormalScriptOverwrite:
    """Formal script overwritten by content confirmation: remove all old entries, unregister artifacts as for
    storyboard removal, and move artifacts plus version histories to project trash.

    ``fingerprint`` captures formal content when read and is public ``revision``. Callers echo it when
    accepting overwrite; mismatch at confirmation means unaccepted, refusing again with a new list.
    Writes also use it as baseline, so acceptance covers only this listed content.
    """

    fingerprint: str
    entries: tuple[OverwrittenScriptEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": published_revision(self.fingerprint),
            "entries": [entry.to_dict() for entry in self.entries],
            "storyboard_count": sum(entry.has_storyboard for entry in self.entries),
            "video_count": sum(entry.has_video for entry in self.entries),
            "narration_audio_count": sum(entry.has_narration_audio for entry in self.entries),
            "end_frame_count": sum(entry.has_end_frame for entry in self.entries),
            "grid_member_count": sum(entry.grid_id is not None for entry in self.entries),
            "grid_count": len({entry.grid_id for entry in self.entries if entry.grid_id is not None}),
        }


#: Per-item artifact kinds in overwrite lists: ``to_dict`` item keys → copy keys, in presentation order.
_OVERWRITE_ENTRY_KINDS: tuple[tuple[str, str], ...] = (
    ("has_storyboard", "script_overwrite_kind_storyboard"),
    ("has_video", "script_overwrite_kind_video"),
    ("has_narration_audio", "script_overwrite_kind_narration_audio"),
    ("has_end_frame", "script_overwrite_kind_end_frame"),
    ("grid_id", "script_overwrite_kind_grid"),
)

#: Overwrite summary items: (count key, copy key). Omit zero-count items.
_OVERWRITE_LOSS_TOTALS: tuple[tuple[str, str], ...] = (
    ("storyboard_count", "script_overwrite_loss_storyboard"),
    ("video_count", "script_overwrite_loss_video"),
    ("narration_audio_count", "script_overwrite_loss_narration_audio"),
    ("end_frame_count", "script_overwrite_loss_end_frame"),
    ("grid_member_count", "script_overwrite_loss_grid"),
)


def render_overwrite_loss_text(overwrite: Mapping[str, Any], translate: Callable[..., str]) -> str:
    """Render ``FormalScriptOverwrite.to_dict()`` as a textual loss inventory.

    Web confirmation dialogs and Agent ``script_overwrite_required`` receipts share this text, registered
    only in i18n key tables; callers never separately assemble inventories.
    """
    entries: list[Mapping[str, Any]] = list(overwrite.get("entries") or ())
    separator = translate("script_overwrite_separator")
    lines = [translate("script_overwrite_summary", count=len(entries))]
    totals = [
        translate(key, count=count, grids=overwrite.get("grid_count", 0))
        for count_key, key in _OVERWRITE_LOSS_TOTALS
        if (count := int(overwrite.get(count_key) or 0)) > 0
    ]
    if totals:
        lines.append(translate("script_overwrite_loss", items=separator.join(totals)))
    if entries:
        lines.append(translate("script_overwrite_history"))
        rendered: list[str] = []
        for entry in entries:
            kinds = [translate(key) for field, key in _OVERWRITE_ENTRY_KINDS if entry.get(field)]
            rendered.append(
                translate("script_overwrite_entry", id=entry.get("id", ""), kinds=separator.join(kinds))
                if kinds
                else str(entry.get("id", ""))
            )
        lines.append(translate("script_overwrite_entries", entries=separator.join(rendered)))
    return "\n".join(lines)


def overwrite_with_text(overwrite: Mapping[str, Any] | None, translate: Callable[..., str]) -> dict[str, Any] | None:
    """Attach rendered loss inventory ``text`` to overwrite inventories, or None without overwrite."""
    if overwrite is None:
        return None
    return {**overwrite, "text": render_overwrite_loss_text(overwrite, translate)}


def _bound_script_filename(project: Mapping[str, Any], episode: int) -> str | None:
    """Episode project.json script binding normalized under ``scripts/``, or None without binding."""

    entry = find_episode(project, episode)
    binding = entry.get("script_file") if isinstance(entry, dict) else None
    if not isinstance(binding, str) or not binding:
        return None
    return ProjectManager.normalize_script_filename(binding) or None


class ForeignFormalScriptError(ValueError):
    """No readable/writable formal location: the binding is absent from disk, while canonical paths hold another episode's script.

    Carry episode ID and occupying filename for actionable caller messages. Inherit ``ValueError`` so
    callers without dedicated handling still reject rather than succeed.
    """

    def __init__(self, episode: int, filename: str) -> None:
        super().__init__(
            f"集（id={episode}）的规范剧本路径 scripts/{filename} 上是另一集的剧本，不能当作本集正式脚本读写"
        )
        self.episode = episode
        self.filename = filename


def formal_script_filename(project_path: Path, project: Mapping[str, Any], episode: int) -> str:
    """Formal episode script filename under ``scripts/``.

    Use project.json ``script_file`` when its bound file exists. Missing/out-of-bounds bindings or absent
    files fall back to canonical ``episode_N.json``. Overwrite-list reads and confirmed writes both use
    this resolver, always identifying the same file.

    Fallback applies only to empty canonical paths or this episode's actual scripts. Episodes skipped
    during migration may bind vanished legacy paths while canonical paths hold another episode's script;
    unconditional fallback would read that other content, rebuild its live script, and bind this episode
    to it. Raise ``ForeignFormalScriptError``. Ownership checks (unreadable as an object or mismatched
    internal episode numbers) match validation before v14→v15 renaming.
    """
    filename = _bound_script_filename(project, episode)
    if filename is not None:
        path = try_safe_join(project_path / "scripts", filename)
        if path is not None and path.is_file():
            return filename
    canonical = episode_script_filename(episode)
    canonical_path = project_path / "scripts" / canonical
    if canonical_path.is_file():
        script = load_json_or_none(canonical_path)
        # Require strictly positive integer episode numbers: raw JSON ``true`` becomes Python ``True``, both an ``int``
        # and equal to ``1``. Comparing only with ``!=`` would let dirty files impersonate episode 1 during ownership checks.
        recorded = script.get("episode") if isinstance(script, dict) else None
        if type(recorded) is not int or recorded != episode:
            raise ForeignFormalScriptError(episode, canonical)
    return canonical


def formal_script_overwrite(
    project_path: Path, project: Mapping[str, Any], episode: int
) -> FormalScriptOverwrite | None:
    """Read the formal script content confirmation will overwrite; None if this episode has no formal script.

    Read ``formal_script_filename``'s resolved file, exactly where confirmation writes. Existing unreadable
    scripts (invalid JSON or broken item arrays) still count as formal scripts with empty item lists:
    overwriting still requires acceptance.

    Return None when canonical paths hold another episode's script: it cannot populate this episode's
    overwrite inventory. Confirmation rejects before writing with ``ForeignFormalScriptError``.
    """
    try:
        filename = formal_script_filename(project_path, project, episode)
    except ForeignFormalScriptError:
        return None
    path = project_path / "scripts" / filename
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return FormalScriptOverwrite(fingerprint=hashlib.sha256(raw).hexdigest(), entries=())
    fingerprint = content_fingerprint_of_data(parsed)
    if not isinstance(parsed, dict):
        return FormalScriptOverwrite(fingerprint=fingerprint, entries=())
    try:
        items, id_field, _kind = resolve_items(parsed)
    except ScriptEditError:
        items, id_field = [], ""
    entries: list[OverwrittenScriptEntry] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(id_field), str) or not item[id_field]:
            continue
        assets = get_generated_assets(item)
        grid_id = assets.get("grid_id")
        entries.append(
            OverwrittenScriptEntry(
                entry_id=item[id_field],
                has_storyboard=bool(assets.get("storyboard_image")),
                has_video=bool(assets.get("video_clip")),
                has_narration_audio=bool(assets.get("narration_audio")),
                has_end_frame=bool(item.get("end_frame_image")),
                grid_id=grid_id if isinstance(grid_id, str) and grid_id else None,
            )
        )
    return FormalScriptOverwrite(fingerprint=fingerprint, entries=tuple(entries))


def prompt_authoring_generated(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    """Whether this episode's prompt_authoring artifact (generated script JSON) already exists.

    Existing bindings recognize only their own file, never ``formal_script_filename`` fallback. Absent
    bindings' files plus another episode's canonical file are precisely migration-reported skipped cases,
    never evidence of existing output. Without binding, check canonical paths.
    """
    filename = _bound_script_filename(project, episode) or episode_script_filename(episode)
    path = try_safe_join(project_path / "scripts", filename)
    return path is not None and path.is_file()


def review_status(project_path: Path, project: dict[str, Any], episode: int) -> ReviewStatus:
    """Derive episode content-confirmation state.

    - No script_plan or inapplicable gate: not_applicable / no_script_plan;
    - Stored confirmation matching live script_plan fingerprints: confirmed; mismatch after edits: pending_review;
    - No confirmation fingerprint: pending_review, even with formal scripts present, whose origin cannot
      otherwise be tied to the current plan.
    """
    path = script_plan_path(project_path, project, episode)
    if path is None:
        return "not_applicable"
    # Draft presence precedes fingerprint checks: invalid output still awaits handling. Whether formal files are absent,
    # old, or confirmed, the episode has no releasable script_plan. Use pending_review instead of a new state:
    # prompt_authoring blockers and Web displays need that same unreleased flag; a new state would affect every consumer.
    if script_plan_quarantined(project_path, project, episode):
        return "pending_review"
    live = content_fingerprint(path)
    if live is None:
        return "no_script_plan"
    return "confirmed" if _formal_script_plan_confirmed(project_path, project, episode, live) else "pending_review"


def _formal_script_plan_confirmed(project_path: Path, project: dict[str, Any], episode: int, live: str) -> bool:
    stored_fingerprint = stored_review(project, episode).get("fingerprint")
    if stored_fingerprint is not None:
        return stored_fingerprint == live
    # Without confirmation fingerprints, formal scripts cannot be tied to a plan, so the current one is unconfirmed.
    # v14→v15 migrations backfill existing episode baselines. Starting blank deletes confirmation records and also reaches here.
    return False


def formal_script_plan_confirmed(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    """Whether formal planning is exactly the confirmed version: read-only, with content edits made in formal scripts.

    Match ``review_status`` but ignore repair drafts. Drafts from rerunning planning do not change the
    confirmed formal plan, which remains read-only. New written content changes fingerprints and restores
    editability.
    """
    path = script_plan_path(project_path, project, episode)
    if path is None:
        return False
    live = content_fingerprint(path)
    return live is not None and _formal_script_plan_confirmed(project_path, project, episode, live)


def formal_script_plan_agent_owned(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    """Formal planning is read-only while Agent-retrieved editable drafts exist; callers check under its formal-plan lock."""
    kind = script_plan_quarantine_kind(project)
    draft = read_quarantine(project_path, episode, kind) if kind is not None else None
    return draft is not None and draft_owner(draft) == DRAFT_OWNER_AGENT


def apply_confirmation(project: dict[str, Any], episode: int, fingerprint: str, confirmed_at: str) -> bool:
    """Write project ``episodes[i].script_plan_review`` in place; return False for missing episode entries.

    Services call inside ProjectManager.update_project RMW callbacks. This is the single authority for
    persisted confirmation-fingerprint shapes.
    """
    ep = find_episode(project, episode)
    if ep is None:
        return False
    ep[REVIEW_FIELD] = {"fingerprint": fingerprint, "confirmed_at": confirmed_at}
    return True


def carry_confirmation_through_migration(project: dict[str, Any], episode: int, before: str, after: str) -> bool:
    """Duration adoption for existing script_plan drafts mechanically changes formats, not content, but writes change fingerprints.

    If episode confirmation matches premigration content (``before``), shift it to migrated ``after``
    values, avoiding renewed confirmation solely due to migration. Leave already mismatched fingerprints
    (actual edits outside migration) unchanged, return False, and retain pending confirmation.

    Apply only without migration warnings: pure format adoption without clamped duration changes. Callers
    must skip this function for warnings so confirmation expires normally. Changed durations in ``after``
    differ from what users reviewed; shifting confirmation would silently approve unreviewed content.

    Call inside locked ``ProjectManager.update_project`` callbacks against latest reread project state.
    Both migration write entries in ``server/services/project/script_review.py`` and
    ``lib/script/script_generator.py`` share it rather than duplicate checks. Cascading asset/derivative
    rename and merge rewrites also change names only, not content, and shift confirmations here through
    ``ProjectManager._carry_script_plan_confirmations``.
    """
    stored = stored_review(project, episode)
    if stored.get("fingerprint") != before:
        return False
    apply_confirmation(project, episode, after, str(stored.get("confirmed_at") or ""))
    return True


def migrate_script_plan_draft_in_place(
    project_path: Path,
    content: object,
    *,
    episode: int,
    update_project: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]],
    supported_durations: Sequence[int] | None = None,
) -> tuple[dict[str, Any] | None, list[ValidationMessage]]:
    """Perform one-time duration adoption in an in-memory script_plan draft and write back; return ``(最新 project, warnings)``.

    Callers hold both exclusive locks in prompt_authoring draft → formal script_plan order. Use the single
    ``write_script_plan_locked`` entry, sharing Web saves/resplitting's per-path lock. Mechanical format
    adoption does not invalidate prompt_authoring drafts; read-modify-write inside one critical section
    also needs no baseline check. Without migration, do not write; return ``(None, [])``.

    Usually migration changes formats mechanically and shifts fingerprints. Use locked ``update_project``
    to shift episode confirmation through ``carry_confirmation_through_migration``, avoiding renewed review
    solely on loading. Nonempty ``warnings`` mean tier/structural-range clamps changed actual durations:
    content edits, so do not shift confirmation. Fingerprint comparisons make confirmed episodes await
    review normally; episodes without stored fingerprints explicitly record premigration fingerprints,
    likewise ensuring mismatch and review. Persist these markers for both dry-run and real generation:
    idempotently persisted migrations emit no warnings on retries, so persisted markers alone preserve
    content-confirmation blocking afterward.

    Persist project confirmation markers before drafts. Interruptions leave premigration drafts whose
    next load reruns migration and self-heals. Reversing order loses old fields; reruns find ``changed=False``,
    permanently losing markers.

    With ``supported_durations`` from prompt_authoring's model tiers, map directly to tiers. Without them
    on Web gates (async + DB capabilities unavailable synchronously), clamp structural ranges only.

    Both script_plan read entries in ``lib.script.script_generator.ScriptGenerator`` and
    ``server.services.project.script_review`` share migration/confirmation-shifting checks here.
    """
    if not isinstance(content, dict):
        return None, []
    before = content_fingerprint_of_data(content)
    changed, warnings = migrate_unit_durations(content.get("units"), supported_durations=supported_durations)
    for message in warnings:
        logger.warning(
            "script_plan 草稿 %s 时长收编迁移: %s",
            REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
            message.render(default_translate),
        )
    if not changed:
        return None, []

    if warnings:

        def _invalidate_unconfirmed(p: dict[str, Any]) -> None:
            # Leave episodes with existing fingerprints alone: premigration confirmations already mismatch naturally;
            # confirmations of other content already produce pending_review in review_status.
            stored = stored_review(p, episode)
            if not stored.get("fingerprint"):
                apply_confirmation(p, episode, before, str(stored.get("confirmed_at") or ""))

        updated = update_project(_invalidate_unconfirmed)
    else:
        after = content_fingerprint_of_data(content)

        def _carry(p: dict[str, Any]) -> None:
            carry_confirmation_through_migration(p, episode, before, after)

        updated = update_project(_carry)

    write_script_plan_locked(project_path, episode, content, clear_prompt_authoring_quarantine=False)
    return updated, warnings
