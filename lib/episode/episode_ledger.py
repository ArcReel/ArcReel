"""Episode ledgers: data models and reading helpers for episodes[] ledger fields.

The project.json episodes list is the single source of truth for episodes. Entries extend episode/title/script_file with ledger fields
(source_range / hook / outline / ledger_status) and record episode source origins (see
``lib.episode.episode_sources``). Physical ``source/episode_N.txt`` files for episodes cut from whole sources are derived; standalone
source episode files are their own sources.

``source_range`` is the sole positional truth for cut episodes: required to reconstruct derived files from sources or continue planning. Legacy split-flow
episodes lack it and remain consumable, but planning refuses them and directs callers to full reset (see ``lib.episode.episode_planner`` and
``lib.episode.episode_reset``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.episode.episode_paths import episode_drafts_dir, episode_script_relpath
from lib.infra.path_safety import safe_exists
from lib.infra.text_utils import normalize_newlines
from lib.project.project_schema import parse_project_schema_version

_STRICT_CONFIG = ConfigDict(extra="forbid")

LedgerStatus = Literal["planned", "consumed", "stale"]

#: Current states for validation to identify out-of-set values (tolerate them, recording diagnostic logs only).
LEDGER_STATUSES: tuple[str, ...] = get_args(LedgerStatus)

# ASCII digits only: \d permits fullwidth/other Unicode digits, misclassifying nonpipeline files as derived episodes.
_EPISODE_FILE_RE = re.compile(r"episode_([0-9]+)\.txt")
_SCRIPT_FILE_RE = re.compile(r"episode_([0-9]+)\.json")
_DRAFT_DIR_RE = re.compile(r"episode_([0-9]+)")

# Sole definition of source-text extensions, shared by whole-source manifests and other source readers.
SOURCE_TEXT_SUFFIXES = {".txt", ".md"}

# Top-level project.json source fingerprints (relative source path → normalized-text sha256). Ledger coordinates bind
# specific source content; fingerprints prove unchanged originals. Full resets clear ledgers, invalidating/clearing fingerprints too.
SOURCE_FINGERPRINTS_KEY = "source_fingerprints"


# Field-validation failures carry translation keys: Pydantic only transports reasons as strings; DataValidator
# reconstructs translatable fragments when aggregating errors so users see reasons in requested languages (see
# ``lib.project.data_validator._pydantic_error_summary``）。
LEDGER_SOURCE_FILE_NOT_RELATIVE_KEY = "val_ledger_source_file_not_relative"
LEDGER_SOURCE_FILE_ESCAPES_KEY = "val_ledger_source_file_escapes"
LEDGER_START_AFTER_END_KEY = "val_ledger_start_after_end"


def _validate_rel_posix_path(value: str) -> str:
    """``source_file`` path semantics: project-relative POSIX paths; reject absolute paths / ``..`` / backslashes.

    Permitting these shapes lets path-based source readers escape project directories.
    """
    if not value or "\\" in value:
        raise ValueError(LEDGER_SOURCE_FILE_NOT_RELATIVE_KEY)
    parts = PurePosixPath(value).parts
    if PurePosixPath(value).is_absolute() or ".." in parts:
        raise ValueError(LEDGER_SOURCE_FILE_ESCAPES_KEY)
    return value


class SourceRange(BaseModel):
    """Episode source-material ranges spanning files in whole-source order (ADR 0097).

    Start at ``start`` in ``source_file``, ending exclusively at ``end`` in ``end_file``; ``end_file`` defaults to
    ``source_file``. Offsets are per-file indices in ``normalize_source_text`` coordinates, exact narration splits
    or soft drama material ranges. Paths are project-relative POSIX, such as ``source/novel.txt``.
    Whole-source manifests determine file order, not validated here.
    """

    model_config = _STRICT_CONFIG

    source_file: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    end_file: str | None = None

    @field_validator("source_file", "end_file")
    @classmethod
    def _check_source_file(cls, value: str | None) -> str | None:
        return None if value is None else _validate_rel_posix_path(value)

    @model_validator(mode="after")
    def _check_order(self) -> SourceRange:
        if (self.end_file is None or self.end_file == self.source_file) and self.start > self.end:
            raise ValueError(LEDGER_START_AFTER_END_KEY)
        return self


class EpisodeOutline(BaseModel):
    """Drama episode outlines: story beats + next-episode teasers, produced by planning tools."""

    model_config = _STRICT_CONFIG

    story_beats: list[str] = Field(default_factory=list)
    next_episode_teaser: str | None = None


#: From this schema onward, next outlines use immediately following broadcast-order episodes, falling back to titles without planning data.
#: Earlier projects appear only in migration chains: activation before v15→v16 uses episode number + 1, omitting unplanned outlines;
#: v15→v16 plans once with each rule, rewriting script-plan registrations changed solely by next-episode outlines.
NEXT_EPISODE_OUTLINE_BY_ORDER_SCHEMA_VERSION = 16


def episode_outline_context(
    project: Mapping[str, Any], episode: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract ``(本集大纲, 下集大纲)`` from episode ledgers as script content-generation (script_plan) planning inputs.

    Outline dicts contain ``title`` / ``hook`` / ``story_beats`` / ``next_episode_teaser``. Without any current-entry
    planning data (legacy entries, planning tools have not written), first item is None. Next means the immediately following broadcast-order episode:
    provide its outline if planned, otherwise title; neither present or current episode last means second item None.
    After content extraction moved earlier, script_plan (normalize) consumes outlines: script content (storyboard boundaries / narration) must cover story beats
    and land the ending hook in the final scene; prompt_authoring is visual-only and no longer needs outlines.
    """

    def _entry(ep_num: int) -> Mapping[str, Any]:
        return next(
            (e for e in (project.get("episodes") or []) if isinstance(e, Mapping) and e.get("episode") == ep_num),
            {},
        )

    def _context(entry: Mapping[str, Any]) -> dict[str, Any] | None:
        raw_outline = entry.get("outline")
        outline = raw_outline if isinstance(raw_outline, Mapping) else {}
        raw_beats = outline.get("story_beats")
        # Treat nonlist shapes (manual corruption) as missing; filter nonstring list members too, preventing characterwise string rendering
        # or raw numbers / None reaching script_plan prompts (matching the helper's fail-soft rules).
        story_beats = [beat for beat in raw_beats if isinstance(beat, str)] if isinstance(raw_beats, list) else []
        ctx: dict[str, Any] = {
            "title": entry.get("title"),
            "hook": entry.get("hook"),
            "story_beats": story_beats,
            "next_episode_teaser": outline.get("next_episode_teaser"),
        }
        if not ctx["hook"] and not ctx["story_beats"] and not ctx["next_episode_teaser"]:
            return None
        return ctx

    if parse_project_schema_version(project) < NEXT_EPISODE_OUTLINE_BY_ORDER_SCHEMA_VERSION:
        return _context(_entry(episode)), _context(_entry(episode + 1))

    following: Mapping[str, Any] = {}
    entries = [e for e in (project.get("episodes") or []) if isinstance(e, Mapping)]
    for index, entry in enumerate(entries[:-1]):
        if entry.get("episode") == episode:
            following = entries[index + 1]
            break
    next_context = _context(following)
    title = following.get("title")
    if next_context is None and isinstance(title, str) and title.strip():
        next_context = {"title": title, "hook": None, "story_beats": [], "next_episode_teaser": None}
    return _context(_entry(episode)), next_context


