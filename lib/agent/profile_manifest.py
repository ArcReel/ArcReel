"""Profile manifest materialization system.

Materialize agent_runtime_profile/.claude/ and CLAUDE.md into each project’s {project}/.claude/ +
CLAUDE.md. Manifest + sha256 distinguishes unchanged built-in skills, user edits, and user deletions.

Store manifests at ``{project_dir}/.arcreel_profile_manifest.json`` (project root, jointly managing
.claude and top-level CLAUDE.md). Schemas are versioned; ``profile_id`` mismatches equal resets.

The 15-row decision table covers ``{P 存/缺} × {D 存/缺} × {M 无/active/tombstone}``;
``_apply_decision`` exhaustively matches it, explicitly raising NotImplementedError for unlisted states.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import portalocker

from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.content_digest import sha256_file
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project import project_trash

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".arcreel_profile_manifest.json"
LOCK_FILENAME = ".profile_sync.lock"
MANIFEST_SCHEMA_VERSION = 1
EXPECTED_PROFILE_ID = "arcreel/builtin"
LOCK_TIMEOUT_SECONDS = 10

# Two materialized profile roots: ``.claude/**`` tree + top-level ``CLAUDE.md``.
_PROFILE_TREE_ROOT = ".claude"
_PROFILE_TOP_FILE = "CLAUDE.md"


class ProfileMissingError(RuntimeError):
    """Missing profile directory means deployment error; refuse materialization to prevent mass-pruning all projects."""


class ProfileEmptyError(RuntimeError):
    """No materializable files in profile means deployment error; likewise refuse to run."""


class ProfileMisconfiguredError(RuntimeError):
    """Invalid profile variants (missing pairs or coexisting generic files) mean deployment error; refuse materialization."""


ContentMode = Literal["narration", "drama", "ad"]
VALID_CONTENT_MODES: frozenset[str] = frozenset({"narration", "drama", "ad"})


# ---------- Basic utilities ----------


def _is_skippable_dest(rel: str) -> bool:
    return rel in (MANIFEST_FILENAME, LOCK_FILENAME)


def _walk_files(root: Path, rel_to: Path) -> set[str]:
    out: set[str] = set()
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            out.add(p.relative_to(rel_to).as_posix())
    return out


def _parse_variant_suffix(rel: str) -> tuple[str, str | None]:
    """Split ``foo.narration.md`` into (logical_rel="foo.md", mode="narration").
    Nonvariants return (rel, None). Recognize only the final stem segment.
    """
    path = PurePosixPath(rel)
    # path.stem removes the final extension; split once more to get the penultimate suffix.
    stem_parts = path.stem.rsplit(".", 1)
    if len(stem_parts) == 2 and stem_parts[1] in VALID_CONTENT_MODES:
        # Rebuild logical_parent/<logical_stem><ext>; top-level parent is PurePosixPath('.'),
        # ``PurePosixPath('.') / 'foo.md'`` still produces ``PurePosixPath('foo.md')`` without a separate branch.
        logical_name = stem_parts[0] + path.suffix
        return (path.parent / logical_name).as_posix(), stem_parts[1]
    return rel, None


def enumerate_profile_files(profile_dir: Path) -> set[str]:
    """POSIX-relative source-file paths within profiles, including CLAUDE.<mode>.md variants.

    At top level, accept only ``CLAUDE.md`` + ``CLAUDE.<mode>.md``. Deliberately ignore others
    to match ``enumerate_dest_files``; otherwise source discovery without destination enumeration
    would send apparently missing files into tombstone branches. Add new top-level logical files
    to both this function and ``enumerate_dest_files`` together.
    """
    files: set[str] = set()
    if profile_dir.is_dir():
        for p in profile_dir.iterdir():
            if not p.is_file():
                continue
            logical, _ = _parse_variant_suffix(p.name)
            if logical == _PROFILE_TOP_FILE:
                files.add(p.name)
    files |= _walk_files(profile_dir / _PROFILE_TREE_ROOT, profile_dir)
    return files


def resolve_profile_files_for_mode(
    profile_dir: Path,
    content_mode: ContentMode,
) -> dict[str, str]:
    """Project profile file trees into ``{logical_rel: source_rel}`` mappings.

    Generic files: logical_rel == source_rel.
    Variants: retain only the matching ``content_mode``, stripping ``.<mode>`` from logical_rel.

    Raises:
        ValueError: content_mode outside ``VALID_CONTENT_MODES``.
        ProfileMisconfiguredError: Missing variant pairs or generic/variant coexistence.
    """
    if content_mode not in VALID_CONTENT_MODES:
        raise ValueError(f"content_mode must be one of {VALID_CONTENT_MODES}, got {content_mode!r}")

    profile_files = enumerate_profile_files(profile_dir)

    # variants[logical_rel][mode] = source_rel
    variants: dict[str, dict[str, str]] = {}
    commons: dict[str, str] = {}
    for src in profile_files:
        logical, mode = _parse_variant_suffix(src)
        if mode is None:
            commons[logical] = src
        else:
            variants.setdefault(logical, {})[mode] = src

    # Validation 1: generic files and variants are mutually exclusive.
    collisions = set(commons) & set(variants)
    if collisions:
        sample = sorted(collisions)[0]
        raise ProfileMisconfiguredError(
            f"profile has both common and variant for {sample!r}; remove one. all collisions: {sorted(collisions)}"
        )

    # Validation 2: variant pairs are complete.
    for logical, by_mode in variants.items():
        missing = VALID_CONTENT_MODES - set(by_mode)
        if missing:
            raise ProfileMisconfiguredError(
                f"profile variant {logical!r} missing variant for mode(s): {sorted(missing)}; "
                f"all variants of a logical file must exist together"
            )

    mapping: dict[str, str] = dict(commons)
    for logical, by_mode in variants.items():
        mapping[logical] = by_mode[content_mode]
    return mapping


@contextlib.contextmanager
def _project_lock(project_dir: Path):
    """``portalocker.Lock`` internally opens path with ``open()``, following symlinks. Attackers
    precreating ``.profile_sync.lock`` → ``/etc/x`` can truncate external files during locking.
    Open descriptors ourselves with ``os.open(O_CREAT|O_WRONLY|O_NOFOLLOW)``, rejecting symlink
    locks with ELOOP; then use lower-level portalocker ``lock()`` / ``unlink()`` with timeout polling.

    Windows lacks POSIX-only ``O_NOFOLLOW`` on ``os``; fall back to lstat precheck and unflagged
    ``os.open``. Symlink creation requires SeCreateSymbolicLinkPrivilege or developer mode,
    lowering the attack threat. The precheck/open TOCTOU window matches ArcReel’s local-user,
    portalocker-held deployment model.
    """
    lock_path = project_dir / LOCK_FILENAME
    if lock_path.is_symlink():
        raise ValueError(f"lock path is a symlink, refusing: {lock_path}")
    o_nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY | o_nofollow, 0o600)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"lock path is a symlink, refusing: {lock_path}") from e
        raise
    f = os.fdopen(fd, "wb")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)
                break
            except (portalocker.AlreadyLocked, portalocker.LockException):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        try:
            yield
        finally:
            portalocker.unlock(f)
    finally:
        f.close()


def _ensure_dest_within(project_dir: Path, rel: str) -> Path:
    """Resolved ``project_dir / rel`` must remain inside ``project_dir``; otherwise ``raise``.

    Prevent malicious symlinks redirecting materialization I/O outside project roots. Imported
    archives/manual symlinks may point ``.claude`` or its ancestors to ``/etc`` etc., causing
    sync ``_safe_copy`` / ``_safe_unlink_if_file`` to read/write external files.

    rel already passed ``_normalize_profile_rel_path`` in force_resync, or filesystem-realpath
    enumeration in the main entry point, so its shape is trusted; this layer guards destination symlink jumps only.
    """
    # rel segments may not exist yet; realpath stops at the first nonexistent segment, still reflecting the prefix correctly.
    try:
        return safe_join(project_dir, rel, allow_base=True)
    except PathTraversalError as exc:
        raise ValueError(f"dest path escapes project_dir: {rel!r}") from exc


def _normalize_profile_rel_path(rel: object) -> str:
    """UI/external force_resync ``paths`` must reject absolute paths and ``..``, or
    ``profile_dir / rel`` and ``project_dir / rel`` escape profile/project roots to read/write
    arbitrary writable files.

    Require POSIX-relative paths, no ``..``, and neither manifest nor lockfile.
    Return normalized POSIX strings.
    """
    if not isinstance(rel, str) or rel == "":
        raise ValueError(f"Invalid profile sync path: {rel!r}")
    pp = PurePosixPath(rel)
    # PurePosixPath collapses repeated slashes and strips ``.`` segments, so ``parts`` cannot contain empty strings;
    # it retains ``..``, which must be rejected explicitly to block traversal.
    if pp.is_absolute() or ".." in pp.parts:
        raise ValueError(f"Invalid profile sync path: {rel!r}")
    out = pp.as_posix()
    if _is_skippable_dest(out):
        raise ValueError(f"Path not eligible for profile sync: {rel!r}")
    return out


def enumerate_dest_files(project_dir: Path) -> set[str]:
    """Project ``.claude/**`` + ``CLAUDE.md`` files, excluding manifest/lockfile themselves."""
    files: set[str] = set()
    if (project_dir / _PROFILE_TOP_FILE).is_file():
        files.add(_PROFILE_TOP_FILE)
    dest_tree = project_dir / _PROFILE_TREE_ROOT
    if dest_tree.is_symlink():
        raise ValueError(f"profile tree is a symlink, refusing to enumerate: {dest_tree}")
    files |= {rel for rel in _walk_files(dest_tree, project_dir) if not _is_skippable_dest(rel)}
    return files


def _enumerate_dest_symlinks(project_dir: Path) -> set[str]:
    """Return profile symlink entries without traversing directory targets."""
    links: set[str] = set()
    dest_top = project_dir / _PROFILE_TOP_FILE
    if dest_top.is_symlink():
        links.add(_PROFILE_TOP_FILE)

    dest_tree = project_dir / _PROFILE_TREE_ROOT
    if not dest_tree.is_dir() or dest_tree.is_symlink():
        return links
    for dirpath, dirnames, filenames in os.walk(dest_tree, followlinks=False):
        for name in (*dirnames, *filenames):
            path = Path(dirpath) / name
            if path.is_symlink():
                links.add(path.relative_to(project_dir).as_posix())
    return links


# ---------- Manifest data classes ----------


@dataclasses.dataclass
class Manifest:
    schema_version: int
    profile_id: str
    entries: dict[str, dict]
    # None means unmigrated: old manifests predate content_mode; first new sync
    # backfills actual mode through needs_migration without destructive reset.
    # Nonempty means content_mode at last sync; mismatches with this request trigger reset.
    content_mode: ContentMode | None = None

    @classmethod
    def empty(cls) -> Manifest:
        return cls(
            schema_version=MANIFEST_SCHEMA_VERSION,
            profile_id=EXPECTED_PROFILE_ID,
            entries={},
            content_mode=None,
        )

    def to_jsonable(self) -> dict:
        data: dict = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "entries": dict(sorted(self.entries.items())),
        }
        # Omit None fields, preserving compact legacy manifests and reducing diff noise.
        if self.content_mode is not None:
            data["content_mode"] = self.content_mode
        return data

    def normalized_bytes(self) -> bytes:
        """Deterministic serialization: sort_keys + indent + UTF-8, for readable diffs and prewrite comparison."""
        return json.dumps(
            self.to_jsonable(),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")


def load_manifest(project_dir: Path) -> tuple[Manifest, bytes] | None:
    """Read manifests and return ``(manifest, raw_bytes)``.

    Return None, invoking first-migration handling, for:
    - Missing files
    - Corrupt JSON
    - schema_version mismatch (destructive wipe is cleaner than legacy compatibility logic)
    - profile_id mismatch (new profile = new source = equivalent reset)
    """
    path = project_dir / MANIFEST_FILENAME
    # Do not trust symlink manifests: ``read_bytes`` would follow external targets
    # (leaking information and basing sync decisions on wrong manifests). Treat as corruption and reset.
    if path.is_symlink():
        logger.warning("manifest %s is a symlink, refusing to follow; will reset", path)
        return None
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    # Deliberately propagate PermissionError / OSError: these are real I/O failures;
    # silently resetting would escalate temporary problems into destructive .claude/CLAUDE.md overwrites.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("manifest %s corrupt, will reset", path)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        logger.info("manifest %s schema_version mismatch, will reset", path)
        return None
    if data.get("profile_id") != EXPECTED_PROFILE_ID:
        logger.info("manifest %s profile_id mismatch, will reset", path)
        return None
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return None
    # Every entry must be a dict with valid active/tombstone shapes.
    # Treat malformed entries as corrupt manifests and reset, rather than letting _apply_decision
    # crash the whole operation with AttributeError.
    for key, entry in entries.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            logger.warning("manifest %s has malformed entry %r, will reset", path, key)
            return None
        source = entry.get("source")
        if source == "profile":
            if not isinstance(entry.get("sha256"), str):
                logger.warning("manifest %s entry %s missing sha256, will reset", path, key)
                return None
        elif source == "tombstone":
            pass
        else:
            logger.warning("manifest %s entry %s unknown source=%r, will reset", path, key, source)
            return None
    raw_mode = data.get("content_mode")
    content_mode: ContentMode | None
    if raw_mode is None:
        content_mode = None
    elif isinstance(raw_mode, str) and raw_mode in VALID_CONTENT_MODES:
        content_mode = cast(ContentMode, raw_mode)
    else:
        logger.warning("manifest %s invalid content_mode=%r, will reset", path, raw_mode)
        return None
    return (
        Manifest(
            schema_version=data["schema_version"],
            profile_id=data["profile_id"],
            entries=entries,
            content_mode=content_mode,
        ),
        raw,
    )


def save_manifest(
    project_dir: Path,
    manifest: Manifest,
    original_bytes: bytes | None = None,
) -> bool:
    """Atomic write with in-memory prewrite comparison; return whether disk was actually written.

    ``original_bytes is None`` means first migration and writes directly; otherwise skip writes
    when normalized bytes equal the original.
    """
    new_bytes = manifest.normalized_bytes()
    if original_bytes is not None and new_bytes == original_bytes:
        return False
    path = project_dir / MANIFEST_FILENAME
    # Use mkstemp instead of ``path.with_suffix("...tmp")``: predictable names let attackers
    # precreate symlinks to /etc/x, following them during writes and truncating external files. mkstemp uses
    # O_CREAT|O_EXCL|O_RDWR, unpredictable same-directory names, and cross-platform symlink rejection.
    # os.replace uses rename(2); symlink destinations replace only the link itself, never its target.
    parent = path.parent
    fd, tmp_str = tempfile.mkstemp(prefix=MANIFEST_FILENAME + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_bytes)
        os.replace(tmp_str, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_str)  # hard-delete: temp -- Atomic manifest-write temporary file
        raise
    return True


# ---------- Entry/timestamp utilities ----------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _profile_active_entry(sha: str, size: int) -> dict:
    return {"sha256": sha, "size": size, "source": "profile"}


def _tombstone_entry() -> dict:
    return {"source": "tombstone", "deleted_at": _now_iso()}


def _safe_unlink_if_file(path: Path) -> None:
    # ``is_file`` follows symlinks (stat); ``unlink`` does not (lstat). Combined:
    # symlink to file → True, unlink removes only the link, never its target;
    # real file → True + unlink. This is safe; targets are never accidentally deleted.
    if path.is_file():
        path.unlink()  # hard-delete: derived -- Upstream-removed, user-unmodified materialized profile files, rematerializable from profile


def _safe_copy(source: Path, dest: Path) -> None:
    """Copy src → dest, removing symlink destinations first.

    TOCTOU defense: external processes can replace dest with external-target symlinks after
    ``_ensure_dest_within``. Direct ``shutil.copy2`` follows them even when arguments were resolved
    absolute paths, because writing ``open()`` resolves strings again. If symlinked, first
    ``unlink`` the link itself (lstat does not dereference), narrowing file-level races to
    microseconds between unlink/open.

    Residual risk: raced replacements of parent directories (``.claude`` or ancestors) with
    external symlinks still follow links. Server-created ArcReel directories, ordinary users
    without shells, and portalocker locking require an external root process for this attack;
    accept that residual risk. Full protection needs openat + O_NOFOLLOW at every path level,
    exceeding this attack model’s engineering scope.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        dest.unlink()  # hard-delete: infra -- Remove destination symlinks before copying, preventing writes through to targets
    # Reject real directory destinations: ``shutil.copy2(src, dest_dir)`` would copy into
    # unexpected ``dest_dir/src.name`` instead of failing. Decision-table rel values always identify files;
    # directory destinations mean user/archive-precreated same names. Fail explicitly so callers count errors.
    if dest.exists() and dest.is_dir():
        raise ValueError(f"dest is a directory, refusing copy: {dest}")
    shutil.copy2(source, dest)


def _new_stats() -> dict:
    return {
        # Backward-compatible four keys.
        "created": 0,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        # Newly added stat.
        "upgraded": 0,
        "user_modified": 0,
        "user_only": 0,
        "pruned": 0,
        "orphaned": 0,
        "deleted_user": 0,
        "tombstoned": 0,
        "unchanged": 0,
        "collision": 0,
        "migrated_total": 0,
    }


# ---------- First-migration branch ----------


def _trash_discarded_files(profile_dir: Path, project_dir: Path, mapping: dict[str, str]) -> None:
    """Record files reset would discard (project-only or different from built-in profiles) as deletions first.

    Include regular files only; never descend into symlink directories, and links themselves are not content.
    """
    discarded: list[str] = []
    top = project_dir / _PROFILE_TOP_FILE
    candidates = [top] if top.is_file() and not top.is_symlink() else []
    tree = project_dir / _PROFILE_TREE_ROOT
    if tree.is_dir() and not tree.is_symlink():
        for dirpath, _dirnames, filenames in os.walk(tree, followlinks=False):
            candidates.extend(Path(dirpath) / name for name in filenames)
    for path in candidates:
        rel = path.relative_to(project_dir).as_posix()
        if path.is_symlink() or not path.is_file() or _is_skippable_dest(rel):
            continue
        source = profile_dir / mapping[rel] if rel in mapping else None
        if source is None or not source.is_file() or sha256_file(source) != sha256_file(path):
            discarded.append(rel)
    if not discarded:
        return
    with project_metadata_lock(project_dir):
        project_trash.record_deletion(
            project_dir, {"kind": "agent_profile", "path": _PROFILE_TREE_ROOT}, sorted(discarded)
        )


def _full_reset_from_profile(
    profile_dir: Path,
    project_dir: Path,
    mapping: dict[str, str],
    *,
    content_mode: ContentMode | None = None,
) -> dict:
    """Delete destinations, materialize the full profile, and write manifest baselines.

    Used for missing/corrupt manifests, schema_version/profile_id mismatches, or content_mode
    mismatches (destructive mode changes).

    Args:
        mapping: ``{logical_rel: source_rel}`` from resolve_profile_files_for_mode.
        content_mode: Manifest content_mode; omit when None for legacy force_resync paths.
    """
    stats = _new_stats()

    # Clear both destination roots, covering every filesystem type, or subsequent mkdir()/_safe_copy()
    # could be blocked by wrong-type placeholders (regular ``.claude`` file / ``CLAUDE.md`` directory),
    # leaving reset half-done with an incomplete manifest.
    dest_tree = project_dir / _PROFILE_TREE_ROOT
    dest_top = project_dir / _PROFILE_TOP_FILE
    _trash_discarded_files(profile_dir, project_dir, mapping)
    # Only built-in-matching files, about to be rematerialized, plus links/directories remain; customizations already entered the recycle bin.
    if dest_tree.is_symlink() or dest_tree.is_file():
        dest_tree.unlink()  # hard-delete: derived -- Customizations recycled; rematerialize the rest from profile
    elif dest_tree.is_dir():
        shutil.rmtree(dest_tree)  # hard-delete: derived -- Customizations recycled; rematerialize the rest from profile
    if dest_top.is_symlink() or dest_top.is_file():
        dest_top.unlink()  # hard-delete: derived -- Customizations recycled; rematerialize the rest from profile
    elif dest_top.is_dir():
        shutil.rmtree(dest_top)  # hard-delete: derived -- Customizations recycled; rematerialize the rest from profile

    manifest = Manifest.empty()
    for rel in sorted(mapping):
        source_rel = mapping[rel]
        source = profile_dir / source_rel
        try:
            _safe_copy(source, project_dir / rel)
            sha = sha256_file(source)
            size = source.stat().st_size
            manifest.entries[rel] = _profile_active_entry(sha, size)
            stats["migrated_total"] += 1
            stats["created"] += 1
        except OSError as e:
            logger.warning("profile reset skip %s: %s", rel, e)
            stats["errors"] += 1

    manifest.content_mode = content_mode
    save_manifest(project_dir, manifest, original_bytes=None)
    return stats


# ---------- Decision table (15-row exhaustive match) ----------


def _apply_decision(
    profile_dir: Path,
    project_dir: Path,
    rel: str,
    source_rel: str,
    p_exists: bool,
    d_exists: bool,
    m: dict | None,
    manifest: Manifest,
    stats: dict,
) -> None:
    """Apply the decision table to one file; callers catch OSError.

    Args:
        rel: Logical destination-relative path without .<mode>, for manifest.entries/destination paths.
        source_rel: Actual profile-relative source path, possibly with .<mode>, for reading profile files.
    """
    if m is None:
        m_kind = "none"
    elif m.get("source") == "tombstone":
        m_kind = "tombstone"
    else:
        m_kind = "active"

    p = profile_dir / source_rel
    d = project_dir / rel
    # Read p_hash/p_size only in p_exists=True branches; missing-source branches can use empty-string/0
    # placeholders (never written), while types stay non-Optional, avoiding four narrowing assertions.
    p_hash = sha256_file(p) if p_exists else ""
    p_size = p.stat().st_size if p_exists else 0
    d_hash: str | None = sha256_file(d) if d_exists else None
    m_hash: str | None = m.get("sha256") if (m_kind == "active" and m is not None) else None

    match (p_exists, d_exists, m_kind):
        case (True, False, "none"):
            # #1 Initial delivery
            _safe_copy(p, d)
            manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
            stats["created"] += 1
        case (True, False, "active"):
            # #2 User deleted an active built-in skill → tombstone; never restore it.
            manifest.entries[rel] = _tombstone_entry()
            stats["deleted_user"] += 1
            stats["skipped"] += 1
        case (True, True, "active"):
            if d_hash == p_hash and m_hash == p_hash:
                # #3 All three states agree.
                stats["unchanged"] += 1
                stats["skipped"] += 1
            elif d_hash == m_hash and d_hash != p_hash:
                # #4 User unchanged, profile upgraded → overwrite and refresh manifest.
                _safe_copy(p, d)
                manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
                stats["upgraded"] += 1
                stats["repaired"] += 1
            elif d_hash != m_hash and d_hash == p_hash:
                # #5 State-machine reentry: user edits now exactly match current profile.
                manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
                stats["unchanged"] += 1
                stats["skipped"] += 1
            else:
                # #6 User edited (d_hash != m_hash and != p_hash).
                stats["user_modified"] += 1
                stats["skipped"] += 1
        case (False, True, "active"):
            if d_hash == m_hash:
                # #7 Upstream deleted, user unchanged → delete D during materialization and tombstone.
                _safe_unlink_if_file(d)
                manifest.entries[rel] = _tombstone_entry()
                stats["pruned"] += 1
                stats["repaired"] += 1
            else:
                # #8 Upstream deleted, user edited → retain D and old baseline; retain entry
                # so settings still identifies a legacy customization forked from the built-in profile, rather than
                # a sourceless project-only file. Future materializations retain three-way protection.
                stats["orphaned"] += 1
                stats["skipped"] += 1
        case (False, True, "none"):
            # #9 Project-only skill
            stats["user_only"] += 1
            stats["skipped"] += 1
        case (True, True, "tombstone"):
            # #10 User deleted then manually restored → clear tombstone; next round treats as user_only.
            manifest.entries.pop(rel, None)
            stats["user_only"] += 1
            stats["skipped"] += 1
        case (True, False, "tombstone"):
            # #11 Stable: user deleted, profile still present.
            stats["tombstoned"] += 1
            stats["skipped"] += 1
        case (False, True, "tombstone"):
            # #12 With P absent, tombstone is inapplicable and D is orphaned → clear entry.
            manifest.entries.pop(rel, None)
            stats["user_only"] += 1
            stats["skipped"] += 1
        case (False, False, "tombstone"):
            # #13 Both absent, tombstone persists → no-op.
            stats["tombstoned"] += 1
            stats["skipped"] += 1
        case (False, False, "active"):
            # #14 Both deleted in one round → tombstone (implicitly assumes missing D means user deletion;
            # volume changes/failures leaving D temporarily empty need force_resync_profile to clear tombstones).
            manifest.entries[rel] = _tombstone_entry()
            stats["pruned"] += 1
            stats["repaired"] += 1
        case (True, True, "none"):
            # #15 Name collision: new profile file matches an existing project filename.
            if d_hash == p_hash:
                # Identical content → treat as delivered, write active entry.
                manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
            # Different content → retain D, write no entry (next round enters #9 user_only).
            stats["collision"] += 1
            stats["skipped"] += 1
        case _:
            raise NotImplementedError(f"unreachable case: {p_exists=} {d_exists=} {m_kind=}")


# ---------- Public API ----------


def sync_profile_to_project(
    profile_dir: Path,
    project_dir: Path,
    content_mode: ContentMode,
) -> dict:
    """Main profile → project_dir materialization entry point.

    Raises:
        ProfileMissingError: Profile directory absent.
        ProfileEmptyError: No materializable profile files.
        ProfileMisconfiguredError: Invalid variant configuration.
        ValueError: content_mode is neither narration nor drama.
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    mapping = resolve_profile_files_for_mode(profile_dir, content_mode)
    if not mapping:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)

    with _project_lock(project_dir):
        loaded = load_manifest(project_dir)
        if loaded is None:
            return _full_reset_from_profile(profile_dir, project_dir, mapping, content_mode=content_mode)
        manifest, original_bytes = loaded

        # mode_status: missing(None)=needs_migration; present mismatch=mismatch → reset.
        if manifest.content_mode is not None and manifest.content_mode != content_mode:
            logger.info(
                "manifest %s content_mode %r != requested %r, resetting",
                project_dir / MANIFEST_FILENAME,
                manifest.content_mode,
                content_mode,
            )
            return _full_reset_from_profile(profile_dir, project_dir, mapping, content_mode=content_mode)

        stats = _new_stats()
        dest_files = enumerate_dest_files(project_dir)
        all_keys = mapping.keys() | dest_files | manifest.entries.keys()

        for rel in sorted(all_keys):
            p_exists = rel in mapping
            d_exists = rel in dest_files
            m = manifest.entries.get(rel)
            try:
                _ensure_dest_within(project_dir, rel)
                _apply_decision(
                    profile_dir,
                    project_dir,
                    rel,
                    mapping.get(rel, rel),  # source_rel; absent mapping (P absent) uses rel.
                    p_exists,
                    d_exists,
                    m,
                    manifest,
                    stats,
                )
            except ValueError as e:
                logger.warning("profile sync skip %s (escape guard): %s", rel, e)
                stats["errors"] += 1
            except OSError as e:
                logger.warning("profile sync skip %s: %s", rel, e)
                stats["errors"] += 1

        # Write mode after main sync completes, whether needs_migration or match.
        manifest.content_mode = content_mode
        save_manifest(project_dir, manifest, original_bytes)
        return stats


def get_profile_status(
    profile_dir: Path,
    project_dir: Path,
    content_mode: ContentMode,
) -> dict[str, object]:
    """Return project-local files that a full built-in reset would discard.

    A customization is a tracked file whose content differs from its recorded
    built-in baseline, a user deletion of a currently shipped file, or a
    project-only profile file. Unmodified files removed upstream are not
    customizations and are pruned by normal sync.
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    mapping = resolve_profile_files_for_mode(profile_dir, content_mode)
    if not mapping:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    loaded = load_manifest(project_dir)
    dest_files = enumerate_dest_files(project_dir)
    dest_symlinks = _enumerate_dest_symlinks(project_dir)
    if loaded is None:
        # Without a trustworthy baseline, existing destination files may be
        # customized and missing projected files may be user deletions. A full
        # reset repairs both shapes, so keep the reset action available for the
        # complete affected set.
        customized_files = sorted(dest_files | dest_symlinks | mapping.keys())
        return {"customized": bool(customized_files), "customized_files": customized_files}

    manifest, _ = loaded
    customized = set(dest_symlinks)
    for rel in dest_files:
        entry = manifest.entries.get(rel)
        if entry is None:
            customized.add(rel)
            continue
        if entry.get("source") == "tombstone":
            customized.add(rel)
            continue
        try:
            dest = _ensure_dest_within(project_dir, rel)
            if sha256_file(dest) != entry.get("sha256"):
                customized.add(rel)
        except (OSError, ValueError):
            customized.add(rel)

    for rel in mapping:
        if rel in dest_files:
            continue
        # Any missing file still shipped by the current projection is a user
        # deletion, whether or not the manifest tracked the path yet.
        customized.add(rel)

    files = sorted(customized)
    return {"customized": bool(files), "customized_files": files}


def force_resync_profile(
    profile_dir: Path,
    project_dir: Path,
    content_mode: ContentMode,
    *,
    paths: Iterable[str] | None = None,
) -> dict:
    """Force P over D, update manifests, and clear tombstones.

    Used by the UI Restore Built-in Skill button. ``paths=None`` means all paths.
    ``paths`` are **logical paths** (e.g. ``CLAUDE.md``); resolve sources internally by content_mode.
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    mapping = resolve_profile_files_for_mode(profile_dir, content_mode)
    if not mapping:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    target = {_normalize_profile_rel_path(rel) for rel in paths} if paths is not None else set(mapping)

    project_dir.mkdir(parents=True, exist_ok=True)

    with _project_lock(project_dir):
        if paths is None:
            return _full_reset_from_profile(profile_dir, project_dir, mapping, content_mode=content_mode)
        loaded = load_manifest(project_dir)
        if loaded is None:
            manifest, original_bytes = Manifest.empty(), None
        else:
            manifest, original_bytes = loaded

        stats = _new_stats()
        for rel in sorted(target):
            source_rel = mapping.get(rel)
            p = profile_dir / source_rel if source_rel else None
            if p is None or not p.is_file():
                logger.warning("force_resync skip missing profile file: %s", rel)
                continue
            try:
                _ensure_dest_within(project_dir, rel)
                d = project_dir / rel
                _safe_copy(p, d)
                sha = sha256_file(p)
                manifest.entries[rel] = _profile_active_entry(sha, p.stat().st_size)
                stats["created"] += 1
                stats["repaired"] += 1
            except ValueError as e:
                logger.warning("force_resync skip %s (escape guard): %s", rel, e)
                stats["errors"] += 1
            except OSError as e:
                logger.warning("force_resync skip %s: %s", rel, e)
                stats["errors"] += 1

        manifest.content_mode = content_mode
        save_manifest(project_dir, manifest, original_bytes)
        return stats
