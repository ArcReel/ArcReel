"""Drafts from script_plan / prompt_authoring: persisted envelopes, violation reports, and promotion rules.

Generation costs money. Discarding violations and resampling wastes money without convergence:
the same model is likely to repeat the same mistakes on the same source. Leave formal files unchanged;
persist invalid output with itemized reports in a same-directory draft. Agents read with
``open_draft``, repair ``content`` with ``patch_draft`` (or register assets/use registered names), then
invoke promotion for complete revalidation with **the same validator**. Passing promotes to formal files
and clears the draft; failing refreshes reports for further repairs. No repair-round limit: each round
uses specific locations, rather than gambling on resampling.

The same draft slot also serves as an **editing workspace**. Formal script_plan files cannot be edited
through Write/Edit: they share a per-path lock with Web saves, migration read/modify/write, and resplitting,
but Agent file tools cannot obtain it. Retrieve a draft of finalized script_plan, edit, then persist through
the same promotion path. Both uses share envelopes/promotion rules: origins differ, but content stays in
drafts and writes occur only during locked promotion. Separate mechanisms would split gate/generation
understanding into incompatible halves.

Drafts contain **the shape of that model-output step**, not the persisted shape. Machine-derived fields
(reference-to-video ``unit_id`` / ``shots`` / ``references``, drama ``needs_replan``) never enter drafts;
letting Agents edit derived data invites drift.

Envelope shape::

    {"kind": ..., "episode": N, "meta": {..., "schema_version": V},
     "violations": [{"code","label","message", "item_index"?, "item_id"?, "params"?}, ...], "content": {...}}

``violations`` snapshots the last validation for Agent location/context and Web display. Promotion always
revalidates current ``content``, never trusting stored reports. ``meta`` holds context unavailable from
project state: script_plan source paths (reparsing all ``source/`` during promotion would loosen source-anchor
substring checks versus generation) and formal-file content fingerprints at generation/retrieval
(``base_fingerprint``, the optimistic-concurrency baseline before promotion).

Another ``meta`` key, ``schema_version``, versions the envelope rather than validation context.
``quarantine_envelope`` writes it centrally; ``read_quarantine`` parses it into
``QuarantinedDraft.schema_version`` and removes it from ``meta``. Consumers use parsed results, not raw
``meta`` keys, leaving only validation context in ``meta`` and keeping ``draft_revision`` independent of versions.
Missing means v1; see ``resolve_schema_version``. This module has no migration framework: version values
provide evidence when ``meta`` key semantics change, and no current branch dispatches by version.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.episode.episode_paths import (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    episode_drafts_dir,
)
from lib.infra.content_digest import prefixed_canonical_json_digest
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.project.project_activity import record_project_activity
from lib.project.project_change_hints import emit_project_change_hint
from lib.script.draft_violation import DraftViolation, render_violation_report

#: Draft origins. ``content`` matches that step's model-output schema: reference-to-video script_plan is
#: ``{units: [{duration_seconds, source_text, text}]}``, prompt_authoring ``{title, units: [{text}]}``,
#: drama script_plan ``{title, scenes: [...]}`` (``DramaNormalizedScript`` minus machine-derived
#: ``needs_replan``), and narration script_plan ``{segments: [...]}`` (``NarrationScriptPlanDraft``,
#: with no machine-derived fields, so draft and persisted layers share the same shape).
QUARANTINE_KIND_SCRIPT_PLAN = "reference_video_script_plan"
QUARANTINE_KIND_PROMPT_AUTHORING = "reference_video_prompt_authoring"
QUARANTINE_KIND_DRAMA_SCRIPT_PLAN = "drama_script_plan"
QUARANTINE_KIND_NARRATION_SCRIPT_PLAN = "narration_script_plan"

_QUARANTINE_FILENAMES: dict[str, str] = {
    QUARANTINE_KIND_SCRIPT_PLAN: REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    QUARANTINE_KIND_PROMPT_AUTHORING: REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
}

#: All draft filenames for consumers locating drafts without kinds, such as cascading asset-rename rewrite lists.
#: Derive from the table rather than maintaining another list: new origins need one row. Omitted registration
#: silently excludes drafts from rewrite lists; their reference arrays and ``@[名称]`` bodies then miss rewrites,
#: blocking promotion with unregistered-reference errors.
QUARANTINE_FILENAMES: frozenset[str] = frozenset(_QUARANTINE_FILENAMES.values())

#: Field-edit instructions vary by origin because bodies differ; nonexistent field names misdirect Agents
#: to uneditable locations. Register alongside filenames, adding one row here for each new origin.
_QUARANTINE_REPORT_HINTS: dict[str, tuple[str, str]] = {
    QUARANTINE_KIND_SCRIPT_PLAN: (
        "script_plan 拆分",
        "content（按报告字段路径修复；视频单元级字段位于 content.units[i]）",
    ),
    QUARANTINE_KIND_PROMPT_AUTHORING: ("prompt_authoring 提示词编写", "content.units[i].text"),
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: (
        "script_plan 规范化",
        "content（分镜级字段位于 content.scenes[i]）",
    ),
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: (
        "script_plan 分镜拆分",
        "content（分镜级字段位于 content.segments[i]："
        "novel_text / duration_seconds / segment_break / characters_in_segment / scenes / props）",
    ),
}

#: Envelope version written by this code in ``meta.schema_version``. Increment when changing existing
#: ``meta`` key semantics so readers can distinguish old/new drafts; adding keys alone needs no increment.
QUARANTINE_SCHEMA_VERSION = 1

_SCHEMA_VERSION_KEY = "schema_version"

#: Promotion tool name, named by report instructions; define centrally rather than repeating across call sites.
PROMOTE_TOOL_NAME = "promote_draft"

#: Tool retrieving formal script_plan for editing. Formal files share a per-path lock with Web saves,
#: migrations, and resplitting. Sandboxed Agent Write/Edit cannot obtain it, so edits always retrieve drafts,
#: modify, then promote. Only promotion writes, under the same lock as those three paths. Write-denial
#: messages must also name the tool, so centralize it here.
OPEN_DRAFT_TOOL_NAME = "open_draft"

#: Draft meta marker for editable drafts retrieved from formal content through Agent ``open_draft``,
#: rather than generation-produced repair drafts. Confirmed script plans are read-only; this marker governs
#: refusal to modify/promote editing copies. Web shows editable-draft status only, never their content.
FORMAL_EDIT_META_KEY = "formal_edit"

#: Draft handlers: ``user`` repair drafts allow creator edits; ``agent`` identifies Agent-editable drafts.
DRAFT_OWNER_USER = "user"
DRAFT_OWNER_AGENT = "agent"

DOC_TYPE_DRAMA_SCRIPT_PLAN = "drama_script_plan"
DOC_TYPE_NARRATION_SCRIPT_PLAN = "narration_script_plan"
DOC_TYPE_REFERENCE_SCRIPT_PLAN = "reference_script_plan"
DOC_TYPE_REFERENCE_PROMPT_AUTHORING = "reference_prompt_authoring"

DOC_TYPE_TO_QUARANTINE_KIND: dict[str, str] = {
    DOC_TYPE_DRAMA_SCRIPT_PLAN: QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    DOC_TYPE_NARRATION_SCRIPT_PLAN: QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    DOC_TYPE_REFERENCE_SCRIPT_PLAN: QUARANTINE_KIND_SCRIPT_PLAN,
    DOC_TYPE_REFERENCE_PROMPT_AUTHORING: QUARANTINE_KIND_PROMPT_AUTHORING,
}

QUARANTINE_KIND_TO_DOC_TYPE: dict[str, str] = {kind: doc_type for doc_type, kind in DOC_TYPE_TO_QUARANTINE_KIND.items()}


@dataclass(frozen=True)
class QuarantinedDraft:
    """An in-memory draft: ``content`` is flat output awaiting revalidation; ``violations`` snapshots the previous report.

    ``schema_version`` is the parsed envelope version (missing means ``1``; see ``resolve_schema_version``).
    This key is removed from ``meta``: this field owns versioning, while ``meta`` owns validation context.
    """

    kind: str
    episode: int
    content: dict[str, Any]
    violations: list[dict[str, Any]]
    meta: dict[str, Any]
    schema_version: int
    path: Path


def draft_owner(draft: QuarantinedDraft) -> str:
    """Draft handler: editable-draft markers assign ownership to the Agent; other generation-produced repair drafts belong to creators."""
    return DRAFT_OWNER_AGENT if draft.meta.get(FORMAL_EDIT_META_KEY) is True else DRAFT_OWNER_USER


def resolve_schema_version(meta: dict[str, Any]) -> int:
    """Parse the schema version from persisted raw ``meta``, interpreting missing/untrustworthy versions as ``1``.

    The sole envelope-version authority: envelope readers use this, other consumers use
    ``QuarantinedDraft.schema_version``. Neither independently reads ``meta["schema_version"]``;
    scattered interpretations would invent fallback rules at each call site. Input is persisted ``meta``,
    not ``QuarantinedDraft.meta``, whose version key was removed and would always return ``1`` here.

    Rules:

    * **Missing**: envelopes without a version parse as ``1``; these drafts have v1 semantics.
    * **Nonintegers** (``bool`` / float / numeric strings, etc.) and **integers below 1** also parse as ``1``.
      An untrustworthy version proves only that this code did not write it, while only v1 semantics exist.
      Rejecting it would hide a draft that Agents could still repair/promote, a far greater cost than
      reading the earliest semantics.
    * **Future versions above ``QUARANTINE_SCHEMA_VERSION``** return unchanged, neither clamped nor rejected.
      No migration framework or version-based dispatch exists here. Early clamping disguises newer drafts
      as current ones; actual future semantic changes must decide compatibility versus refusal when needed,
      rather than preempting that decision here.
    """
    version = meta.get(_SCHEMA_VERSION_KEY)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return 1
    return version


def quarantine_envelope(
    kind: str,
    episode: int,
    *,
    content: dict[str, Any],
    violations: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a complete persistable envelope, stamping current ``meta.schema_version``.

    Envelopes are constructed only here, centralizing which version/key to stamp so writers cannot forget.
    Always override caller ``meta`` containing ``schema_version`` (such as edited, reread drafts) with the
    current version: this code writes current semantics, and retaining an old version would mislabel them.
    """
    return {
        "kind": kind,
        "episode": episode,
        "meta": {**(meta or {}), _SCHEMA_VERSION_KEY: QUARANTINE_SCHEMA_VERSION},
        "violations": violations,
        "content": content,
    }