def normalize_source_text(text: str) -> str:
    """Sole normalization for ledger coordinates: Unicode NFC + newlines unified to ``\\n``.

    All source_range offsets address this function's output coordinates;
    every consumer slicing sources by offset must first apply this function.
    """
    return unicodedata.normalize("NFC", normalize_newlines(text))


@dataclass
class SourceDoc:
    """One whole-source file: project-relative POSIX path + normalized full text."""

    rel_path: str
    text: str


def parse_episode_num(value: Any) -> int | None:
    """Permissively parse entry episode numbers: int (excluding bool, since True collides with episode 1 keys) or digit-only
    strings (historical manual data); otherwise None (preserve entries unchanged, excluding number-based disposition).

    ``str.isdigit()`` recognizes more characters than ``int()`` converts (for example superscript ``²`` and
    circled ``①``). Corrupt ledgers containing these pass ``isdigit()`` but ``int()`` raises
    ``ValueError``. Return None for this mismatch too, preventing caller crashes (including the zero-prevalidation reset
    escape hatch).
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def parse_positive_episode_num(value: Any) -> int | None:
    """Require positive integers in addition to :func:`parse_episode_num`.

    Although ``parse_episode_num`` parses 0 and negatives, ledger consumers (for example zero-prevalidation
    ``lib.episode.episode_reset``) uniformly regard nonpositive numbers as corrupt ledger data, excluding normal
    disposition. Shape-valid ledger-entry classification must agree, or malformed entries with 0 /
    negative numbers would incorrectly pass as valid.
    """
    num = parse_episode_num(value)
    return num if num is not None and num > 0 else None


def well_formed_ledger_entries(project: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Read entries before whole-ledger broadcast-order rewrites: return a copy if ``episodes`` is an object list with unique positive integer IDs, otherwise None.

    Writers index entries by IDs then reorder; unparseable or duplicate IDs cause entries to disappear or repeat during reordering.
    """
    raw = project.get("episodes")
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        return None
    nums = [parse_positive_episode_num(entry.get("episode")) for entry in raw]
    if None in nums or len(set(nums)) != len(nums):
        return None
    return list(raw)


