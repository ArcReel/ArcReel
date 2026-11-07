"""Sole script_plan intermediate-file/episode-script path authority.

Content review/status/Web drafts/generators/SDK text tools share path mappings.
Absent plans allow prompt_authoring as ``no_script_plan``, not faults, so every reader/writer
must share filenames/directories. Register new structured two-stage content modes in
``SCRIPT_PLAN_FILENAMES`` to align confirmation/status/Web/disk writes.

Preserve these semantic differences:

- Content confirmation recognizes structured ``.json`` only, ``SCRIPT_PLAN_FILENAMES`` / ``script_plan_filename``;
- Status/Web readers also recognize legacy ``.md`` aliases, ``SCRIPT_PLAN_LEGACY_FILENAMES`` /
  ``script_plan_read_candidates``, preserving existing work visibility/browsing. Segmentation status and format migration
  are distinct.
"""

from __future__ import annotations

from pathlib import Path

#: Structured names by content_mode, the only two kinds confirmation accepts.
#: Register new structured two-stage modes here to align gates/status/Web/writes.
SCRIPT_PLAN_FILENAMES: dict[str, str] = {
    "drama": "script_plan_normalized_script.json",
    "narration": "script_plan_segments.json",
}

#: Legacy unstructured aliases by content_mode for status/Web existing-work browsing only,
#: never confirmation/writes. New modes lack legacy data and need no entries.
SCRIPT_PLAN_LEGACY_FILENAMES: dict[str, tuple[str, ...]] = {
    "drama": ("script_plan_normalized_script.md",),
    "narration": ("script_plan_segments.md",),
}

#: Structured reference_video script_plan names. reference_video is generation_mode,
#: spanning narration/drama content modes, outside content-keyed ``SCRIPT_PLAN_FILENAMES``;
#: confirmation separately includes its variant name (``lib.script.script_review.script_plan_kind``).
REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME = "script_plan_reference_units.json"

#: Legacy reference_video free-text aliases for reading/browsing existing work only;
#: writing/generation rejects them, explicitly requesting resplitting when only old ``.md`` exists.
REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME = "script_plan_reference_units.md"

#: Draft names beside formal files, never same names, preserving validated formal content while retaining
#: unresolved results for Agent fixes/promotion. Confirmation/generation both recognize these
#: because drafts block confirmation/prompt_authoring. Centralize with formal names to prevent
#: missed names silently bypassing unresolved-draft blockers.
REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_reference_units.invalid.json"
REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME = "prompt_authoring_reference_script.invalid.json"
DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_normalized_script.invalid.json"
NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_segments.invalid.json"

#: Agent-write-protected formal names (``AgentAccessPolicy._is_protected_formal_script_plan``).
#: Names, never project-variant-resolved paths: protection precedes session assembly, while content_mode/
#: generation_mode change at runtime; state-based branching would leave changed-mode projects unguarded.
#: Protect only variants with alternate draft channels, paired with remedies to avoid deadlocked Agents.
#: All three formal routes have draft paths, so all three names are listed.
AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES: frozenset[str] = frozenset(
    {SCRIPT_PLAN_FILENAMES["drama"], SCRIPT_PLAN_FILENAMES["narration"], REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME}
)


def script_plan_filename(content_mode: str) -> str | None:
    """Structured names by content_mode; None for nonstructured modes such as ad."""
    return SCRIPT_PLAN_FILENAMES.get(content_mode)


def script_plan_read_candidates(content_mode: str) -> tuple[str, ...]:
    """Structured names + legacy ``.md`` aliases for read/browse fallback when primary files are absent.

    Empty tuples for nonstructured modes. Confirmation never uses these, requiring structured ``.json`` only.
    """
    primary = SCRIPT_PLAN_FILENAMES.get(content_mode)
    if primary is None:
        return ()
    return (primary, *SCRIPT_PLAN_LEGACY_FILENAMES.get(content_mode, ()))


def episode_drafts_dir(project_path: Path, episode: int) -> Path:
    """Episode script_plan draft directory ``{project}/drafts/episode_N``."""
    return project_path / "drafts" / f"episode_{episode}"


def episode_source_relpath(episode: int) -> str:
    """Derived project-relative episode sources, ``source/episode_N.txt``.

    Planning derives from ledger ``source_range``; script_plan reads it by default,
    workflow states/artifact fingerprints use the same path. All share this function.
    """
    return f"source/episode_{episode}.txt"


def episode_source_path(project_path: Path, episode: int) -> Path:
    """Absolute derived episode sources, ``{project}/source/episode_N.txt``.

    Producers, planning-derived writes/reconciliation, use this function and share
    ``episode_source_relpath`` path shapes with consumers.
    """
    return project_path / episode_source_relpath(episode)


def episode_script_filename(episode: int) -> str:
    """Episode script ``episode_N.json``, without ``scripts/`` prefix."""
    return f"episode_{episode}.json"


def episode_script_relpath(episode: int) -> str:
    """Default project-relative scripts, ``scripts/episode_N.json``."""
    return f"scripts/{episode_script_filename(episode)}"