def draft_revision(draft: QuarantinedDraft) -> str:
    """Return the canonical optimistic-concurrency token for persisted draft state.

    Hash the ``quarantine_envelope`` persisted shape minus its version key: ``meta`` comes from
    ``QuarantinedDraft.meta`` without ``schema_version``, keeping token scope version-independent.
    Update this alongside persisted-shape changes.
    """
    return prefixed_canonical_json_digest(
        {
            "kind": draft.kind,
            "episode": draft.episode,
            "meta": draft.meta,
            "violations": draft.violations,
            "content": draft.content,
        }
    )


def draft_payload(draft: QuarantinedDraft) -> dict[str, Any]:
    """Return the host-independent draft document exposed by MCP adapters."""
    return {
        "episode": draft.episode,
        "doc_type": QUARANTINE_KIND_TO_DOC_TYPE[draft.kind],
        "content": draft.content,
        "violations": draft.violations,
        "revision": draft_revision(draft),
    }


def quarantine_filename(kind: str) -> str:
    """Filename of this stage's draft inside its episode directory."""
    return _QUARANTINE_FILENAMES[kind]


def quarantine_path(project_path: Path, episode: int, kind: str) -> Path:
    """Draft path for this episode/stage, alongside formal script_plan."""
    return episode_drafts_dir(project_path, episode) / quarantine_filename(kind)