def is_derived_episode_name(name: str) -> bool:
    """Whether filenames are derived episode files ``episode_N.txt`` (ASCII digits only)."""
    return _EPISODE_FILE_RE.fullmatch(name) is not None


def compute_source_fingerprints(sources: list[SourceDoc]) -> dict[str, str]:
    """Record sha256 fingerprints of normalized texts per source file (relative source path → hexdigest).

    ``sources`` comes from ``lib.episode.episode_sources.discover_sources``; its ``text`` already passes ``normalize_source_text``,
    so newline-style differences (CRLF/LF) do not affect fingerprints.
    """
    return {doc.rel_path: hashlib.sha256(doc.text.encode("utf-8")).hexdigest() for doc in sources}


def mismatched_source_fingerprints(recorded: Any, sources: list[SourceDoc]) -> list[str]:
    """Compare recorded fingerprints with current sources; return differing relative source paths, sorted by path.

    Compare recorded files only: non-``Mapping`` ``recorded`` or absent file entries mean legacy projects /
    new source files without fingerprints, excluded from comparison (never block initial planning). Recorded files with different current fingerprints or
    missing from whole sources (deleted/removed from manifests) mismatch: original text underlying ledger coordinates is no longer
    trustworthy, and full reset is the only escape. Malformed recorded values (nonstr) count as unrecorded; dirty data must not crash
    comparison logic.
    """
    if not isinstance(recorded, Mapping):
        return []
    current = compute_source_fingerprints(sources)
    mismatched = {
        rel
        for rel, fingerprint in recorded.items()
        if isinstance(rel, str) and isinstance(fingerprint, str) and current.get(rel) != fingerprint
    }
    return sorted(mismatched)


def discover_episode_file_aliases(project_dir: Path) -> dict[int, list[Path]]:
    """Enumerate all derived episode-file aliases source/episode_N.txt → {episode number: [paths, ...]} (filename-sorted).

    One episode number can have multiple padded filename aliases (``episode_1.txt`` / ``episode_01.txt``).
    Most callers need one representative path (see ``discover_episode_files``); callers disposing of every derived
    file for a number (for example reset cleanup) use this function for all aliases.

    Include dangling symlinks too: ``Path.is_file()`` returns False when targets are missing,
    otherwise making them invisible to discovery. Disposition callers (for example reset) then miss cleanup,
    and residual dangling links cause ``EpisodePlanner`` symlink validation to block subsequent derived-file
    writes. ``unlink()``/``rename()`` act only on link entries without following final-component targets;
    including dangling links introduces no write-through risk.
    """
    source_dir = project_dir / "source"
    if not source_dir.is_dir():
        return {}
    result: dict[int, list[Path]] = {}
    for path in sorted(source_dir.iterdir()):
        match = _EPISODE_FILE_RE.fullmatch(path.name)
        if match and (path.is_file() or path.is_symlink()):
            result.setdefault(int(match.group(1)), []).append(path)
    return result


def discover_episode_files(project_dir: Path) -> dict[int, Path]:
    """Enumerate derived episode files source/episode_N.txt → {episode number: path} (one readable representative per number).

    Representatives come only from readable regular-file aliases. If every alias is a dangling symlink without real
    contents, omit that episode entirely rather than returning unreadable paths.
    ``discover_episode_file_aliases`` sorts filenames without distinguishing dangling links: if a dangling alias
    (for example ``episode_01.txt``) sorts before a valid file (``episode_1.txt``), simply choosing the first
    makes content-based readers see empty content and creates ghost entries for entirely dangling episode numbers
    without any real contents.
    """
    result: dict[int, Path] = {}
    for num, paths in discover_episode_file_aliases(project_dir).items():
        readable = next((p for p in paths if p.is_file()), None)
        if readable is not None:
            result[num] = readable
    return result