def violation_entries(violations: list[DraftViolation]) -> list[dict[str, Any]]:
    """Convert violation exceptions to persisted/display entries: category, location prefix, message, optional line/item location."""
    entries: list[dict[str, Any]] = []
    for violation in violations:
        entry: dict[str, Any] = {
            "code": violation.code,
            "label": violation.label,
            "message": str(violation),
            "line": violation.line,
        }
        if violation.locations:
            entry["locations"] = list(violation.locations)
        if violation.reason is not None:
            entry["reason"] = violation.reason
        if violation.action is not None:
            entry["action"] = violation.action
        if violation.item_index is not None:
            entry["item_index"] = violation.item_index
        if violation.item_id is not None:
            entry["item_id"] = violation.item_id
        if violation.params is not None:
            entry["params"] = violation.params
        entries.append(entry)
    return entries


def announce_draft_change(project_path: Path, path: Path) -> None:
    """Emit one project-change hint with the current origin after writing or deleting a draft.

    Drafts bypass ProjectManager writes; event services discover them by polling and take origins from
    hints arriving in the same scan window. Emit immediately after writes so Web manual saves count as
    ``webui``. Agents/text tasks set no origin and still broadcast as ``filesystem``.
    """
    emit_project_change_hint(project_path.name, changed_paths=[path.relative_to(project_path).as_posix()])