def discover_product_episode_nums(project_dir: Path) -> set[int]:
    """Enumerate episode numbers with downstream artifacts on disk (script JSON / script_plan draft directories), independent of ledger entries.

    If entries disappear (corruption/manual deletion) but ``scripts/episode_N.json`` or
    ``drafts/episode_N/`` remain, deriving candidate numbers solely from ledger entries and ``source/episode_N.txt``
    misses orphan artifacts and skips consumption classification.
    """
    nums: set[int] = set()
    scripts_dir = project_dir / "scripts"
    if scripts_dir.is_dir():
        for path in scripts_dir.iterdir():
            match = _SCRIPT_FILE_RE.fullmatch(path.name)
            if match and path.is_file():
                nums.add(int(match.group(1)))
    drafts_dir = project_dir / "drafts"
    if drafts_dir.is_dir():
        for path in drafts_dir.iterdir():
            match = _DRAFT_DIR_RE.fullmatch(path.name)
            # Directory presence does not prove artifacts: files.py::update_draft_content creates directories before
            # draft-content validation, so rejected invalid saves leave empty directories. Only persisted script_plan_* counts as
            # downstream artifacts, matching has_downstream_products().
            if match and path.is_dir() and any(path.glob("script_plan_*")):
                nums.add(int(match.group(1)))
    return nums


def has_downstream_products(project_dir: Path, episode_num: int, entry: Mapping[str, Any]) -> bool:
    """Whether episodes have downstream artifacts (script JSON / script_plan intermediates; media requires scripts, so script presence covers it).

    Disk evidence takes precedence over ledger state: consumption remains detectable despite corrupted/missing statuses.
    """
    script_file = entry.get("script_file")
    if isinstance(script_file, str) and safe_exists(project_dir, script_file):
        return True
    if (project_dir / episode_script_relpath(episode_num)).is_file():
        return True
    drafts_dir = episode_drafts_dir(project_dir, episode_num)
    # script_plan_* matches every format (structured .json / legacy .md / reference_units.md), covering script_plan artifacts
    # across all content_mode values: any segmentation means downstream artifacts exist, preventing replanning overwrite.
    return drafts_dir.is_dir() and any(drafts_dir.glob("script_plan_*"))


def episode_has_products(
    project_dir: Path, episode_num: int, entry: Mapping[str, Any], *, product_nums: Collection[int]
) -> bool:
    """Episodes have artifacts if ledger consumed or script / script_plan exists on disk (including zero-padded script filenames).

    ``product_nums`` comes from :func:`discover_product_episode_nums`, computed once by callers. Manual splitting, replanning and reset share this rule
    to decide whether replaced old cut episodes become sourceless or are removed directly.
    """
    return (
        entry.get("ledger_status") == "consumed"
        or episode_num in product_nums
        or has_downstream_products(project_dir, episode_num, entry)
    )


def episodes_with_products(project_dir: Path, entries: Iterable[Mapping[str, Any]]) -> set[int]:
    """Ledger episode IDs with artifacts, classified individually through :func:`episode_has_products`."""
    product_nums = discover_product_episode_nums(project_dir)
    found: set[int] = set()
    for entry in entries:
        episode = parse_positive_episode_num(entry.get("episode"))
        if episode is not None and episode_has_products(project_dir, episode, entry, product_nums=product_nums):
            found.add(episode)
    return found


@dataclass(frozen=True)
class SourceSpan:
    """Ledger source-range coordinates: start ``(source_file, start)``, exclusive end ``(end_file, end)``.

    Single-file ranges have ``end_file == source_file``.
    """

    source_file: str
    start: int
    end_file: str
    end: int

    @property
    def crosses_files(self) -> bool:
        return self.end_file != self.source_file


def source_range_value(source_file: str, start: int, end_file: str, end: int) -> dict[str, Any]:
    """Persisted ledger source-range shape: omit ``end_file`` when endpoints are in the same file."""
    value: dict[str, Any] = {"source_file": source_file, "start": start, "end": end}
    if end_file != source_file:
        value["end_file"] = end_file
    return value


def parse_source_range(entry: Mapping[str, Any]) -> SourceSpan | None:
    """Parse entry ``source_range`` coordinates, returning None for incomplete structure.

    Sole predicate for episode position records, shared by planning and reset: check field types only
    (``source_file`` / ``end_file`` str, ``start`` / ``end`` nonbool int), without validating
    bounds. Callers decide whether coordinates must fall within sources according to their own rules. Empty dictionaries ``{}`` or mappings
    missing fields satisfy ``isinstance(..., Mapping)`` but lack usable coordinates, so always treat as no coordinates.
    """
    source_range = entry.get("source_range")
    if not isinstance(source_range, Mapping):
        return None
    rel = source_range.get("source_file")
    start = source_range.get("start")
    end = source_range.get("end")
    end_file = source_range.get("end_file", rel)
    if (
        isinstance(rel, str)
        and isinstance(end_file, str)
        and isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
    ):
        return SourceSpan(source_file=rel, start=start, end_file=end_file, end=end)
    return None