def write_quarantine(
    project_path: Path,
    episode: int,
    kind: str,
    *,
    content: dict[str, Any],
    violations: list[DraftViolation],
    meta: dict[str, Any] | None = None,
) -> Path:
    """Atomically replace the entire draft with invalid output and its report; return its path.

    Replace rather than merge: resampling or rerunning promotion creates new output, and merging leftovers
    makes Agents repair half-old/half-new bodies. Create directories first because the episode may never
    have produced script_plan.
    """
    path = quarantine_path(project_path, episode, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        quarantine_envelope(
            kind,
            episode,
            content=content,
            violations=violation_entries(violations),
            meta=meta,
        ),
    )
    announce_draft_change(project_path, path)
    record_project_activity(project_path)
    return path


def read_quarantine(project_path: Path, episode: int, kind: str) -> QuarantinedDraft | None:
    """Read a draft; return None for missing files, invalid JSON, or malformed envelopes.

    Treat malformed files as absent drafts rather than raising: existing files or interruptions can leave
    invalid JSON. Callers suggest resplitting instead of internal errors, but ``exists`` remains true:
    gates/generation still block, never treating broken drafts as absent to permit generation.

    ``kind`` / ``episode`` must match the requested draft; missing/mismatched values also mean malformed.
    Otherwise parsing and discarding those fields would let envelopes copied from other episodes validate
    anchors against their own ``meta.source``, rebuild with this episode's unit_id values, and overwrite
    this episode's formal script_plan.
    """
    path = quarantine_path(project_path, episode, kind)
    data = load_json_or_none(path)
    if not isinstance(data, dict):
        return None
    content = data.get("content")
    if not isinstance(content, dict):
        return None
    if data.get("kind") != kind:
        return None
    try:
        if int(data["episode"]) != episode:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    raw_violations = data.get("violations")
    violations = [v for v in raw_violations if isinstance(v, dict)] if isinstance(raw_violations, list) else []
    raw_meta = data.get("meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    return QuarantinedDraft(
        kind=kind,
        episode=episode,
        content=content,
        violations=violations,
        meta={key: value for key, value in meta.items() if key != _SCHEMA_VERSION_KEY},
        schema_version=resolve_schema_version(meta),
        path=path,
    )


def quarantine_exists(project_path: Path, episode: int, kind: str) -> bool:
    """Whether this episode/stage has a draft, without parsing: the predicate for gate blocking/generation refusal."""
    return quarantine_path(project_path, episode, kind).exists()


def clear_quarantine(project_path: Path, episode: int, kind: str, *, record_activity: bool = True) -> bool:
    """Clear a successfully promoted draft, returning whether deletion occurred and advancing project activity if so. Discard through :func:`lib.script.planning_draft_trash.discard_quarantine`.

    Silently tolerate absence: direct reruns may promote without drafts. Inside outer write transactions,
    pass ``record_activity=False`` and record after outer commit: the project-global ledger does not roll back.
    """
    path = quarantine_path(project_path, episode, kind)
    try:
        # hard-delete: temp -- promotion staging draft; content is already persisted in the formal file
        path.unlink()
    except FileNotFoundError:
        return False
    announce_draft_change(project_path, path)
    if record_activity:
        record_project_activity(project_path)
    return True


def render_report(draft: Path, kind: str, violations: list[DraftViolation], *, episode: int) -> str:
    """Render Agent violation reports with item locations and handling-path repair instructions. ``draft`` is project-relative.

    Specify which field/file to edit and what to invoke afterward, rather than generic repair requests.
    Handling paths are the mechanism's point: Agents unaware that output remains on disk will resample.
    """
    stage, field = _QUARANTINE_REPORT_HINTS[kind]
    doc_type = QUARANTINE_KIND_TO_DOC_TYPE[kind]
    return (
        f"❌ {stage}产出有 {len(violations)} 处违约，已保存为待修复草稿（正式文件未被改动）：{draft.as_posix()}\n\n"
        f"{render_violation_report(violations)}\n\n"
        f'处置：调用 open_draft({{"episode_id": {episode}, "doc_type": "{doc_type}"}}) 读取正文与 revision；'
        f"修正 {field} 后用 patch_draft 携带 base_revision 提交；"
        "若违约是「资产名未登记」，也可改为在 project.json 登记该资产、或改用已登记的名称。\n"
        f'改完调用 {PROMOTE_TOOL_NAME}({{"episode_id": {episode}, "doc_type": "{doc_type}", '
        '"base_revision": "<patch_draft 返回的 revision>"}) '
        "重新全量校验并晋升为正式文件；"
        "仍有违约时会返回刷新后的报告，可继续修改再晋升，无轮次上限。"
    )


def quarantine_and_report(
    project_path: Path,
    episode: int,
    kind: str,
    *,
    content: dict[str, Any],
    violations: list[DraftViolation],
    meta: dict[str, Any] | None = None,
) -> str:
    """Single violation-handling entry: persist draft and render its report, returning Agent report text.

    Persistence and reporting are paired: reports name draft paths determined by writes. Splitting these
    across call sites eventually produces instructions pointing to files never written.
    """
    path = write_quarantine(project_path, episode, kind, content=content, violations=violations, meta=meta)
    # Reports return verbatim to potentially remote Agents: use project-relative draft paths, never leak server layout.
    return render_report(path.relative_to(project_path), kind, violations, episode=episode)


__all__ = [
    "DOC_TYPE_DRAMA_SCRIPT_PLAN",
    "DOC_TYPE_NARRATION_SCRIPT_PLAN",
    "DOC_TYPE_REFERENCE_PROMPT_AUTHORING",
    "DOC_TYPE_REFERENCE_SCRIPT_PLAN",
    "DOC_TYPE_TO_QUARANTINE_KIND",
    "DRAFT_OWNER_AGENT",
    "DRAFT_OWNER_USER",
    "FORMAL_EDIT_META_KEY",
    "OPEN_DRAFT_TOOL_NAME",
    "PROMOTE_TOOL_NAME",
    "QUARANTINE_FILENAMES",
    "QUARANTINE_KIND_DRAMA_SCRIPT_PLAN",
    "QUARANTINE_KIND_NARRATION_SCRIPT_PLAN",
    "QUARANTINE_KIND_PROMPT_AUTHORING",
    "QUARANTINE_KIND_SCRIPT_PLAN",
    "QUARANTINE_SCHEMA_VERSION",
    "QuarantinedDraft",
    "clear_quarantine",
    "draft_owner",
    "draft_payload",
    "draft_revision",
    "quarantine_and_report",
    "quarantine_envelope",
    "quarantine_exists",
    "quarantine_filename",
    "quarantine_path",
    "read_quarantine",
    "render_report",
    "resolve_schema_version",
    "violation_entries",
    "write_quarantine",
]
