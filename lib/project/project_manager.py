r"""Project file manager.

Manage video-project directory structures, storyboard-script reads/writes, and status tracking."""

import asyncio
import copy
import errno
import functools
import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import time
import unicodedata
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Generator, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, ClassVar, Literal, TypeGuard, cast

import portalocker
from pydantic import BaseModel, Field

from lib.agent.agent_profile import agent_profile_dir
from lib.agent.profile_manifest import (
    VALID_CONTENT_MODES,
    ContentMode,
    ProfileEmptyError,
    ProfileMisconfiguredError,
    ProfileMissingError,
    get_profile_status,
    sync_profile_to_project,
)
from lib.agent.profile_manifest import (
    force_resync_profile as _force_resync_profile,
)
from lib.artifacts.artifact_manifest import ArtifactBasisDescriptor
from lib.artifacts.formal_write import formal_write_transaction, project_metadata_lock
from lib.episode.episode_ids import raise_episode_id_high_water
from lib.episode.episode_ledger import SOURCE_TEXT_SUFFIXES
from lib.episode.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    episode_script_filename,
    episode_script_relpath,
)
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    WHOLE_SOURCE_FILES_KEY,
    SourceOrigin,
    record_source_remaining,
    source_planning_inputs,
)
from lib.episode.episode_target_duration import (
    EPISODE_TARGET_DURATION_FIELD,
    MAX_EPISODE_TARGET_DURATION,
    MIN_EPISODE_TARGET_DURATION,
    is_valid_episode_target_duration,
)
from lib.episode.source_kinds import project_overview_source_kind
from lib.infra.app_data_dir import app_data_dir
from lib.infra.async_thread import run_sync_transaction
from lib.infra.content_digest import canonical_json_digest
from lib.infra.data_root_layout import PROJECT_FILENAME, PROJECT_NAME_PATTERN, DataRootLayout, list_project_dirs
from lib.infra.json_io import atomic_write_bytes, atomic_write_json, load_json, load_json_or_none
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.schema_guards import is_int, is_shape, is_str
from lib.infra.validation_messages import ValidationResult, default_translate
from lib.project.asset_rename import (
    AssetDeletionPreview,
    AssetEpisodeReferences,
    AssetRenameConflictError,
    AssetRenameNotFoundError,
    AssetRenameReport,
    owns_named_file,
    plan_asset_file_renames,
    rewrite_entry_paths,
    rewrite_grid_reference_images,
    rewrite_payload_references,
)
from lib.project.asset_types import (
    ALIASES_FIELD,
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    ProjectAssetNameConflictError,
    asset_name_comparison_key,
    build_asset_entry,
    ensure_project_asset_name_available,
    ensure_project_asset_namespace,
    find_project_asset_name,
    normalize_asset_bucket,
    normalize_asset_name,
    record_asset_aliases,
    rekey_equivalent_entries,
    resolve_asset_key,
    validate_asset_name,
)
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_schema import parse_project_schema_version
from lib.project.script_entry_cleanup import trash_entries
from lib.project.task_project_claim import ensure_task_project_claim
from lib.references.reference_catalog import derivative_reference
from lib.script.draft_quarantine import QUARANTINE_FILENAMES
from lib.script.item_ids import (
    ItemIdIssuer,
    episode_item_id_high_water,
    max_item_number,
    raise_item_id_high_water,
)
from lib.script.reference_video.duration_migration import migrate_script_unit_durations
from lib.script.script_editor import ScriptEditError, resolve_items
from lib.script.script_models import get_generated_assets
from lib.speech.audio_utils import resolve_audio_ref_path, resolve_stale_reference_audio
from lib.speech.narration_config import (
    NARRATION_CONFIG_FIELDS,
    NARRATION_DELIVERY_FIELD,
    POST_PRODUCTION,
    validate_project_narration_config,
)

if TYPE_CHECKING:
    from lib.project.asset_merge import AssetMergeReport
    from lib.script.grid.models import GridGeneration

logger = logging.getLogger(__name__)

PROJECT_SLUG_SANITIZER = re.compile(r"[^a-zA-Z0-9]+")

# Generation mode: two required values, fixed at creation and immutable thereafter (enforced by PATCH model structure).
# Grids are not generation modes: independent grid_storyboard boolean, meaningful only for `storyboard` generation.
# Legacy third value "grid" was recoded by v4→v5 as storyboard + grid_storyboard=true.
VALID_GENERATION_MODES: frozenset[str] = frozenset({"storyboard", "reference_video"})
_DEFAULT_GENERATION_MODE = "storyboard"


class _Unset:
    r"""Sentinel distinguishing omitted before (the unified write entry reads the prior file) from explicit None (no prior state)."""


_UNSET = _Unset()


class ScriptWriteConflict(Exception):
    """A formal script changed after an optimistic-concurrency baseline was captured."""

    def __init__(self, *, expected: str | None, actual: str | None, current_content: dict[str, Any] | None):
        super().__init__(f"script changed: expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual
        self.current_content = current_content


def _file_content_fingerprint(path: Path) -> tuple[str | None, dict[str, Any] | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return hashlib.sha256(raw).hexdigest(), None
    return canonical_json_digest(parsed), parsed if isinstance(parsed, dict) else None


def grid_storyboard_enabled(project: dict[str, Any]) -> bool:
    r"""Whether the project produces storyboard images as grids.

    Grids are a storyboard-image production method within storyboard-image-to-video. Reference-to-video
    has no storyboard-image step, so even residual grid_storyboard=true does not activate grids."""
    return project.get("generation_mode") == "storyboard" and bool(project.get("grid_storyboard"))


def find_episode(project: Mapping[str, Any], episode: int | None) -> dict[str, Any] | None:
    r"""Return the entry with ``episode == N`` in project.json ``episodes[]``, or None if absent.

    An ``episode`` of None (unknown episode ID) matches no entry."""
    if episode is None:
        return None
    for ep in project.get("episodes") or []:
        if isinstance(ep, dict) and ep.get("episode") == episode:
            return ep
    return None


def resolve_episode_script_binding(
    project: Mapping[str, Any],
    episode: int,
    expected_script_file: str,
    *,
    require_indexed: bool = False,
) -> str | None:
    """Return the live binding when it still denotes the expected episode script.

    Legacy projects without an ``episodes`` index use the submitted script as
    their binding unless ``require_indexed`` is true. Indexed projects must
    retain a matching normalized binding; a missing episode or a concurrent
    rebind returns ``None``.
    """

    entry = find_episode(project, episode)
    current_binding = entry.get("script_file") if isinstance(entry, dict) else None
    if current_binding is None and not require_indexed and not (project.get("episodes") or []):
        return expected_script_file
    if isinstance(current_binding, str) and (
        ProjectManager.normalize_script_filename(current_binding)
        == ProjectManager.normalize_script_filename(expected_script_file)
    ):
        return current_binding
    return None


def is_episode_number(value: object) -> TypeGuard[int]:
    r"""Whether this value is an episode ID.

    Episode IDs are positive integers. project.json and script JSON may be read raw without structural
    validation: ``True`` is both an ``int`` and equal to ``1``, while ``0`` and negatives correspond to
    no episode. ``isinstance`` checks or magnitude comparisons alone let these values masquerade as ledger
    IDs. This sole definition is shared by ledger reads, write validation, and episode-metadata sync."""

    return type(value) is int and value >= 1


def is_reference_video_project(project: Mapping[str, Any]) -> bool:
    r"""Whether the project uses reference-to-video.

    ``generation_mode`` in project.json is the sole source of truth: fixed at creation and immutable,
    it governs the whole project. Ad/short-film script skeletons do not carry script-level
    ``generation_mode`` stamps either (see ``script_generator``), so scripts alone cannot identify reference-to-video."""
    return project.get("generation_mode") == "reference_video"


def _resolve_items_or_warn(script: dict, *, script_filename: str | None = None) -> list[Any]:
    r"""Read-path fallback for corrupt data: distinguish three script structures using `resolve_items`, logging a warning and returning [] when an existing key is not a list.

    Sharing `resolve_items` with write paths (`update_scene_asset` / `batch_update_scene_assets`) keeps
    read/write discrimination consistent across all three structures, avoiding a silent drama fallback
    returning [] for reference-to-video. Writes should fail loudly (propagate ScriptEditError, explicitly
    fail the worker, and return API 5xx for corruption). Reads return [] for corrupt data without blocking
    UI rendering, but warnings provide operations an observable signal for manual repair so fallback
    remains visible."""
    try:
        items, _id_field, _kind = resolve_items(script)
        return items
    except ScriptEditError as e:
        logger.warning(
            "剧本 %s 数据损坏（%s），读取降级为空列表——请人工修复",
            script_filename or "<unknown>",
            e,
        )
        return []


class EpisodeScriptReboundError(RuntimeError):
    r"""The episode→script_file binding changed across lock acquisition (concurrent PATCH rebinding); callers should retry."""


class EmptySourceError(ValueError):
    r"""Empty source directory prevents overview generation; distinguish configuration errors such as no available text provider so routes do not misclassify user actions."""


# ==================== Data models ====================


class ProjectOverview(BaseModel):
    """Project-overview data model for Gemini Structured Outputs"""

    synopsis: str = Field(description="Story synopsis, 200-300 characters, summarizing the main plot")
    genre: str = Field(
        description="Genre, for example historical palace intrigue (古装宫斗), modern suspense (现代悬疑), fantasy cultivation (玄幻修仙)"
    )
    theme: str = Field(description="Core theme, for example revenge and redemption, growth and transformation")
    world_setting: str = Field(description="Period background and world setting, 100-200 characters")
    # language archives LLM output; the sole source of truth is top-level project["source_language"],
    # synchronized by generate_overview on persistence. All measurement/splitting callers read the top-level field;
    # never read overview.language directly.
    language: Literal["zh", "en", "vi", "ru"] = Field(description="Novel source-language code")


def _rename_agnostic_errors(
    result: ValidationResult, old_name: str, new_name: str
) -> dict[tuple[str, tuple[tuple[str, str], ...]], str]:
    r"""Reduce validation errors to identity-independent fingerprints for the renamed asset, mapped to readable text.

    Asset rename's no-worse criterion compares pre/post error sets. Many validation messages name assets
    (format errors, invalid field types, etc.), so parameters carry asset names. Comparing rendered text
    would mistake a historical error for a newly introduced one because the name changed, rejecting an
    otherwise no-worse rename. Build fingerprints from structured messages (key + params), folding the
    new name back to the old within parameters.

    Fold only two definite shapes: whole parameter values equal to the new name, and bracket segments
    in field paths such as ``characters[新名].xxx``. Do not replace arbitrary substrings: when the new name
    is part of unrelated text, generic replacement could fold a real new error onto an existing fingerprint
    and silently swallow it. If narrowing misses another embedded-name form, failure is conservative:
    a historical error is treated as new, rejecting the rename rather than allowing it."""

    def fold(value: str) -> str:
        if normalize_asset_name(value) == normalized_new:
            return old_name
        return value.replace(f"[{new_name}]", f"[{old_name}]")

    normalized_new = normalize_asset_name(new_name)
    folded: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
    for message in result.error_messages:
        params = tuple(
            sorted(
                (name, fold(value) if isinstance(value, str) else repr(value)) for name, value in message.params.items()
            )
        )
        folded[(message.key, params)] = message.render(default_translate)
    return folded


#: Rewrite target for deletion-preview counts via rename scanning: no asset name compares equal, so every matching reference counts.
_DELETION_SCAN_TARGET = "\x00"


@dataclass
class _AssetReferenceScan:
    r"""One asset-reference scan: rewritten scripts/drafts and reference counts by episode."""

    #: ``(剧本文件名, 改写后载荷, 改写前载荷)``
    scripts: list[tuple[str, dict, dict]] = field(default_factory=list)
    #: ``(草稿路径, 改写后载荷, 改写前指纹)``
    drafts: list[tuple[Path, dict, str]] = field(default_factory=list)
    #: Episode directory name (``episode_N``) → reference count; script filename stems, draft parent-directory names.
    episode_references: Counter[str] = field(default_factory=Counter)

    @property
    def references(self) -> int:
        return self.episode_references.total()


class ProjectManager:
    r"""Video project manager."""

    # Project subdirectory structure
    SUBDIRS: ClassVar[list[str]] = [
        "source",
        "scripts",
        "drafts",
        "characters",
        "scenes",
        "props",
        "products",
        "storyboards",
        "videos",
        "audio",
        "subtitles",
        "presentations",
        "thumbnails",
        "output",
        "grids",
    ]

    # Project-metadata filename
    PROJECT_FILE = PROJECT_FILENAME

    @staticmethod
    def normalize_project_name(name: str) -> str:
        """Validate and normalize a project identifier."""
        normalized = str(name).strip()
        if not normalized:
            raise ValueError("项目标识不能为空")
        if not PROJECT_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("项目标识仅允许英文字母、数字和中划线")
        return normalized

    @staticmethod
    def _slugify_project_title(title: str) -> str:
        r"""Build a filesystem-safe slug prefix from the project title.

        NFKD + ascii ignore removes all Han characters from CJK titles. If the result has no letters
        (empty / only isolated digits from the title, e.g. 第1集 → "1"), fall back to neutral ``proj``
        to avoid apparently meaningful but misleading slugs such as ``1-<hex>``.

        Truncate before checking letters: ``"1-2345...23abc"`` (>24 characters, letters at the end) leaves
        only digits after taking 24 characters and must also collapse to ``proj``."""
        ascii_text = unicodedata.normalize("NFKD", str(title).strip()).encode("ascii", "ignore").decode("ascii")
        slug = PROJECT_SLUG_SANITIZER.sub("-", ascii_text).strip("-_").lower()[:24]
        if not slug or not any(c.isalpha() for c in slug):
            return "proj"
        return slug

    def generate_project_name(self, title: str | None = None) -> str:
        """Generate a unique internal project identifier."""
        prefix = self._slugify_project_title(title or "")
        while True:
            candidate = f"{prefix}-{secrets.token_hex(4)}"
            if not (self.projects_dir / candidate).exists():
                return candidate

    @classmethod
    def for_project_dir(cls, project_dir: str | Path) -> "ProjectManager":
        r"""Construct a ProjectManager for a project directory's data root, inferred by the layout module."""
        return cls(DataRootLayout.for_project_dir(Path(project_dir)).root)

    def __init__(
        self,
        data_root: str | Path,
        *,
        script_reader: Callable[[Path], dict] | None = None,
        script_writer: Callable[[Path, dict], None] | None = None,
    ):
        r"""Initialize the project manager.

        Args:
            data_root: Data root; the data-root layout supplies project directories and other locations.
            script_reader: Script-JSON read seam; defaults to filesystem reads.
            script_writer: Atomic script-JSON write seam; defaults to atomic_write_json."""
        self.layout = DataRootLayout(Path(data_root))
        self.data_root = self.layout.root
        self.projects_dir = self.layout.projects_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._script_reader = script_reader
        self._script_writer = script_writer

    def list_projects(self) -> list[str]:
        r"""List all projects (selection defined by ``list_project_dirs``)."""
        return [d.name for d in list_project_dirs(self.projects_dir)]

    def get_global_assets_root(self) -> Path:
        r"""Return the global asset root, ensuring character/scene/prop subdirectories exist."""
        root = self.layout.global_assets_dir
        root.mkdir(parents=True, exist_ok=True)
        for sub in ("character", "scene", "prop"):
            (root / sub).mkdir(exist_ok=True)
        return root

    def create_project(
        self,
        name: str,
        content_mode: ContentMode = "narration",
        *,
        publish: bool = True,
    ) -> Path:
        r"""Create a project.

        Args:
            name: Globally unique project identifier used in URLs and the filesystem.
            content_mode: Content mode (narration / drama), selecting the materialized profile variant.
            publish: Whether to write a minimal project.json immediately; composed creation workflows should
                publish only after complete metadata is ready.

        Returns:
            Project directory path."""
        name = self.normalize_project_name(name)
        project_dir = self.projects_dir / name

        try:
            project_dir.mkdir()
        except FileExistsError as exc:
            raise FileExistsError(f"项目 '{name}' 已存在") from exc

        # Single-step calls persist content_mode by default; composed creation may defer publication until metadata is complete.
        try:
            for subdir in self.SUBDIRS:
                (project_dir / subdir).mkdir(exist_ok=True)
            if publish:
                atomic_write_json(project_dir / self.PROJECT_FILE, {"content_mode": content_mode})
            self.sync_agent_profile(project_dir, content_mode=content_mode)
        except Exception:
            # Roll back project_dir on sync failure so partial directories do not block retries with same-name FileExistsError.
            shutil.rmtree(
                project_dir, ignore_errors=True
            )  # hard-delete: rollback -- remove the newly created project directory when creation fails
            raise

        return project_dir

    # Concurrent readers (e.g. ProjectEventService polling) may acquire _project_lock and touch its lock file
    # after rmtree empties the directory but before removing it. POSIX rmdir then fails with nonempty-directory
    # ENOTEMPTY; Windows denies access (PermissionError / EACCES) while the other process still holds
    # the lock file. Both are platform-specific symptoms of the same race; retrying removes the lock file
    # together with the next cleanup attempt.
    _DELETE_RETRYABLE_ERRNOS = (errno.ENOTEMPTY, errno.EACCES)

    def delete_project_directory(self, name: str) -> None:
        r"""Hard-delete a project directory only to undo a newly created project; tolerate transient races with concurrent scans and deletion.

        Creator-initiated deletion uses deleted projects (``lib.project.deleted_projects``), not this method."""
        project_dir = self.get_project_path(name)
        attempts = 5
        for attempt in range(attempts):
            try:
                shutil.rmtree(project_dir)  # hard-delete: rollback -- undo failed project creation
                return
            except FileNotFoundError:
                # Directory already absent: an earlier retry or concurrent deletion succeeded. The deletion goal
                # is met; no further retry or error is needed. If it remains, concurrent residue cleanup
                # (remove_project_directory_residue) removed empty children first; retry.
                if not project_dir.exists():
                    return
                if attempt == attempts - 1:
                    raise
            except OSError as exc:
                if exc.errno not in self._DELETE_RETRYABLE_ERRNOS or attempt == attempts - 1:
                    raise
                time.sleep(0.05)

    def remove_project_directory_residue(self, name: str) -> None:
        r"""Remove residue consisting solely of empty directories under the same project name.

        Tasks still running after project deletion may recreate empty subdirectories under the old name via
        ``mkdir(parents=True)``. Leave directories with project.json untouched: they belong to a newly created
        or imported project of the same name. Delete only empty directories, preserving every file."""
        project_dir = safe_join(self.projects_dir, self.normalize_project_name(name))
        if (project_dir / self.PROJECT_FILE).exists():
            return
        for directory, _subdirs, _files in os.walk(project_dir, topdown=False):
            try:
                os.rmdir(directory)  # hard-delete: infra -- recreated empty directories under a deleted project name
            except OSError:
                continue

    def sync_agent_profile(
        self,
        project_dir: Path,
        *,
        content_mode: ContentMode | None = None,
    ) -> dict:
        r"""Materialize agent_runtime_profile into the project's .claude / CLAUDE.md.

        When ``content_mode=None``, read ``project_dir/project.json``. Missing project.json or ``content_mode``
        falls back to ``"narration"`` and logs info; an explicitly invalid ``content_mode`` raises ``ValueError``.

        See ``lib.agent.profile_manifest.sync_profile_to_project``: manifest-driven materialization uses sha256
        to distinguish builtin skill upgrades (auto-propagate), user edits (preserve), and intentional user
        deletions (do not revive). Remove unmodified project copies deleted upstream. The 15-row decision
        table fully covers name collisions, state-machine returns, etc.

        Returns:
            Dictionary with backward-compatible ``created/repaired/skipped/errors`` plus detailed stat keys."""
        if content_mode is None:
            content_mode = self._resolve_content_mode(project_dir)
        profile_dir = agent_profile_dir()
        return sync_profile_to_project(profile_dir, project_dir, content_mode)

    def force_resync_profile(
        self,
        project_dir: Path,
        *,
        paths: list[str] | None = None,
        content_mode: ContentMode | None = None,
    ) -> dict:
        r"""Force profile files over their project counterparts and refresh the manifest.

        For explicit actions such as the UI restore-builtin-skill button. ``paths=None`` means all files;
        a requested path deleted from the profile is skipped with a warning, not counted as an error.

        ``content_mode=None`` resolves automatically from project.json, as in ``sync_agent_profile``."""
        if content_mode is None:
            content_mode = self._resolve_content_mode(project_dir)
        profile_dir = agent_profile_dir()
        return _force_resync_profile(profile_dir, project_dir, content_mode, paths=paths)

    def get_agent_profile_status(self, project_dir: Path) -> dict[str, object]:
        """Describe project-local Agent Profile customizations for settings UI."""
        content_mode = self._resolve_content_mode(project_dir)
        return get_profile_status(agent_profile_dir(), project_dir, content_mode)

    def _resolve_content_mode(self, project_dir: Path) -> ContentMode:
        r"""Read content_mode from project_dir/project.json, falling back to narration when absent.

        Missing ``project.json`` or ``content_mode`` falls back to narration for old-project compatibility.
        Read/parse failure on an existing file raises, routing sync_all_agent_profiles to failed_projects.
        Silent fallback would mismatch a drama project's manifest mode and trigger destructive reset,
        incorrectly switching its profile to the narration variant."""
        pj_path = project_dir / self.PROJECT_FILE
        try:
            data = load_json(pj_path)
        except FileNotFoundError:
            logger.info("project.json missing under %s, defaulting content_mode=narration", project_dir)
            return "narration"
        mode = data.get("content_mode") if isinstance(data, dict) else None
        if mode is None:
            logger.info("project.json has no content_mode under %s, defaulting narration", project_dir)
            return "narration"
        if not isinstance(mode, str) or mode not in VALID_CONTENT_MODES:
            raise ValueError(
                f"project {project_dir.name}: invalid content_mode={mode!r} "
                f"(must be one of {sorted(VALID_CONTENT_MODES)})"
            )
        return cast(ContentMode, mode)

    def sync_all_agent_profiles(self) -> dict:
        r"""Scan project directories and materialize agent_runtime_profile (startup hook).

        Isolate individual project failures: catch ordinary exceptions and continue, counting ``failed_projects``.
        ``ProfileMissingError`` / ``ProfileEmptyError`` indicate deployment-level failures: skip everything and
        set ``aborted=True``, avoiding silent deletion of every project's .claude contents.

        Returns:
            Backward-compatible ``created/repaired/skipped/errors``, detailed stats, and fallback
            ``failed_projects`` / ``aborted`` fields."""
        totals = {
            "created": 0,
            "repaired": 0,
            "skipped": 0,
            "errors": 0,
            "failed_projects": 0,
            "aborted": False,
        }
        _STAT_KEYS_TO_AGGREGATE = (
            "created",
            "repaired",
            "skipped",
            "errors",
            "upgraded",
            "user_modified",
            "user_only",
            "pruned",
            "orphaned",
            "deleted_user",
            "tombstoned",
            "unchanged",
            "collision",
            "migrated_total",
        )
        for project_dir in list_project_dirs(self.projects_dir):
            try:
                result = self.sync_agent_profile(project_dir)
                for key in _STAT_KEYS_TO_AGGREGATE:
                    if key in result:
                        totals[key] = totals.get(key, 0) + result[key]
            except (ProfileMissingError, ProfileEmptyError, ProfileMisconfiguredError) as e:
                # Deployment-level errors (wrong profile path / failed volume mount): skip everything,
                # never destructively fall back to pretending the profile is empty.
                logger.error("profile sync ABORTED for ALL projects: %s", e)
                totals["aborted"] = True
                break
            except ValueError as e:
                # Skip a single invalid project content_mode without affecting others.
                logger.warning("Skip sync for %s: %s", project_dir.name, e)
                totals["failed_projects"] += 1
            except Exception:
                logger.exception("profile sync failed for %s", project_dir.name)
                totals["failed_projects"] += 1
        return totals

    def get_project_path(self, name: str) -> Path:
        r"""Get a project path with traversal protection.

        Within task execution, raise ``ProjectDeletedDuringTaskError`` if the project was deleted during the
        task: a same-name directory may now belong to another newly created or imported project."""
        name = self.normalize_project_name(name)
        ensure_task_project_claim(name)
        try:
            project_dir = safe_join(self.projects_dir, name)
        except PathTraversalError as exc:
            raise ValueError(f"非法项目名称: '{name}'") from exc
        if not project_dir.exists():
            raise FileNotFoundError(f"项目 '{name}' 不存在")
        return project_dir

    @staticmethod
    def _safe_subpath(base_dir: Path, filename: str) -> str:
        r"""Validate that filename joined to base_dir stays inside it; return the normalized absolute path string."""
        try:
            return str(safe_join(base_dir, filename))
        except PathTraversalError as exc:
            raise ValueError(f"非法文件名: '{filename}'") from exc

    def get_project_status(self, name: str) -> dict[str, Any]:
        r"""Get project status.

        Returns:
            Dictionary of completion by stage."""
        project_dir = self.get_project_path(name)

        status = {
            "name": name,
            "path": str(project_dir),
            "source_files": [],
            "scripts": [],
            "characters": [],
            "scenes": [],
            "props": [],
            "products": [],
            "storyboards": [],
            "videos": [],
            "outputs": [],
            "current_stage": "empty",
        }

        # Check directory contents
        for subdir in self.SUBDIRS:
            subdir_path = project_dir / subdir
            if subdir_path.exists():
                files = list(subdir_path.glob("*"))
                if subdir == "source":
                    status["source_files"] = [f.name for f in files if f.is_file()]
                elif subdir == "scripts":
                    status["scripts"] = [f.name for f in files if f.suffix == ".json"]
                elif subdir == "characters":
                    status["characters"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "scenes":
                    status["scenes"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "props":
                    status["props"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "products":
                    status["products"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "storyboards":
                    status["storyboards"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "videos":
                    status["videos"] = [f.name for f in files if f.suffix in [".mp4", ".webm"]]
                elif subdir == "output":
                    status["outputs"] = [f.name for f in files if f.suffix in [".mp4", ".webm"]]

        # Determine current stage
        if status["outputs"]:
            status["current_stage"] = "completed"
        elif status["videos"]:
            status["current_stage"] = "videos_generated"
        elif status["storyboards"]:
            status["current_stage"] = "storyboards_generated"
        elif status["characters"]:
            status["current_stage"] = "characters_generated"
        elif status["scripts"]:
            status["current_stage"] = "script_created"
        elif status["source_files"]:
            status["current_stage"] = "source_ready"
        else:
            status["current_stage"] = "empty"

        return status

    # ==================== Storyboard-script operations ====================

    def save_script(
        self,
        project_name: str,
        script: dict,
        filename: str,
        *,
        validate: bool = True,
        artifact_basis: ArtifactBasisDescriptor | None = None,
        expected_fingerprint: str | _Unset | None = _UNSET,
        project_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path:
        r"""Save a storyboard script.

        Args:
            project_name: Project name.
            script: Script dictionary.
            filename: Script filename; only canonical `episode_N.json` (optionally prefixed with `scripts/`)
                is accepted. Write-consistency validation rejects other filenames.
            validate: Whether to apply no-worse structural validation (default True, fail-safe). Direct saves
                lack prior scripts; the unified writer reads them as needed (no-worse when existing, strict
                validation for new saves).
            artifact_basis: Script-provenance basis frozen before generation begins; ordinary edits omit it,
                resolving current values at commit.
            expected_fingerprint: Optional formal-script content baseline; mismatch within the script lock rejects writes.
            project_update: Extra project.json changes in the same write transaction as script/episode index;
                available only for scripts with episode IDs.

        Returns:
            Saved file path."""
        filename = self.normalize_script_filename(filename)
        episode = script.get("episode")

        with self._script_lock(project_name, filename):
            if not isinstance(expected_fingerprint, _Unset):
                script_path = Path(self._safe_subpath(self.get_project_path(project_name) / "scripts", filename))
                actual_fingerprint, current_content = _file_content_fingerprint(script_path)
                if actual_fingerprint != expected_fingerprint:
                    raise ScriptWriteConflict(
                        expected=expected_fingerprint,
                        actual=actual_fingerprint,
                        current_content=current_content,
                    )
            prepare_on_commit: Callable[[], Callable[[Path], None] | None] | None = None
            before_script: dict | _Unset | None = _UNSET
            if type(episode) is int and episode > 0 and self.project_exists(project_name):
                from lib.artifacts.artifact_activation import prepare_episode_script_manifest_commit

                project_path = self.get_project_path(project_name)
                project_file = self._get_project_file_path(project_name)
                script_path = Path(self._safe_subpath(project_path / "scripts", filename))
                before_script = self._load_script_or_none(script_path)
                items, id_field, script_kind = resolve_items(script)
                resource_ids = tuple(
                    resource_id
                    for item in items
                    if isinstance(item, Mapping)
                    and isinstance((resource_id := item.get(id_field)), str)
                    and resource_id
                )
                previous_resource_ids: tuple[str, ...] = ()
                previous_kind = script_kind
                if before_script is not None:
                    try:
                        previous_items, previous_id_field, previous_kind = resolve_items(before_script)
                    except ScriptEditError:
                        pass
                    else:
                        previous_resource_ids = tuple(
                            resource_id
                            for item in previous_items
                            if isinstance(item, Mapping)
                            and isinstance((resource_id := item.get(previous_id_field)), str)
                            and resource_id
                        )

                def _prepare_manifest_commit() -> Callable[[Path], None] | None:
                    manifest_commit = prepare_episode_script_manifest_commit(
                        project_path,
                        episode=episode,
                        artifact_path=f"scripts/{filename}",
                        resource_ids=resource_ids,
                        removed_resource_ids=tuple(set(previous_resource_ids) - set(resource_ids)),
                        basis=artifact_basis,
                    )
                    # Before writing under the project lock: deletion records need pre-rewrite script/project.json snapshots.
                    before_files = {
                        rel: path.read_bytes()
                        for rel, path in ((f"scripts/{filename}", script_path), (self.PROJECT_FILE, project_file))
                        if path.is_file()
                    }

                    def _register_manifest(_script_path: Path) -> None:
                        # Move removed entries into project trash before unregistering, still under the script lock; new-entry media must not be persisted first.
                        # Overwrite creates structural-removal records for every old entry (ADR 0080), allowing recovery even without media.
                        trash_entries(
                            project_path,
                            episode=episode,
                            script_file=filename,
                            entries=dict.fromkeys(
                                (rid for rid in previous_resource_ids if rid not in resource_ids),
                                previous_kind,
                            ),
                            before=before_files,
                            removed=previous_resource_ids,
                            then=manifest_commit,
                        )

                    return _register_manifest

                prepare_on_commit = _prepare_manifest_commit

            return self._commit_script_unlocked(
                project_name,
                script,
                filename,
                validate=validate,
                before=before_script,
                prepare_on_commit=prepare_on_commit,
                project_update=project_update,
            )

    def _commit_script_unlocked(
        self,
        project_name: str,
        script: dict,
        filename: str,
        *,
        validate: bool,
        before: dict | _Unset | None = _UNSET,
        on_commit: Callable[[Path], None] | None = None,
        prepare_on_commit: Callable[[], Callable[[Path], None] | None] | None = None,
        project_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path:
        """Commit a script, its project index, and an optional sidecar hook together.

        The caller holds the canonical script lock.  An episode index, or a
        ``prepare_on_commit`` callback, also keeps the project lock through the
        formal write and hook so rollback cannot overwrite a concurrent project
        edit. The prepare callback runs only after that lock is held, so a
        schema-dependent sidecar plan cannot race with project activation.
        """

        if on_commit is not None and prepare_on_commit is not None:
            raise ValueError("on_commit and prepare_on_commit are mutually exclusive")

        scripts_dir = self.get_project_path(project_name) / "scripts"
        output_path = Path(self._safe_subpath(scripts_dir, filename))
        project_file = self._get_project_file_path(project_name)
        sync_project = project_file.is_file() and isinstance(script.get("episode"), int)
        lock_project = project_file.is_file() and (sync_project or prepare_on_commit is not None)
        if project_update is not None and not sync_project:
            raise ValueError("project_update requires an episode script of an existing project")

        if lock_project:
            with self._project_lock(project_name):
                prepared_on_commit = prepare_on_commit() if prepare_on_commit is not None else on_commit
                transaction_paths = (output_path, project_file) if sync_project else (output_path,)
                with formal_write_transaction(*transaction_paths):
                    output = self._write_script_unlocked(
                        project_name,
                        script,
                        filename,
                        sync_project=False,
                        validate=validate,
                        before=before,
                        emit_change=False,
                    )
                    if sync_project:
                        project = self._read_project_raw_unlocked(project_name)
                        if self._requires_unique_asset_namespace(project):
                            ensure_project_asset_namespace(project)
                        self._apply_episode_sync(project, script, filename)
                        if project_update is not None:
                            project_update(project)
                        self._migrate_legacy_resolution_on_save(project)
                        self._touch_metadata(project)
                        if self._requires_unique_asset_namespace(project):
                            ensure_project_asset_namespace(project)
                        atomic_write_json(project_file, project)
                    if prepared_on_commit is not None:
                        prepared_on_commit(output)
            changed_paths = [f"scripts/{output_path.name}", *([self.PROJECT_FILE] if sync_project else [])]
        else:
            prepared_on_commit = prepare_on_commit() if prepare_on_commit is not None else on_commit
            with formal_write_transaction(output_path):
                output = self._write_script_unlocked(
                    project_name,
                    script,
                    filename,
                    sync_project=False,
                    validate=validate,
                    before=before,
                    emit_change=False,
                )
                if prepared_on_commit is not None:
                    prepared_on_commit(output)
            changed_paths = [f"scripts/{output_path.name}"]

        emit_project_change_hint(project_name, changed_paths=changed_paths)
        return output

    def _write_script_unlocked(
        self,
        project_name: str,
        script: dict,
        filename: str,
        sync_project: bool = True,
        *,
        validate: bool = True,
        before: dict | _Unset | None = _UNSET,
        emit_change: bool = True,
    ) -> Path:
        r"""Script-write body: validate, update metadata, write atomically, and synchronize project.json.

        **Does not acquire `_script_lock`**: callers must already hold it (see `save_script` / `locked_script`)
        for concurrency protection. Separate to avoid reacquiring the same flock and self-deadlocking when
        `locked_script` reuses `save_script` (like `update_project` inlining `atomic_write_json` rather than
        reusing `save_project`). filename must already lack the `scripts/` prefix and be non-None.

        `sync_project=False` skips `sync_episode_from_script`, which reacquires `_project_lock` via
        `update_project`. Callers already holding the project lock (see `locked_episode_script`) must pass
        False to avoid self-deadlock. Skipping sync has no side effects for script-only edits without episode
        metadata changes.

        `validate=True` (default, fail-safe) uses no-worse structural validation: `raise ScriptStructureValidationError`
        only when pending data turns a valid script invalid; already-invalid old scripts remain allowed.
        Read-modify-write flows (`locked_script` family) already hold prior scripts and should pass `before`
        for zero extra reads. Direct saves omit `before`; this function reads prior data as needed (strict
        validation when absent). Asset-writeback hot paths touching only `generated_assets` pass
        `validate=False` for full exemption."""
        scripts_dir = self.get_project_path(project_name) / "scripts"
        real = self._safe_subpath(scripts_dir, filename)
        output_path = Path(real)

        # No-worse structural guard before persistence prevents corruption remaining hidden until worker execution.
        if validate:
            before_script = self._load_script_or_none(output_path) if isinstance(before, _Unset) else before
            self._guard_no_worse(before_script, script)

        # Validate filename/internal episode consistency too, avoiding a sync-stage error after script persistence
        # and the resulting partial commit with project.json unsynchronized.
        self.require_filename_episode_consistency(script, filename)

        # Update metadata, supporting old scripts without metadata or narration using segments.
        now = datetime.now(UTC).isoformat()
        metadata = script.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            script["metadata"] = metadata
        metadata.setdefault("created_at", now)
        metadata.setdefault("status", "draft")
        metadata["updated_at"] = now

        # Atomic write with traversal protection (output_path resolved before guards), avoiding JSON corruption from concurrent PATCH.
        self._persist_script_json(output_path, script)

        # Synchronize project.json so script persistence and metadata sync form one transaction
        # (sync uses `_project_lock`, distinct from outer `_script_lock`, so they do not conflict).
        if sync_project and self.project_exists(project_name) and isinstance(script.get("episode"), int):
            self.sync_episode_from_script(project_name, filename)

        if emit_change:
            emit_project_change_hint(
                project_name,
                changed_paths=[f"scripts/{output_path.name}"],
            )

        return output_path

    @staticmethod
    def normalize_script_filename(script_filename: str) -> str:
        r"""Strip `scripts/` and collapse `./` segments, normalizing identity consistently with `_script_lock`/`_safe_subpath`.

        `episode_1.json`, `scripts/episode_1.json`, `./episode_1.json`, `./scripts/episode_1.json` are valid aliases
        for the same script: `_safe_subpath` resolves real paths, so they share one file lock. Callers comparing
        across calls or constructing keys (e.g. per-script concurrency markers) should normalize here first to
        avoid mutually invisible identities. Collapse segments before stripping prefixes: otherwise
        `./scripts/x.json` does not initially match because `./` precedes the prefix; after collapse it becomes
        `scripts/x.json`, which `load_script`/`_script_lock` treats as a bare filename and prefixes again,
        producing `scripts/scripts/x.json`. Preserve `..` segments for `_safe_subpath` to reject; no boundary
        checking occurs here."""
        normalized = posixpath.normpath(script_filename).removeprefix("scripts/")
        return "" if normalized == "." else normalized

    @contextmanager
    def locked_script(
        self,
        project_name: str,
        script_filename: str,
        *,
        validate: bool = True,
        on_commit: Callable[[Path], None] | None = None,
        prepare_on_commit: Callable[[dict], Callable[[Path], None] | None] | None = None,
    ):
        r"""Perform script load → mutate → save under one `_script_lock`.

        Yield the script dictionary for in-place mutation; write back on normal exit. Exceptions inside the
        with block (e.g. missing scene/unit) skip writeback while releasing the lock normally. Symmetric with
        `update_project`, eliminating lost updates from concurrent writes between read and write.

        `validate=True` (default) snapshots the prior script before yield and applies no-worse validation on
        writeback with no extra reads. Asset-writeback hot paths touching only `generated_assets` pass
        `validate=False` for full exemption. ``prepare_on_commit`` receives the modified script under the
        project lock and returns a synchronous hook to run after formal writes."""
        norm = self.normalize_script_filename(script_filename)
        with self._script_lock(project_name, norm):
            # Already locked: use the unlocked read variant; migrations persist with writeback.
            script, _migrated = self._read_script_unlocked(project_name, norm)
            before = copy.deepcopy(script) if validate else None
            yield script
            self._commit_script_unlocked(
                project_name,
                script,
                norm,
                validate=validate,
                before=before,
                on_commit=on_commit,
                prepare_on_commit=(lambda: prepare_on_commit(script)) if prepare_on_commit is not None else None,
            )

    def script_lock(self, project_name: str, script_filename: str) -> AbstractContextManager[None]:
        r"""Nonreentrant exclusive script lock for callers acquiring it before the project lock, then calling :meth:`update_script_locked` inside."""
        return self._script_lock(project_name, script_filename)

    def update_script_locked(self, project_name: str, script_filename: str, mutate_fn: Callable[[dict], None]) -> dict:
        r"""With caller-held script then project locks (e.g. restore-pipeline landing rules), read-modify-write a script and sync its episode index; return the written script.

        Apply no-worse validation; like :meth:`update_project_locked`, provide neither failure rollback nor
        change hints."""
        norm = self.normalize_script_filename(script_filename)
        script, _migrated = self._read_script_unlocked(project_name, norm)
        before = copy.deepcopy(script)
        mutate_fn(script)
        self._write_script_unlocked(
            project_name, script, norm, sync_project=False, validate=True, before=before, emit_change=False
        )
        project = self._read_project_raw_unlocked(project_name)
        if isinstance(script.get("episode"), int):
            self._apply_episode_sync(project, script, norm)
        self._touch_metadata(project)
        atomic_write_json(self._get_project_file_path(project_name), project)
        return script

    @contextmanager
    def locked_project_script_snapshot(self, project_name: str, script_filename: str):
        """Yield a read-only project/script snapshot under the canonical write locks.

        Artifact selection must compare execution-frozen inputs with one coherent
        current snapshot, but a read-only comparison must not rewrite the script
        or touch project metadata.  The lock order mirrors
        :meth:`locked_episode_script` (script, then project), so callers can keep
        the comparison and a guarded downstream commit serialized with both
        source edit paths.
        """

        norm = self.normalize_script_filename(script_filename)
        with self._script_lock(project_name, norm), self._project_lock(project_name):
            project = self._read_project_raw_unlocked(project_name)
            script, _migrated = self._read_script_unlocked(project_name, norm)
            yield project, script

    @contextmanager
    def locked_project_snapshot(self, project_name: str):
        """Yield current project metadata under its canonical write lock."""

        with self._project_lock(project_name):
            project = self._read_project_raw_unlocked(project_name)
            yield project

    def _read_project_raw_unlocked(self, project_name: str) -> dict:
        r"""Read project.json raw without locks or migration, only for rechecks while holding `_project_lock`."""
        project_file = self._get_project_file_path(project_name)
        with open(project_file, encoding="utf-8") as f:
            return json.load(f)

    @contextmanager
    def locked_episode_script(
        self,
        project_name: str,
        resolve_script_file: Callable[[dict], str],
        *,
        validate: bool = True,
        on_commit: Callable[[Path], None] | None = None,
    ):
        r"""Resolve episode→script_file and read-modify-write its script under the uniform script-lock → project-lock order.

        `resolve_script_file(project) -> script_file`: caller-provided resolver finds/validates the target
        episode in project.json and returns its bound script filename, optionally raising e.g. 404/409.

        Candidate resolution → locking → binding recheck → write all complete within the `_project_lock`
        critical section, eliminating TOCTOU where a concurrent PATCH rebinds after an unlocked script_file
        read and the write reaches the old script. Acquire script then project locks, matching worker writeback
        (`locked_script` → sync) to avoid ABBA deadlocks.

        Write scripts with `sync_project=False` to skip sync in `_write_script_unlocked`, which would reacquire
        the project lock and self-deadlock. Instead inline episode-metadata sync and project.json writeback
        under the existing project lock, matching old `locked_script` → sync behavior (refreshing episodes
        metadata and `updated_at`).

        If bindings differ across lock acquisition, raise `EpisodeScriptReboundError` so callers retry.

        ``on_commit`` runs after script/project-index writes but before lock release, appending final steps
        such as the Artifact Manifest in the same domain commit. Snapshot both JSON files verbatim before
        commit. Failure in script, project, or hook atomically restores old bytes in the same critical section.
        Hooks must be designed not to raise after their own successful writes; this method cannot infer how
        to undo external state already persisted by them."""
        # Candidate resolution only determines script-lock identity; unlocked access is read-only. If later revision/schema
        # preflight rejects the command, project.json must remain byte-for-byte unchanged. On successful commit, migration
        # persists under the project lock below together with script/index, covered by the same old-byte compensation.
        candidate = resolve_script_file(self.load_project(project_name))
        norm = self.normalize_script_filename(candidate)
        with self._script_lock(project_name, norm), self._project_lock(project_name):
            project = self._read_project_raw_unlocked(project_name)
            if self._requires_unique_asset_namespace(project):
                ensure_project_asset_namespace(project)
            current = resolve_script_file(project)
            cur_norm = self.normalize_script_filename(current)
            if cur_norm != norm:
                raise EpisodeScriptReboundError(f"episode script binding changed: {norm} -> {cur_norm}")
            script_path = Path(self._safe_subpath(self.get_project_path(project_name) / "scripts", norm))
            project_path = self._get_project_file_path(project_name)
            script, _migrated = self._read_script_unlocked(project_name, norm)
            before = copy.deepcopy(script) if validate else None
            yield script
            with formal_write_transaction(script_path, project_path):
                self._write_script_unlocked(
                    project_name,
                    script,
                    norm,
                    sync_project=False,
                    validate=validate,
                    before=before,
                    emit_change=False,
                )
                # Inline project.json sync under the existing project lock (equivalent to update_project writes without reacquiring).
                if isinstance(script.get("episode"), int):
                    self._apply_episode_sync(project, script, norm)
                self._migrate_legacy_resolution_on_save(project)
                self._touch_metadata(project)
                if self._requires_unique_asset_namespace(project):
                    ensure_project_asset_namespace(project)
                atomic_write_json(project_path, project)
                if on_commit is not None:
                    on_commit(script_path)
            emit_project_change_hint(
                project_name,
                changed_paths=[f"scripts/{script_path.name}", self.PROJECT_FILE],
            )

    @staticmethod
    def filename_episode(script_filename: str) -> int | None:
        r"""Episode ID implied by the filename (`episode[-_\s]*N`, case-insensitive), or None if absent.

        Treat nonpositive parses such as `episode_0.json` as having no episode ID: 0 corresponds to no episode,
        and assigning it ownership would create an `episode: 0` ledger entry during metadata sync."""
        base_name = ProjectManager.normalize_script_filename(script_filename)
        filename_match = re.search(r"episode[-_\s]*(\d+)", base_name, re.IGNORECASE)
        if filename_match is None:
            return None
        episode = int(filename_match.group(1))
        return episode if is_episode_number(episode) else None

    @staticmethod
    def canonical_script_episode(script_filename: str) -> int | None:
        r"""Which episode owns this script: N only for an exact canonical filename `episode_N.json`; otherwise unrecognized.

        Ownership depends solely on filename, independently of ledger bindings: since v15 `script_file` can
        only be the episode's canonical path (see `lib.project.project_migrations.v14_to_v15_formal_script_truth`).
        Files such as `episode_1_old.json` / `custom.json` belong to no episode."""
        base_name = ProjectManager.normalize_script_filename(script_filename)
        episode = ProjectManager.filename_episode(base_name)
        if episode is None or base_name != episode_script_filename(episode):
            return None
        return episode

    @staticmethod
    def require_filename_episode_consistency(script: dict, script_filename: str) -> None:
        r"""Validate a canonical episode filename matching the script's `episode` field; otherwise raise ValueError.

        Noncanonical scripts belong to no episode and are rejected, without writing or registering episode
        bindings from the script's `episode`. If the script lacks an int `episode`, require only a canonical filename."""
        base_name = ProjectManager.normalize_script_filename(script_filename)
        owner_episode = ProjectManager.canonical_script_episode(base_name)
        if owner_episode is None:
            raise ValueError(
                f"脚本 {base_name} 的文件名不是规范名（应为 episode_N.json），拒绝操作以避免污染 project.json"
            )
        script_episode = script.get("episode")
        if not is_episode_number(script_episode):
            return
        if script_episode != owner_episode:
            raise ValueError(
                f"脚本 {base_name} 内部 episode={script_episode} 与它归属的 "
                f"episode={owner_episode} 不一致，拒绝操作以避免污染 project.json"
            )

    def _persist_script_json(self, path: Path, script: dict) -> None:
        r"""Sole script-JSON persistence exit: atomic by default, using injected ``script_writer`` when supplied."""
        if self._script_writer is None:
            atomic_write_json(path, script)
        else:
            self._script_writer(path, script)

    def _load_script_or_none(self, path: Path) -> dict | None:
        r"""Read a raw prior-script snapshot; return None for absent/corrupt scripts, triggering strict validation.

        Share the ``script_reader`` seam with ``_read_script_unlocked`` so prior snapshots and formal reads
        use the same source. Injected readers signal unavailable scripts with ``OSError`` (absent/unreadable)
        or ``ValueError`` (corrupt); normalize these to None and propagate other exceptions."""
        if self._script_reader is None:
            loaded = load_json_or_none(path)
        else:
            try:
                loaded = self._script_reader(path)
            except (OSError, ValueError):
                return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _guard_no_worse(before: dict | None, after: dict) -> None:
        r"""No-worse guard: reject only pending data introducing new structural errors.

        Valid after → allow. Invalid after: valid/no prior state → reject (`raise`); already-invalid prior
        state → allow without penalizing legacy issues. Lazy validator import breaks the project_manager →
        validator → data_validator → project_manager cycle."""
        from lib.script.script_structure_validator import (
            ScriptStructureValidationError,
            validate_script_structure,
        )

        after_result = validate_script_structure(after)
        if after_result.valid:
            return
        if before is not None and not validate_script_structure(before).valid:
            return
        raise ScriptStructureValidationError(after_result)

    @staticmethod
    def resolve_episode_from_script(script: dict, script_filename: str) -> int:
        r"""Resolve an episode ID from a script.

        Prefer top-level script `episode` (source of truth), falling back to filename regex
        `episode[-_\s]*(\d+)` (underscore/space/hyphen separators); raise ValueError when both are absent.

        Replaces callers redundantly passing `--episode` CLI arguments and risking mismatches.

        Explicitly exclude `bool`, an `int` subclass: corrupt `episode: true` is treated as missing and uses
        the filename rather than silently becoming episode 1."""
        ep = script.get("episode")
        if isinstance(ep, int) and not isinstance(ep, bool):
            return ep
        match = re.search(r"episode[-_\s]*(\d+)", script_filename, re.IGNORECASE)
        if match:
            return int(match.group(1))
        raise ValueError(f"无法确定集号：剧本缺少 episode 字段且文件名 {script_filename} 不含 episodeN 模式")

    def sync_episode_from_script(self, project_name: str, script_filename: str) -> dict:
        r"""Synchronize episode information from a script file into project.json.

        Agents must call this after writing scripts so WebUI displays the episode list correctly.

        Args:
            project_name: Project name.
            script_filename: Script filename, e.g. episode_1.json.

        Returns:
            Updated project dictionary.

        Raises:
            ValueError: Noncanonical filename (not `episode_N.json`), or filename episode ID mismatches the
                script's `episode`, preventing incorrect script data from overwriting the actual episode
                (e.g. episode_10.json incorrectly containing episode=1 would overwrite episode 1)."""
        # Use the unlocked variant: the unified writer calls here while holding `_script_lock`;
        # migration writeback in `load_script` would reacquire that lock and self-deadlock.
        script, _migrated = self._read_script_unlocked(project_name, script_filename)
        return self.update_project_reconciling_episode_bindings(
            project_name, lambda project: self._apply_episode_sync(project, script, script_filename)
        )

    def _apply_episode_sync(self, project: dict, script: dict, script_filename: str) -> None:
        r"""Synchronize script episode/title/script_file into `project` in place without locking or writing.

        Shared by `sync_episode_from_script` (inside `update_project` lock) and `locked_episode_script`
        (inside the existing `_project_lock` critical section), avoiding duplicate episode-metadata sync logic."""
        base_name = self.normalize_script_filename(script_filename)
        # Defense in depth: SSE scanning calls directly without save_script, so validation remains necessary.
        self.require_filename_episode_consistency(script, base_name)

        script_episode = script.get("episode")
        episode_num = script_episode if is_episode_number(script_episode) else self.canonical_script_episode(base_name)
        episode_title = script.get("title", "")
        script_file = f"scripts/{base_name}"

        # Find/create the episode entry; the entire RMW holds one _project_lock to avoid concurrent-sync lost updates.
        episodes = project.setdefault("episodes", [])
        episode_entry: dict[str, Any] | None = next((ep for ep in episodes if ep["episode"] == episode_num), None)
        if episode_entry is None:
            # Episodes whose scripts precede ledger entries have no registered episode source.
            episode_entry = {"episode": episode_num, SOURCE_ORIGIN_FIELD: SourceOrigin.NONE.value}
            episodes.append(episode_entry)
        # Sync core metadata, excluding statistics computed on project-summary reads.
        episode_entry["title"] = episode_title
        episode_entry["script_file"] = script_file
        # Allocate entry IDs without reuse: record every ID appearing in persisted scripts in this episode's high-water mark.
        try:
            items, id_field, _kind = resolve_items(script)
        except ScriptEditError:
            items, id_field = [], ""
        if episode_num is not None:
            raise_item_id_high_water(
                episode_entry,
                max_item_number((item.get(id_field) for item in items if isinstance(item, Mapping)), episode_num),
            )

        logger.info("已同步剧集信息: Episode %d - %s", episode_num, episode_title)

    def item_id_floor(self, project: Mapping[str, Any], script: Mapping[str, Any], episode: int) -> int:
        r"""Highest main sequence number used in this episode: maximum of the ledger record and existing script IDs."""
        try:
            items, id_field, _kind = resolve_items(dict(script))
        except ScriptEditError:
            items, id_field = [], ""
        return max(
            episode_item_id_high_water(project, episode),
            max_item_number((item.get(id_field) for item in items if isinstance(item, Mapping)), episode),
        )

    def reserve_item_ids(self, project_name: str, episode: int, kind: str, count: int) -> list[str]:
        r"""Allocate ``count`` fresh IDs for writers reserving outside scripts (script-plan drafts), advancing the episode high-water mark to the last.

        Do not record the high-water mark for episodes absent from the ledger: first script persistence
        creates an entry and backfills it from script IDs then."""
        issued: list[str] = []

        def _mutate(project: dict) -> None:
            entry = find_episode(project, episode)
            script: Mapping[str, Any] = {}
            script_file = entry.get("script_file") if entry is not None else None
            if isinstance(script_file, str):
                scripts_dir = self.get_project_path(project_name) / "scripts"
                script_path = Path(self._safe_subpath(scripts_dir, self.normalize_script_filename(script_file)))
                script = self._load_script_or_none(script_path) or {}
            issuer = ItemIdIssuer(episode, kind, self.item_id_floor(project, script, episode))
            issued[:] = [issuer.issue() for _ in range(count)]
            if entry is not None and issued:
                raise_item_id_high_water(entry, issuer.last)

        self.update_project(project_name, _mutate)
        return issued

    def load_script(self, project_name: str, filename: str) -> dict:
        r"""Load a storyboard script, including legacy migration persisted under its script lock.

        Args:
            project_name: Project name.
            filename: Script filename.

        Returns:
            Script dictionary."""
        norm = self.normalize_script_filename(filename)
        # Read unlocked first: most scripts are already adopted. No locks or lock files here, preserving pre-migration read
        # concurrency/filesystem effects (missing scripts also fail loudly before any lock file is created).
        script, migrated = self._read_script_unlocked(project_name, norm)
        if not migrated:
            return script
        with self._script_lock(project_name, norm):
            # Reread under the lock before writeback: read-modify-write must share one script lock, or migration results
            # would overwrite concurrent writes made between unlocked reads and writeback. Do not use
            # _write_script_unlocked: migration only adopts format, so must not refresh metadata.updated_at,
            # synchronize project.json, or emit change hints.
            script, migrated = self._read_script_unlocked(project_name, norm)
            if migrated:
                real = Path(self._safe_subpath(self.get_project_path(project_name) / "scripts", norm))
                self._persist_script_json(real, script)
        return script

    def load_script_readonly(self, project_name: str, filename: str) -> dict:
        """Load a script with in-memory compatibility migrations but never persist them."""
        norm = self.normalize_script_filename(filename)
        script, _migrated = self._read_script_unlocked(project_name, norm)
        return script

    def _read_script_unlocked(self, project_name: str, filename: str) -> tuple[dict, bool]:
        r"""Read a raw script and migrate legacy data in place; return ``(剧本, 是否发生迁移)``; **do not acquire the script lock**.

        Callers own locking and writeback; shared by three kinds:
        - Read-modify-write callers already holding `_script_lock` (`locked_script` family and episode sync at
          the unified writer) persist migrations on their normal writeback. Reacquiring the same flock here
          would self-deadlock.
        - Unlocked probing in `load_script`: return immediately without lock contention if no migration occurs.
        - Locked rereading in `load_script`: obtain the latest content again under the lock before writeback."""
        project_dir = self.get_project_path(project_name)
        filename = self.normalize_script_filename(filename)
        real = Path(self._safe_subpath(project_dir / "scripts", filename))

        if self._script_reader is None:
            # Existence checks apply only to filesystem access; actual readers determine whether scripts exist.
            if not real.exists():
                raise FileNotFoundError(f"剧本文件不存在: {real}")
            with open(real, encoding="utf-8") as f:
                script = json.load(f)
        else:
            script = self._script_reader(real)

        migrated, warnings = migrate_script_unit_durations(script)
        for message in warnings:
            logger.warning("剧本 %s 时长收编迁移: %s", real.name, message.render(default_translate))
        return script, migrated

    def list_scripts(self, project_name: str) -> list[str]:
        r"""List all scripts in the project."""
        project_dir = self.get_project_path(project_name)
        scripts_dir = project_dir / "scripts"
        return [f.name for f in scripts_dir.glob("*.json")]

    def _canonical_episode_scripts(self, project_name: str) -> list[str]:
        r"""Return sorted canonically named scripts under ``scripts/`` as batch reference-rewrite candidates.

        Ownership depends solely on filenames (see ``canonical_script_episode``), using the same criterion
        before/after locking without rechecking inside. Noncanonical JSON belongs to no episode; rewriting
        lacks grounds and would raise ``ValueError`` in write-time episode-consistency validation, aborting
        an entire rename."""
        return sorted(
            name for name in self.list_scripts(project_name) if self.canonical_script_episode(name) is not None
        )

    # ==================== Character management ====================

    def update_character_sheet(self, project_name: str, script_filename: str, name: str, sheet_path: str) -> dict:
        r"""Update the character sheet path."""
        # Asset-writeback hot path changes only runtime fields, cannot worsen structure, and skips structural validation.
        with self.locked_script(project_name, script_filename, validate=False) as script:
            key = resolve_asset_key(script.get("characters"), name)
            if key is None:
                # Raise inside the lock so locked_script skips writeback.
                raise KeyError(f"角色 '{name}' 不存在")
            script["characters"][key]["character_sheet"] = sheet_path
        return script

    # ==================== Data-structure normalization ====================

    @staticmethod
    def create_generated_assets(content_mode: str = "narration") -> dict:
        r"""Create the standard generated_assets structure.

        Args:
            content_mode: Content mode ('narration' or 'drama').

        Returns:
            Standard generated_assets dictionary."""
        return {
            "storyboard_image": None,
            "storyboard_last_image": None,
            "video_clip": None,
            "video_thumbnail": None,
            "video_uri": None,
            "narration_audio": None,
            "grid_id": None,
            "grid_cell_index": None,
            "status": "pending",
            "video_generated_at": None,
        }

    @staticmethod
    def create_scene_template(scene_id: str, duration_seconds: int = 8) -> dict:
        r"""Create a standard scene-object template.

        Args:
            scene_id: Shot ID, e.g. "E1S01", already encoding the episode ID.
            duration_seconds: Shot duration in seconds.

        Returns:
            Standard scene dictionary."""
        return {
            "scene_id": scene_id,
            "duration_seconds": duration_seconds,
            "segment_break": False,
            "characters_in_scene": [],
            "scenes": [],
            "props": [],
            "visual": {
                "description": "",
                "shot_type": "medium shot",
                "camera_movement": "static",
                "lighting": "",
                "mood": "",
            },
            "action": "",
            "dialogue": {"speaker": "", "text": "", "emotion": "neutral"},
            "audio": {"dialogue": [], "narration": "", "sound_effects": []},
            "generated_assets": ProjectManager.create_generated_assets(),
        }

    def normalize_scene(self, scene: dict) -> dict:
        r"""Fill missing fields in one scene.

        Args:
            scene: Scene dictionary.

        Returns:
            Completed scene dictionary."""
        template = self.create_scene_template(
            scene_id=scene.get("scene_id", "000"),
            duration_seconds=scene.get("duration_seconds", 8),
        )

        # Merge visual fields
        if "visual" not in scene:
            scene["visual"] = template["visual"]
        else:
            for key in template["visual"]:
                if key not in scene["visual"]:
                    scene["visual"][key] = template["visual"][key]

        # Merge audio fields
        if "audio" not in scene:
            scene["audio"] = template["audio"]
        else:
            for key in template["audio"]:
                if key not in scene["audio"]:
                    scene["audio"][key] = template["audio"][key]

        # Fill generated_assets fields
        # generated_assets comes from script JSON on disk; external edits may corrupt it into non-dicts (None/strings/etc.).
        # Normalize with get_generated_assets first to avoid TypeError from membership checks/assignments below.
        assets = get_generated_assets(scene)
        assets_template = self.create_generated_assets()
        for key in assets_template:
            if key not in assets:
                assets[key] = assets_template[key]
        scene["generated_assets"] = assets

        # Fill other top-level fields
        top_level_defaults = {
            "segment_break": False,
            "characters_in_scene": [],
            "scenes": [],
            "props": [],
            "action": "",
            "dialogue": template["dialogue"],
        }

        for key, default_value in top_level_defaults.items():
            if key not in scene:
                scene[key] = default_value

        # Update status
        self.update_scene_status(scene)

        return scene

    def update_scene_status(self, scene: dict) -> str:
        r"""Update and return scene status based on generated_assets.

        Status values:
        - pending: Not started.
        - storyboard_ready: Storyboard image complete.
        - completed: Video complete.

        Args:
            scene: Scene dictionary.

        Returns:
            Updated status value."""
        # generated_assets comes from script JSON on disk; external edits may corrupt it into non-dicts (None/strings/etc.).
        # get_generated_assets normalizes to an empty dict, treating it as not generated and avoiding AttributeError from chained .get().
        assets = get_generated_assets(scene)

        has_image = bool(assets.get("storyboard_image"))
        has_video = bool(assets.get("video_clip"))

        if has_video:
            status = "completed"
        elif has_image:
            status = "storyboard_ready"
        else:
            status = "pending"

        assets["status"] = status
        scene["generated_assets"] = assets
        return status

    # ==================== Scene management ====================

    def update_scene_asset(
        self,
        project_name: str,
        script_filename: str,
        scene_id: str,
        asset_type: str,
        asset_path: str,
        *,
        on_commit: Callable[[Path], None] | None = None,
    ) -> dict:
        r"""Update a scene's generated-resource path.

        Args:
            project_name: Project name.
            script_filename: Script filename.
            scene_id: Shot ID.
            asset_type: Resource type ('storyboard_image' or 'video_clip').
            asset_path: Resource path.

        Returns:
            Updated script."""
        # Asset-writeback hot path changes only generated_assets, cannot worsen structure, and skips structural validation.
        # A corrupt shot-array key (e.g. segments: null) is more severe; writes must fail loudly.
        # Silent no-op hides data loss: workers may finish N video_clip writes thinking they succeeded while the UI
        # sees no writeback. Propagate ScriptEditError; workers handle fallback by failing tasks and requiring manual repair.
        # `resolve_items` shares three-structure discrimination with `_write_script_unlocked` / read helpers,
        # preventing reference-to-video from falling back to drama,
        # reading "scenes", silently returning [], then masking the root cause with a shot-not-found KeyError.
        with self.locked_script(
            project_name,
            script_filename,
            validate=False,
            on_commit=on_commit,
        ) as script:
            self._set_scene_asset_in_script(script, scene_id, asset_type, asset_path)
        return script

    def _set_scene_asset_in_script(
        self,
        script: dict[str, Any],
        scene_id: str,
        asset_type: str,
        asset_path: str,
    ) -> dict[str, Any]:
        """Mutate one loaded script; the caller owns its canonical script lock."""

        content_mode = script.get("content_mode", "narration")
        items, id_field, _kind = resolve_items(script)
        for item in items:
            # Skip non-dict elements in corrupt scripts, mirroring script_editor._find_index isinstance guards,
            # avoiding AttributeError from item.get(id_field); misses still fail loudly with KeyError below.
            if not isinstance(item, dict):
                continue
            if str(item.get(id_field)) != str(scene_id):
                continue
            assets = item.get("generated_assets")
            if not isinstance(assets, dict):
                assets = {}
                item["generated_assets"] = assets
            for key, default_value in self.create_generated_assets(content_mode).items():
                if key not in assets:
                    assets[key] = default_value
            assets[asset_type] = asset_path
            self.update_scene_status(item)
            return item
        raise KeyError(f"分镜 '{scene_id}' 不存在")

    def update_scene_asset_across_scripts(
        self,
        project_name: str,
        script_filenames: Sequence[str],
        scene_id: str,
        asset_type: str,
        asset_path: str,
        *,
        on_commit: Callable[[], None] | None = None,
        on_miss: Callable[[], None] | None = None,
    ) -> tuple[str, ...]:
        """Update every matching script and a final sidecar under one lock set.

        Restore operations can touch several episode scripts before registering
        one Manifest identity.  Holding every canonical script lock plus the
        project lock through rollback prevents a failed final registration from
        restoring blanket snapshots over a concurrent script edit.
        """

        # Lock/rewrite all named scripts with canonical filenames; others belong to no episode and remain untouched.
        normalized = tuple(
            sorted(
                {
                    name
                    for name in map(self.normalize_script_filename, script_filenames)
                    if self.canonical_script_episode(name) is not None
                }
            )
        )
        project_path = self.get_project_path(project_name)
        scripts_dir = project_path / "scripts"
        script_paths = [Path(self._safe_subpath(scripts_dir, name)) for name in normalized]
        project_file = self._get_project_file_path(project_name)
        changed: list[str] = []

        with ExitStack() as locks:
            for name in normalized:
                locks.enter_context(self._script_lock(project_name, name))
            locks.enter_context(self._project_lock(project_name))
            with formal_write_transaction(*script_paths, project_file):
                project = self._read_project_raw_unlocked(project_name)
                for name in normalized:
                    try:
                        script, _migrated = self._read_script_unlocked(project_name, name)
                        before = copy.deepcopy(script)
                        self._set_scene_asset_in_script(script, scene_id, asset_type, asset_path)
                    except KeyError:
                        continue
                    except ScriptEditError as exc:
                        logger.warning("跨集同步元数据跳过脏脚本 %s: %s", name, exc)
                        continue
                    except OSError as exc:
                        logger.warning("跨集同步元数据 sibling 集 %s IO 失败: %s", name, exc)
                        continue
                    self._write_script_unlocked(
                        project_name,
                        script,
                        name,
                        sync_project=False,
                        validate=False,
                        before=before,
                        emit_change=False,
                    )
                    # Sync only the episode actually bound to this script in the ledger; writes must not use restore to rebind.
                    script_episode = script.get("episode")
                    if isinstance(script_episode, int) and resolve_episode_script_binding(
                        project, script_episode, name, require_indexed=True
                    ):
                        self._apply_episode_sync(project, script, name)
                    changed.append(name)

                if changed:
                    self._migrate_legacy_resolution_on_save(project)
                    self._touch_metadata(project)
                    if self._requires_unique_asset_namespace(project):
                        ensure_project_asset_namespace(project)
                    atomic_write_json(project_file, project)
                if changed and on_commit is not None:
                    on_commit()
                if not changed and on_miss is not None:
                    on_miss()

        if changed:
            emit_project_change_hint(
                project_name,
                changed_paths=[*(f"scripts/{name}" for name in changed), self.PROJECT_FILE],
            )
        return tuple(changed)

    def batch_update_scene_assets(
        self,
        project_name: str,
        script_filename: str,
        updates: list[tuple[str, str, Any]],
        *,
        on_commit: Callable[[Path], None] | None = None,
        prepare_on_commit: Callable[[dict], Callable[[Path], None] | None] | None = None,
    ) -> dict:
        r"""Update generated-resource paths for multiple scenes with one read/write.

        Args:
            project_name: Project name.
            script_filename: Script filename.
            updates: List of (scene_id, asset_type, asset_path).
            on_commit: Synchronous hook after script/project.json writes, before leaving the formal transaction.
            prepare_on_commit: Callback preparing ``on_commit`` from the modified script under the project lock;
                mutually exclusive with ``on_commit``.

        Returns:
            Updated script."""
        if not updates:
            return {}

        # Asset-writeback hot path changes only generated_assets, cannot worsen structure, and skips structural validation.
        # Corrupt shot-array keys (resolve_items raises ScriptEditError) and missing IDs both fail loudly:
        # silent no-op discards N completed clip paths while SSE still broadcasts all updated and the UI
        # remains pending forever. Collect all missing IDs before raising so workers see the whole failure set;
        # exceptions inside locked_script's with block prevent all writeback, matching single update_scene_asset.
        # resolve_items also lets reference-to-video workers index video_units correctly by unit_id.
        with self.locked_script(
            project_name,
            script_filename,
            validate=False,
            on_commit=on_commit,
            prepare_on_commit=prepare_on_commit,
        ) as script:
            content_mode = script.get("content_mode", "narration")
            items, id_field, _kind = resolve_items(script)

            # Build scene_id → item index to avoid O(N*M) lookup. Filter non-dict elements from corrupt scripts
            # (mirroring script_editor.new_item_ids); updates targeting them become missing → loud KeyError.
            item_by_id: dict[str, dict] = {str(item.get(id_field)): item for item in items if isinstance(item, dict)}
            missing: list[str] = []

            for scene_id, asset_type, asset_path in updates:
                item = item_by_id.get(str(scene_id))
                if item is None:
                    missing.append(str(scene_id))
                    continue

                assets = item.get("generated_assets")
                if not isinstance(assets, dict):
                    assets = {}
                    item["generated_assets"] = assets

                assets_template = self.create_generated_assets(content_mode)
                for key, default_value in assets_template.items():
                    if key not in assets:
                        assets[key] = default_value

                assets[asset_type] = asset_path
                self.update_scene_status(item)

            if missing:
                raise KeyError(f"批量回写命中失败：以下分镜不存在 {sorted(set(missing))}")
        return script

    def get_pending_scenes(self, project_name: str, script_filename: str, asset_type: str) -> list[dict]:
        r"""Get pending shots.

        Args:
            project_name: Project name.
            script_filename: Script filename.
            asset_type: Resource type.

        Returns:
            Pending-shot list."""
        script = self.load_script(project_name, script_filename)

        # `_resolve_items_or_warn` distinguishes all three script structures and warns/skips corrupt data. Silent reads
        # are safer than silent writes (empty UI lists beat 5xx blocking pages), but warnings keep failures observable;
        # write paths (update_scene_asset / batch_update_scene_assets) instead use `resolve_items` to
        # raise ScriptEditError directly, ensuring explicit corruption signals. Reference-to-video correctly returns
        # video_units rather than silently losing its data through a drama fallback.
        items = _resolve_items_or_warn(script, script_filename=script_filename)

        # Missing / null / non-dict item.generated_assets means not generated: read-side corruption tolerance
        # via get_generated_assets normalization, mirroring write-side update_scene_asset isinstance checks.
        def _missing(item: dict) -> bool:
            return not get_generated_assets(item).get(asset_type)

        # Filter corrupt non-dict elements, mirroring script_editor.new_item_ids, so the UI does not render garbage.
        return [item for item in items if isinstance(item, dict) and _missing(item)]

    # ==================== File-path utilities ====================

    def get_source_path(self, project_name: str, filename: str) -> Path:
        r"""Get the source-file path."""
        return self.get_project_path(project_name) / "source" / filename

    def get_character_path(self, project_name: str, filename: str) -> Path:
        r"""Get the character sheet path."""
        return self._get_asset_path("character", project_name, filename)

    def get_storyboard_path(self, project_name: str, filename: str) -> Path:
        r"""Get the storyboard-image path."""
        return self.get_project_path(project_name) / "storyboards" / filename

    def get_video_path(self, project_name: str, filename: str) -> Path:
        r"""Get the video path."""
        return self.get_project_path(project_name) / "videos" / filename

    def get_output_path(self, project_name: str, filename: str) -> Path:
        r"""Get the output path."""
        return self.get_project_path(project_name) / "output" / filename

    def get_scenes_needing_storyboard(self, project_name: str, script_filename: str) -> list[dict]:
        r"""Get shots needing storyboard-image generation (shared logic for both modes).

        Args:
            project_name: Project name.
            script_filename: Script filename.

        Returns:
            Shots needing storyboard-image generation."""
        script = self.load_script(project_name, script_filename)

        # Like get_pending_scenes: resolve_items three-mode discrimination, warn-and-skip fallback, and
        # get_generated_assets normalization for corrupt data.
        items = _resolve_items_or_warn(script, script_filename=script_filename)

        def _missing_storyboard(item: dict) -> bool:
            return not get_generated_assets(item).get("storyboard_image")

        # Like get_pending_scenes: filter non-dict elements, mirroring script_editor.new_item_ids.
        return [item for item in items if isinstance(item, dict) and _missing_storyboard(item)]

    # ==================== Project-level metadata management ====================

    def _get_project_file_path(self, project_name: str) -> Path:
        r"""Get the project-metadata file path."""
        return self.get_project_path(project_name) / self.PROJECT_FILE

    def project_exists(self, project_name: str) -> bool:
        r"""Check whether the project-metadata file exists."""
        try:
            return self._get_project_file_path(project_name).exists()
        except FileNotFoundError:
            return False

    def load_project(self, project_name: str) -> dict:
        r"""Load project metadata as an in-memory project.json snapshot without locks, migration, or writes.

        Args:
            project_name: Project name.

        Returns:
            Project-metadata dictionary."""
        project_file = self._get_project_file_path(project_name)

        if not project_file.exists():
            raise FileNotFoundError(f"项目元数据文件不存在: {project_file}")

        with open(project_file, encoding="utf-8") as f:
            return json.load(f)

    @contextmanager
    def _project_lock(self, project_name: str):
        r"""Acquire an exclusive project-file lock through a hidden lock file.

        Use a separate .project.json.lock, not the data file itself, so os.replace changing the inode does
        not invalidate the lock."""
        project_file = self._get_project_file_path(project_name)
        with project_metadata_lock(project_file.parent):
            yield

    @contextmanager
    def locked_source_mutation(self, project_name: str) -> Generator[Path]:
        """Serialize source-file mutations with project transactions.

        Source writers must use this context so a source-file change and its matching
        project.json commit observe one immutable source snapshot.
        """
        project_path = self.get_project_path(project_name)
        with self._project_lock(project_name):
            source_dir = project_path / "source"
            if source_dir.is_symlink() or source_dir.is_junction():
                raise ValueError("source 目录不得是符号链接或 junction")
            source_dir.mkdir(parents=True, exist_ok=True)
            yield source_dir

    @contextmanager
    def locked_source_registration(self, project_name: str) -> Generator[tuple[Path, dict, ExitStack]]:
        r"""Registration variant of :meth:`locked_source_mutation`: change source files and project.json registrations under the same project lock.

        Yield ``(source_dir, project, undo)``. Mutate ``project`` (whole-source list, episode ledger) in place
        inside the block. On normal exit, recalculate unplanned-source availability from files on disk, then
        write ``project.json`` if ``project`` changed. Register each on-disk file change's undo callback in ``undo``.
        On block exceptions or ``project.json`` writeback failure, do not write back, and execute callbacks in
        reverse registration order under the lock."""
        project_file = self._get_project_file_path(project_name)
        changed = False
        with self.locked_source_mutation(project_name) as source_dir:
            project = self._read_project_raw_unlocked(project_name)
            before = json.dumps(project, sort_keys=True, ensure_ascii=False)
            undo = ExitStack()
            try:
                yield source_dir, project, undo
                # The block may edit source text alone: always recompute unplanned-source availability from files on disk.
                record_source_remaining(source_dir.parent, project)
                if json.dumps(project, sort_keys=True, ensure_ascii=False) != before:
                    self._apply_project_mutation_unlocked(project_name, project, lambda _project: None)
                    atomic_write_json(project_file, project)
                    changed = True
            except BaseException:
                try:
                    undo.close()
                except Exception:
                    logger.exception("撤销源文改动失败: %s", project_name)
                raise
            undo.pop_all()
        if changed:
            emit_project_change_hint(project_name, changed_paths=[self.PROJECT_FILE])

    @asynccontextmanager
    async def async_file_lock(
        self,
        path: Path,
    ) -> AsyncGenerator[None]:
        """Cancellation-safe async counterpart of :meth:`file_lock`."""
        lock_path = path.parent / f".{path.name}.lock"

        # Register immediately after opening: cancellation during opening can lose its return value; this ensures closure.
        handles: list[BinaryIO] = []

        def _open_lock_file() -> BinaryIO:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+b")
            handles.append(handle)
            return handle

        try:
            handle = await run_sync_transaction(_open_lock_file)
        except BaseException:
            for opened in handles:
                opened.close()
            raise
        acquired = False
        try:
            while not acquired:
                try:
                    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    acquired = True
                except portalocker.AlreadyLocked:
                    await asyncio.sleep(0.05)
            yield
        finally:
            try:
                if acquired:
                    portalocker.unlock(handle)
            finally:
                handle.close()

    @contextmanager
    def file_lock(self, path: Path):
        r"""Acquire an exclusive arbitrary-file lock through a hidden lock file, publicly sharing mutual exclusion across modules.

        Name it `.{basename}.lock` (starting with `.`), in the file's directory, following `_project_lock` /
        `_script_lock` conventions and automatically excluded by directory globs. Use when multiple
        read-modify-write entry points for one file (e.g. script_plan draft migration writeback and body saving)
        need serialization. Lock the same real path at each entry; they need not know about each other."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / f".{path.name}.lock"
        lock_path.touch(exist_ok=True)
        with portalocker.Lock(lock_path, flags=portalocker.LOCK_EX):
            yield

    @contextmanager
    def _script_lock(self, project_name: str, script_filename: str):
        r"""Acquire an exclusive script-file lock through a hidden lock file.

        **Critical**: normalize filename with `_safe_subpath` before deriving the lock key. Otherwise aliases
        `./episode_1.json` / `episode_1.json` could resolve to one real path but acquire different locks,
        bypassing mutual exclusion."""
        scripts_dir = self.get_project_path(project_name) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_filename = self.normalize_script_filename(script_filename)
        real = Path(self._safe_subpath(scripts_dir, script_filename))
        with self.file_lock(real):
            yield

    def save_project(self, project_name: str, project: dict) -> Path:
        r"""Save project metadata.

        Args:
            project_name: Project name.
            project: Project-metadata dictionary.

        Returns:
            Saved file path."""
        project_file = self._get_project_file_path(project_name)

        self._migrate_legacy_resolution_on_save(project)
        self._touch_metadata(project)

        with self._project_lock(project_name):
            if self._requires_unique_asset_namespace(project):
                ensure_project_asset_namespace(project)
            atomic_write_json(project_file, project)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )

        return project_file

    def update_project(
        self,
        project_name: str,
        mutate_fn: Callable[[dict], None],
        *,
        on_commit: Callable[[Path], None] | None = None,
        formal_paths: Sequence[Path] = (),
    ) -> dict:
        r"""Atomically update project.json: file lock → read → modify → atomic writeback.

        Avoid lost-update races between concurrent tasks (e.g. multiple character-image generations).
        Return the written project-metadata dict so callers need not load_project again.

        Args:
            project_name: Project name.
            mutate_fn: Callback receiving and mutating the project dict in place.

        Returns:
            Written project-metadata dictionary (same structure as load_project)."""
        project_file = self._get_project_file_path(project_name)

        with self._project_lock(project_name), ExitStack() as transaction:
            if on_commit is not None or formal_paths:
                transaction.enter_context(formal_write_transaction(project_file, *formal_paths))
            project = self.update_project_locked(project_name, mutate_fn)
            if on_commit is not None:
                on_commit(project_file)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )

        return project

    def update_project_locked(self, project_name: str, mutate_fn: Callable[[dict], None]) -> dict:
        r"""Locked step of :meth:`update_project`: caller already holds the nonreentrant project lock, e.g. restore-pipeline landing rules.

        Read, mutate, normalize, and atomically write back, returning the written metadata; no failure
        rollback or change hints."""
        project = self._read_project_raw_unlocked(project_name)
        self._apply_project_mutation_unlocked(project_name, project, mutate_fn)
        atomic_write_json(self._get_project_file_path(project_name), project)
        return project

    def update_asset_entry(
        self,
        asset_type: str,
        project_name: str,
        name: str,
        mutate_fn: Callable[[dict], None],
        *,
        on_commit: Callable[[Path], None] | None = None,
        formal_paths: Sequence[Path] = (),
    ) -> dict:
        """Update one asset and reconcile a moved formal sheet claim.

        Every asset router shares this project-lock/formal-write boundary.  A
        metadata-only input change keeps the current sheet claim so currency
        comparison can report it stale; clearing or replacing the canonical
        sheet path removes the old claim in the same durable commit.

        ``on_commit`` runs inside that same commit, after the sheet claim is
        reconciled, so a mutation that also retires sub-resources (a derivative
        and its image) can retire their claims without a second transaction.
        """

        from lib.artifacts.artifact_activation import reconcile_artifact_target_claims
        from lib.artifacts.artifact_manifest import ArtifactKey

        spec = ASSET_SPECS[asset_type]
        project_dir = self.get_project_path(project_name)
        canonical_name: str | None = None
        sheet_path_changed = False
        result: dict[str, Any] = {}

        def _mutate(project: dict) -> None:
            nonlocal canonical_name, sheet_path_changed
            bucket = project.get(spec.bucket_key)
            key = resolve_asset_key(bucket, name)
            if not isinstance(bucket, dict) or key is None:
                raise KeyError(name)
            entry = bucket[key]
            if not isinstance(entry, dict):
                raise ValueError(f"project asset {spec.bucket_key}/{key} must be an object")
            previous_sheet_path = entry.get(spec.sheet_field)
            mutate_fn(entry)
            canonical_name = key
            sheet_path_changed = entry.get(spec.sheet_field) != previous_sheet_path
            result.update(entry)

        def _reconcile_claim(_project_file: Path) -> None:
            if not sheet_path_changed:
                return
            if canonical_name is None:  # pragma: no cover - mutation contract
                raise RuntimeError("asset update did not resolve a canonical identity")
            reconcile_artifact_target_claims(
                project_dir,
                (ArtifactKey.asset_sheet(asset_type, canonical_name),),
            )

        def _commit(project_file: Path) -> None:
            _reconcile_claim(project_file)
            if on_commit is not None:
                on_commit(project_file)

        self.update_project(project_name, _mutate, on_commit=_commit, formal_paths=formal_paths)
        return result

    def update_project_reconciling_episode_bindings(
        self,
        project_name: str,
        mutate_fn: Callable[[dict], None],
    ) -> dict:
        """Update project metadata and forget claims unowned after rebinding.

        The selected script is the ownership boundary for every episode-scoped
        formal artifact.  When a mutation changes that binding, reconcile all
        existing claims for the affected episode through the canonical target
        resolver and one Manifest compare-and-swap.
        """

        from lib.artifacts.artifact_activation import reconcile_artifact_target_claims
        from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter

        project_dir = self.get_project_path(project_name)
        changed_episodes: set[int] = set()

        def _bindings(project: Mapping[str, Any]) -> dict[int, str | None]:
            raw_episodes = project.get("episodes")
            if not isinstance(raw_episodes, list):
                return {}
            bindings: dict[int, str | None] = {}
            for raw_episode in raw_episodes:
                if not isinstance(raw_episode, Mapping):
                    continue
                episode = raw_episode.get("episode")
                if type(episode) is not int or episode < 1:
                    continue
                script_file = raw_episode.get("script_file")
                bindings[episode] = (
                    self.normalize_script_filename(script_file)
                    if isinstance(script_file, str) and script_file
                    else None
                )
            return bindings

        def _mutate(project: dict) -> None:
            before = _bindings(project)
            mutate_fn(project)
            after = _bindings(project)
            changed_episodes.update(
                episode for episode in before.keys() | after.keys() if before.get(episode) != after.get(episode)
            )

        def _reconcile_claims(_project_file: Path) -> None:
            if not changed_episodes:
                return
            adapter = ProjectArtifactManifestAdapter(project_dir)
            claimed_keys = tuple(key for key in adapter.snapshot_entries() if key.episode_number in changed_episodes)
            reconcile_artifact_target_claims(project_dir, claimed_keys, adapter=adapter)

        return self.update_project(project_name, _mutate, on_commit=_reconcile_claims)

    def _apply_project_mutation_unlocked(
        self, project_name: str, project: dict, mutate_fn: Callable[[dict], None]
    ) -> None:
        """Apply one mutation plus the canonical save-time normalizations.

        The caller owns the project lock and is responsible for the durable
        write. Keeping this sequence shared lets compound file transactions
        make their final existence decision and project mutation under one
        lock without re-entering :meth:`update_project`.
        """

        if self._requires_unique_asset_namespace(project):
            ensure_project_asset_namespace(project)
        # Mutation may remove ledger entries: first cover pre-change episode IDs in the historical high-water mark.
        raise_episode_id_high_water(project)
        planning_inputs = source_planning_inputs(project)
        mutate_fn(project)
        # Episode planning, manual splitting, replan adoption, reset, and deletion all change the ledger here. If ledger/source registrations change,
        # recompute unplanned-source availability from disk; project lists read that flag rather than source text.
        if source_planning_inputs(project) != planning_inputs:
            record_source_remaining(self.get_project_path(project_name), project)
        if self._requires_unique_asset_namespace(project):
            ensure_project_asset_namespace(project)
        self._migrate_legacy_resolution_on_save(project)
        self._touch_metadata(project)

    def update_project_with_file_copies(
        self,
        project_name: str,
        mutate_fn: Callable[[dict], None],
        copies: list[tuple[Path, Path]],
        *,
        on_commit: Callable[[Path], None] | None = None,
    ) -> dict:
        r"""Commit file copies, project.json, and an optional sidecar hook as one transaction under the project lock."""

        return self._update_project_with_files(
            project_name,
            mutate_fn,
            copies=copies,
            on_commit=on_commit,
        )

    def _update_project_with_files(
        self,
        project_name: str,
        mutate_fn: Callable[[dict], None],
        *,
        copies: list[tuple[Path, Path]] | None = None,
        writes: list[tuple[bytes, Path]] | None = None,
        on_commit: Callable[[Path], None] | None = None,
    ) -> dict:
        r"""Commit file changes and project.json writeback as one rollback-capable transaction under the project lock.

        ``mutate_fn`` may finalize naming under the lock and append operations to both lists. No files are
        installed if it raises. Stage all copies/byte writes first, then replace destinations individually.
        Installation/JSON-write failure restores in reverse order; restoration failures only log, preserving
        the original exception. Clean backups after successful commit. All destinations must differ to avoid
        ambiguous operation order within a transaction."""
        project_file = self._get_project_file_path(project_name)
        copies = copies if copies is not None else []
        writes = writes if writes is not None else []

        token = secrets.token_hex(8)
        staged: list[tuple[Path, Path]] = []
        installed: list[tuple[Path, Path | None]] = []
        committed = False
        with self._project_lock(project_name), ExitStack() as transaction:
            if on_commit is not None:
                transaction.enter_context(formal_write_transaction(project_file))
            try:
                with open(project_file, encoding="utf-8") as f:
                    project = json.load(f)
                if self._requires_unique_asset_namespace(project):
                    ensure_project_asset_namespace(project)
                mutate_fn(project)
                if self._requires_unique_asset_namespace(project):
                    ensure_project_asset_namespace(project)
                self._migrate_legacy_resolution_on_save(project)
                self._touch_metadata(project)

                # mutate_fn may finalize naming and fill copies under the lock; destination uniqueness must therefore
                # be validated after the callback while still holding the same project lock.
                replacement_destinations = [destination for _source, destination in copies] + [
                    destination for _content, destination in writes
                ]
                if len(set(replacement_destinations)) != len(replacement_destinations):
                    raise ValueError("项目文件事务包含重复目标路径")
                for index, (source, destination) in enumerate(copies):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.{token}-{index}.tmp")
                    shutil.copyfile(source, temporary)
                    staged.append((temporary, destination))
                for offset, (content, destination) in enumerate(writes, start=len(staged)):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.{token}-{offset}.tmp")
                    atomic_write_bytes(temporary, content)
                    staged.append((temporary, destination))

                for index, (temporary, destination) in enumerate(staged):
                    backup: Path | None = None
                    if destination.exists() or destination.is_symlink():
                        backup = destination.with_name(f".{destination.name}.{token}-{index}.bak")
                        os.replace(destination, backup)
                    installed.append((destination, backup))
                    os.replace(temporary, destination)

                atomic_write_json(project_file, project)
                if on_commit is not None:
                    on_commit(project_file)
                committed = True
            except BaseException:
                for destination, backup in reversed(installed):
                    try:
                        if destination.exists() or destination.is_symlink():
                            destination.unlink()  # hard-delete: rollback -- remove files newly installed by this write on failure
                        if backup is not None:
                            os.replace(backup, destination)
                    except OSError:
                        logger.exception("恢复项目文件事务失败: %s", destination)
                raise
            finally:
                for temporary, _destination in staged:
                    try:
                        temporary.unlink(missing_ok=True)  # hard-delete: temp -- write staging files
                    except OSError:
                        logger.warning("清理项目文件事务暂存失败: %s", temporary)
                if committed:
                    for _destination, backup in installed:
                        if backup is None:
                            continue
                        try:
                            backup.unlink(missing_ok=True)  # hard-delete: temp -- post-commit .bak backups
                        except OSError:
                            logger.warning("清理项目文件事务备份失败: %s", backup)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )
        return project

    @staticmethod
    def _touch_metadata(project: dict) -> None:
        r"""Before each project.json write, refresh updated time and raise the historical episode high-water mark to cover ledger IDs."""
        raise_episode_id_high_water(project)
        now = datetime.now(UTC).isoformat()
        if "metadata" not in project:
            project["metadata"] = {"created_at": now, "updated_at": now}
        else:
            project["metadata"]["updated_at"] = now

    @staticmethod
    def _requires_unique_asset_namespace(project: dict) -> bool:
        r"""From schema v6, prevent ordinary business writes from operating on corrupt asset namespaces."""
        return parse_project_schema_version(project) >= 6

    @staticmethod
    def _migrate_legacy_resolution_on_save(project: dict) -> None:
        r"""When project.model_settings contains resolution, clear matching legacy entries in video_model_settings.

        For each new model_settings key ("<provider>/<model>") with resolution set, remove resolution from
        video_model_settings[<model>]. Delete entries becoming empty; remove video_model_settings entirely
        when the legacy dict becomes empty."""
        model_settings = project.get("model_settings") or {}
        legacy = project.get("video_model_settings") or {}
        if not model_settings or not legacy:
            return
        for composite_key, entry in model_settings.items():
            if "/" not in composite_key:
                continue
            _, model_id = composite_key.split("/", 1)
            if not entry.get("resolution"):
                continue
            legacy_entry = legacy.get(model_id)
            if not legacy_entry:
                continue
            legacy_entry.pop("resolution", None)
            if not legacy_entry:
                legacy.pop(model_id, None)
        if not legacy:
            project.pop("video_model_settings", None)

    # Ad/short-film projects always have one episode: a single episode-1 ledger entry and its script file.
    AD_SINGLE_EPISODE: ClassVar[dict[str, str | int]] = {
        "episode": 1,
        "title": "",
        "script_file": episode_script_relpath(1),
        SOURCE_ORIGIN_FIELD: SourceOrigin.NONE.value,
    }
    # Data-layer fallback when creation omits target_duration, matching the creation wizard's default tier.
    AD_DEFAULT_TARGET_DURATION = 60

    def create_project_metadata(
        self,
        project_name: str,
        title: str | None = None,
        style: str | None = None,
        content_mode: str | None = "narration",
        aspect_ratio: str | None = "9:16",
        default_duration: int | None = None,
        episode_target_duration: int | None = None,
        style_template_id: str | None = None,
        extras: dict | None = None,
        target_duration: int | None = None,
        brief: str | None = None,
        narration: Mapping[str, object] | None = None,
    ) -> dict:
        r"""Create a new project-metadata file.

        `extras` supplies optional model/backend fields (e.g. video_backend / image_provider_t2i /
        image_provider_i2i / text_backend_{script,overview,style}). Callers remove empty values; this method
        literally writes existing extras keys. The retired single image_backend field is outside the write
        scope (unread by parsers and rejected at write boundaries); callers must no longer supply it.

        `target_duration` / `brief` are available only for content_mode=ad. Ad projects lack `default_duration`
        and `episode_target_duration`, with episodes always one entry for episode 1.

        `episode_target_duration` is target duration per episode in seconds; see `lib.episode.episode_target_duration`
        for bounds. A soft preference, injected only into script-plan prompts and review-panel comparison,
        without blocking.

        `narration` configures narration delivery and TTS snapshots (see `lib.speech.narration_config`), defaulting
        to post-production voiceover. TTS voiceover requires a complete snapshot."""
        project_name = self.normalize_project_name(project_name)
        project_title = str(title).strip() if title is not None else ""
        resolved_mode = content_mode or "narration"

        # Data-layer guard: mode-specific fields are mutually exclusive. Routes already return 400; cover non-route callers too.
        if resolved_mode == "ad":
            if default_duration is not None:
                raise ValueError("广告/短片项目不持有 default_duration（分镜时长按 target_duration 预算逐个分镜规划）")
            if episode_target_duration is not None:
                raise ValueError("广告/短片项目不持有 episode_target_duration（整集体量按 target_duration 预算规划）")
            if target_duration is not None and not is_int(target_duration, minimum=1):
                raise ValueError(f"target_duration 必须为正整数秒，当前为 {target_duration!r}")
            if brief is not None and not is_str(brief):
                raise ValueError(f"brief 必须是字符串，当前为 {brief!r}")
        else:
            if target_duration is not None:
                raise ValueError("target_duration 仅广告/短片项目（content_mode=ad）可用")
            if brief is not None:
                raise ValueError("brief 仅广告/短片项目（content_mode=ad）可用")

        # Align schema_version with CURRENT_SCHEMA_VERSION: new projects start current,
        # avoiding erroneous startup migration (e.g. v0→v1 clearing scenes/props when clues is absent).
        from lib.project.project_migrations import CURRENT_SCHEMA_VERSION

        project = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            # Allow empty strings: frontend i18n displays an unnamed-project fallback, avoiding a slug-style
            # project_name being fixed as the user-visible title.
            "title": project_title,
            "content_mode": resolved_mode,
            "aspect_ratio": aspect_ratio or "9:16",
            "style": style or "",
            "episodes": [],
            WHOLE_SOURCE_FILES_KEY: [],
            "characters": {},
            "scenes": {},
            "props": {},
            NARRATION_DELIVERY_FIELD: POST_PRODUCTION,
            "metadata": {
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        }
        if narration is not None:
            unknown = set(narration) - set(NARRATION_CONFIG_FIELDS)
            if unknown:
                raise ValueError(f"narration 只接受旁白交付配置字段，收到: {sorted(unknown)}")
            project.update(narration)
            validate_project_narration_config(project)
        if resolved_mode == "ad":
            project["target_duration"] = (
                target_duration if target_duration is not None else self.AD_DEFAULT_TARGET_DURATION
            )
            project["brief"] = brief if brief is not None else ""
            project["episodes"] = [dict(self.AD_SINGLE_EPISODE)]
        if default_duration is not None:
            project["default_duration"] = default_duration
        if episode_target_duration is not None:
            if not is_valid_episode_target_duration(episode_target_duration):
                raise ValueError(
                    f"episode_target_duration 必须是 {MIN_EPISODE_TARGET_DURATION}–"
                    f"{MAX_EPISODE_TARGET_DURATION} 秒的整数，当前为 {episode_target_duration!r}"
                )
            project[EPISODE_TARGET_DURATION_FIELD] = episode_target_duration
        if style_template_id is not None:
            project["style_template_id"] = style_template_id
        if extras:
            # Data-layer guard: forbid the retired single image_backend field (parsers ignore it; writing it would
            # recreate silently ignored legacy data). Routes already return 400; cover non-route callers too.
            if "image_backend" in extras:
                raise ValueError("image_backend 已废弃，请改用 image_provider_t2i / image_provider_i2i")
            # extras may only append optional fields, never overwrite validated/constructed core fields above;
            # otherwise non-route callers could bypass mode-exclusivity guards (e.g. default_duration on ad projects).
            reserved = set(project) | {
                "default_duration",
                EPISODE_TARGET_DURATION_FIELD,
                "style_template_id",
                "target_duration",
                "brief",
            }
            forbidden = reserved & set(extras)
            if forbidden:
                raise ValueError(f"extras 不允许覆盖核心字段: {sorted(forbidden)}")
            project.update(extras)

        # Generation mode/grid: routes already validate required binary values and ad exclusivity (400/422); cover non-route
        # callers too. Fill omitted values with data-layer defaults so new projects persist both fields, matching schema v5.
        generation_mode = project.setdefault("generation_mode", _DEFAULT_GENERATION_MODE)
        if not isinstance(generation_mode, str) or generation_mode not in VALID_GENERATION_MODES:
            raise ValueError(f"generation_mode 值无效: {generation_mode!r}，必须是 {sorted(VALID_GENERATION_MODES)}")
        grid_storyboard = project.setdefault("grid_storyboard", False)
        if not isinstance(grid_storyboard, bool):
            raise ValueError(f"grid_storyboard 必须是布尔值，当前为 {grid_storyboard!r}")
        if resolved_mode == "ad" and grid_storyboard:
            raise ValueError("广告/短片项目不支持宫格分镜（grid_storyboard）")

        self.save_project(project_name, project)
        return project

    def add_episode(self, project_name: str, episode: int, title: str, script_file: str) -> dict:
        r"""Add an episode to a project.

        Args:
            project_name: Project name.
            episode: Episode ID.
            title: Episode title.
            script_file: Relative script-file path.

        Returns:
            Updated project metadata."""

        def _mutate(project: dict) -> None:
            # Update existing entries, otherwise append; entire RMW holds one _project_lock.
            for ep in project["episodes"]:
                if ep["episode"] == episode:
                    ep["title"] = title
                    ep["script_file"] = script_file
                    return
            # Append episodes to airing order, excluding statistics calculated on project-summary reads.
            project["episodes"].append({"episode": episode, "title": title, "script_file": script_file})

        return self.update_project(project_name, _mutate)

    def sync_project_status(self, project_name: str) -> dict:
        r"""[Deprecated] Synchronize project status.

        Deprecated: statistics such as status, progress, and item_count are computed when reading project
        summaries, not stored in JSON files.

        Retained only for backward compatibility; performs no writes.

        Args:
            project_name: Project name.

        Returns:
            Project metadata without statistics, which project summaries inject."""
        import warnings

        warnings.warn(
            "sync_project_status() 已废弃。status 等统计字段现由项目摘要读时计算。",
            DeprecationWarning,
            stacklevel=2,
        )
        # Return project data without writing anything.
        return self.load_project(project_name)

    # ================ Unified project-asset API (character / scene / prop / product) ================
    #
    # Six private methods in this section use lib.project.asset_types.ASSET_SPECS to unify bucket reads/writes
    # for three project asset types: character / scene / prop. Public methods below (add_project_scene /
    # add_prop / get_scene / update_*_sheet, etc.) all delegate to these methods; signatures and exceptions
    # remain 100% compatible with old callers.

    def _add_asset(self, asset_type: str, project_name: str, name: str, entry: dict) -> bool:
        r"""Add an entry to project[bucket][name], returning False if already present in the same type.

        Read-modify-write under one update_project file lock avoids concurrent-add lost updates.

        Raises:
            ProjectAssetNameConflictError: Name belongs to another asset type or differs from an existing asset only by case."""
        name = validate_asset_name(name)
        spec = ASSET_SPECS[asset_type]
        added = False

        def _mutate(project):
            nonlocal added
            bucket = project.setdefault(spec.bucket_key, {})
            # Existing keys may be NFD; resolve comparison coordinates before conflict checks to avoid visually duplicate assets.
            if resolve_asset_key(bucket, name) is not None:
                logger.debug("%s '%s' 已存在于 project.json，跳过", spec.label_zh, name)
                return
            ensure_project_asset_name_available(project, name, requested_asset_type=asset_type)
            bucket[name] = entry
            added = True

        self.update_project(project_name, _mutate)
        if added:
            logger.info("添加%s: %s", spec.label_zh, name)
        return added

    def _add_assets_batch(self, asset_type: str, project_name: str, entries: dict[str, dict]) -> int:
        r"""Add entries in bulk, skipping existing names and returning the added count.

        Read-modify-write under one update_project file lock avoids concurrent bulk-add lost updates.
        Any name conflict with another asset type prevents the entire batch from being persisted.

        Raises:
            ProjectAssetNameConflictError: Any name belongs to another asset type or differs from an existing asset only by case."""
        spec = ASSET_SPECS[asset_type]
        # As in upsert_assets, normalized (strip + NFC) equivalent keys ({"李白", "  李白  "}
        # or NFC/NFD variants) must not silently overwrite each other. Fail the whole batch loudly without persistence
        # so callers recognize collisions and deduplicate.
        normalized_entries: dict[str, dict] = {}
        raw_keys_by_normalized: dict[str, str] = {}
        for raw_name, entry in entries.items():
            name = validate_asset_name(raw_name)
            if name in normalized_entries:
                raise ValueError(
                    f"{spec.bucket_key} 的 entries 含规范化后冲突的 name {name!r}："
                    f"原始键 {raw_keys_by_normalized[name]!r} 与 {raw_name!r} 在规范化（strip + NFC）后等价"
                )
            normalized_entries[name] = entry
            raw_keys_by_normalized[name] = raw_name
        entries = normalized_entries
        added = 0

        def _mutate(project):
            nonlocal added
            bucket = project.setdefault(spec.bucket_key, {})
            for name, entry in entries.items():
                if resolve_asset_key(bucket, name) is not None:
                    logger.debug("%s '%s' 已存在，跳过", spec.label_zh, name)
                    continue
                ensure_project_asset_name_available(project, name, requested_asset_type=asset_type)
                bucket[name] = entry
                added += 1
                logger.info("添加%s: %s", spec.label_zh, name)

        if entries:
            self.update_project(project_name, _mutate)
        return added

    def upsert_assets(self, project_name: str, table: str, entries: dict[str, dict]) -> dict[str, Any]:
        r"""Upsert assets by table (characters/scenes/props/products) and name: add if absent, change fields if present.

        Read-modify-write under one `update_project` file lock. After applying but before persistence, validate
        the resulting project payload structurally using **no-worse semantics**: raise and **do not persist**
        only when the upsert makes a previously valid project invalid (`update_project` skips atomic_write
        when mutation raises). Allow already-invalid historical data (e.g. empty `style`); otherwise the whole
        patch_project path would be unusable for such projects (old `add_assets.py` reported errors without
        blocking writes). Shares the unified script writer's `_guard_no_worse` basis, expanding add-only to editing.

        Return a **diagnostic dict**, not project metadata: ``added`` (new entry names), ``merged`` (merged
        existing names), ``dropped_fields`` (fields removed by the allowlist, {name: [field names]}), and
        ``dropped_legacy`` (removed historical fields such as type/importance, {name: [field names]}). Callers
        (MCP tools) use it to provide explicit Agent feedback. Silent dropping is intentional for least privilege,
        but complete silence makes Agents think reference_image / sheet_field writes succeeded. Diagnostics
        let tools explain ignored fields to Agents, preventing repeated attempts at the same discarded fields."""
        # data_validator imports this module (VALID_GENERATION_MODES) at module level; lazy import breaks the cycle.
        from lib.project.asset_derivatives import merge_agent_derivatives, normalize_agent_derivatives
        from lib.project.data_validator import DataValidator

        asset_type = self._resolve_asset_type(table)
        # Distinguish invalid entries types from empty objects so Agents can correct inputs precisely.
        if not is_shape(entries, dict):
            raise ValueError(f"entries 必须是对象（dict），当前为 {type(entries).__name__}")
        if not entries:
            raise ValueError("entries 不能为空（至少需要一个 name → attrs 条目）")
        # Normalize name: nonempty after strip + NFC, path-safe single component (validate_asset_name,
        # since names enter file paths and single-segment route parameters). Agent input like "  李白  " with spaces causes
        # later name-indexed lookups (character generation, etc.) to mismatch. Invalid names fail loudly.
        # Also detect normalization collisions: {"李白": {...}, "  李白  ": {...}} or NFC/NFD variants become
        # identical keys, silently overwriting earlier values. Fail loudly so Agents recognize collisions and deduplicate.
        normalized_entries: dict[str, dict] = {}
        raw_keys_by_normalized: dict[str, str] = {}
        for raw_name, attrs in entries.items():
            try:
                name = validate_asset_name(raw_name)
            except ValueError as exc:
                raise ValueError(f"{table}: {exc}") from None
            if not is_shape(attrs, dict):
                raise ValueError(f"{table} '{name}' 的内容必须是对象")
            if name in normalized_entries:
                raise ValueError(
                    f"{table} 的 entries 含规范化后冲突的 name {name!r}："
                    f"原始键 {raw_keys_by_normalized[name]!r} 与 {raw_name!r} 在规范化（strip + NFC）后等价"
                )
            normalized_entries[name] = attrs
            raw_keys_by_normalized[name] = raw_name

        spec = ASSET_SPECS[asset_type]
        # Field allowlists use spec's Agent-permission dimension `agent_editable_extra_fields`, **not** schema dimension
        # `extra_string_fields`, which includes system/user paths such as `reference_image` (like sheet_field,
        # updated via dedicated `update_character_reference_image` API), not directly writable by Agent patch_project.
        # Forbidden fields also include `sheet_field` (character_sheet / scene_sheet / prop_sheet, written back by the
        # asset-generation pipeline through dedicated `_update_asset_sheet` after images are ready), and arbitrary non-spec keys.
        # `_strip_legacy_asset_fields` handles historical type/importance fields; this allowlist adds least privilege.
        # Allowlisted `derivatives` (only for capable types) normalize/merge via `lib.project.asset_derivatives` Agent-write
        # rules: only variation descriptions are writable; the generation pipeline preserves sheet paths.
        allowed_fields = spec.agent_writable_fields
        # Collect allowlist/legacy field drops so callers explicitly inform Agents. Silent dropping remains intentional,
        # but returning a dict tells tools what was dropped so they inform Agents, preventing repeated LLM attempts.
        cleaned: dict[str, dict[str, Any]] = {}
        dropped_fields: dict[str, list[str]] = {}  # name → [fields dropped by the allowlist]
        dropped_legacy: dict[str, list[str]] = {}  # name → [fields removed by _LEGACY_ASSET_FIELDS]
        for name, attrs in normalized_entries.items():
            legacy_keys = sorted(set(attrs) & self._LEGACY_ASSET_FIELDS)
            if legacy_keys:
                dropped_legacy[name] = legacy_keys

            entry_clean: dict[str, Any] = {}
            non_allowed: list[str] = []
            for k, v in self._strip_legacy_asset_fields(attrs).items():
                if k in allowed_fields:
                    entry_clean[k] = v
                else:
                    non_allowed.append(k)
                    logger.debug(
                        "upsert_assets: %s '%s' 的字段 %r 不在 Agent 可编辑白名单 %s,已忽略",
                        table,
                        name,
                        k,
                        sorted(allowed_fields),
                    )
            if non_allowed:
                dropped_fields[name] = sorted(non_allowed)
            if DERIVATIVES_FIELD in entry_clean:
                entry_clean[DERIVATIVES_FIELD] = normalize_agent_derivatives(
                    entry_clean[DERIVATIVES_FIELD], spec=spec, field_path=f"{table} {name!r}"
                )
            cleaned[name] = entry_clean

        added: list[str] = []
        merged: list[str] = []
        noop: list[str] = []

        def _mutate(project: dict) -> None:
            validator = DataValidator(str(self.projects_dir))
            before_errors = set(validator.validate_project_payload(project).errors)  # Pre-change snapshot
            bucket = project.setdefault(spec.bucket_key, {})
            if not isinstance(bucket, dict):
                # Historical corruption: bucket_key exists but is not a dict (e.g. list/str). Continuing would cause
                # ambiguous AttributeError from bucket.get/bucket[name].update below. Fail loudly first with
                # unexpected type/offending key (mutation cannot physically apply to a non-dict; unrelated to no-worse semantics).
                raise ValueError(f"project[{spec.bucket_key!r}] 必须是对象，当前为 {type(bucket).__name__}")
            for name, attrs in cleaned.items():
                # Existing entry keys may be NFD: resolve the actual key and update in place, not a second NFC-named entry.
                match = find_project_asset_name(project, name)
                if match is not None and not match.is_entry(asset_type, name):
                    raise ProjectAssetNameConflictError(name, match, asset_type)
                key = match.name if match is not None else name
                existing = isinstance(bucket.get(key), dict)
                # Merge derivatives by name (update matching descriptions, add new names, keep unmentioned ones). Whole-table replacement
                # would erase unmentioned derivatives' sheet paths; other base-entry fields still replace whole values. Empty tables
                # do not count as changes, becoming no-op like entries whose fields are all dropped.
                derivatives = attrs.get(DERIVATIVES_FIELD) or {}
                body_attrs = {k: v for k, v in attrs.items() if k != DERIVATIVES_FIELD}
                # Detect no-op only for existing entries: update({}) after all fields are dropped by allowlist/legacy stripping
                # changes nothing. Classify as noop rather than merged to avoid falsely reporting one merge. New entries are
                # created even with empty cleaned data; description defaults to empty, required only for sheet generation.
                if existing and not body_attrs and not derivatives:
                    noop.append(name)
                    continue
                if existing:
                    bucket[key].update(
                        body_attrs
                    )  # Edit: merge fields, preserving existing fields such as sheet paths.
                    merged.append(name)
                else:
                    bucket[key] = self._build_asset_entry(asset_type, body_attrs.get("description", ""), body_attrs)
                    added.append(name)
                if derivatives:
                    merge_agent_derivatives(bucket[key], derivatives)
            after_errors = set(validator.validate_project_payload(project).errors)
            # No-worse uses error-set differences: after must add no errors compared with before.
            #   - Valid before, invalid after → new_errors=all after errors → reject
            #   - Already corrupt, same corruption after → new_errors=∅ → allow continued patching of historical data
            #   - Already corrupt, new error after (e.g. non-string entry description) → new_errors≠∅ → reject
            #   - Already corrupt, partially repaired after → new_errors=∅ → allow improvements to historical data
            # Stricter than comparing valid flags: prevents new-entry structural errors piggybacking into corrupt projects.
            new_errors = after_errors - before_errors
            if new_errors:
                raise ValueError("project.json 结构校验失败: " + "; ".join(sorted(new_errors)))

        self.update_project(project_name, _mutate)
        return {
            "added": added,
            "merged": merged,
            "noop": noop,
            "dropped_fields": dropped_fields,
            "dropped_legacy": dropped_legacy,
        }

    def delete_asset(self, project_name: str, table: str, name: str) -> dict:
        r"""Delete a project asset: its entry, sheet, originals, reference audio, version history, and derivatives become one deletion record.

        The asset entry owns the sheet manifest identity; leaving its registration would point to an absent
        asset, so all callers share this method. Rewrite project.json, version records, and manifest in one
        formal-write transaction, rolling back together if creation of the deletion record fails."""

        from lib.project import asset_trash

        asset_type = self._resolve_asset_type(table)
        spec = ASSET_SPECS[asset_type]
        project_dir = self.get_project_path(project_name)
        deleted_name: str | None = None
        deleted_entry: dict[str, Any] = {}
        before: dict[str, bytes] = {}

        def _mutate(project: dict) -> None:
            nonlocal deleted_name
            bucket = project.get(spec.bucket_key)
            key = resolve_asset_key(bucket, name)
            if not isinstance(bucket, dict) or key is None:
                raise KeyError(f"{spec.label_zh} '{name}' 不存在")
            before.update(asset_trash.state_before(project_dir))
            deleted_name = key
            entry = bucket[key]
            if isinstance(entry, dict):
                deleted_entry.update(entry)
            del bucket[key]

        def _trash(_project_file: Path) -> None:
            if deleted_name is None:  # pragma: no cover - mutate contract
                raise RuntimeError("asset deletion did not resolve a canonical identity")
            asset_trash.trash_asset(project_dir, spec, deleted_name, deleted_entry, before)

        return self.update_project(
            project_name, _mutate, on_commit=_trash, formal_paths=asset_trash.state_paths(project_dir)
        )

    def preview_asset_deletion(self, project_name: str, table: str, name: str) -> AssetDeletionPreview:
        r"""Read-only reference preview before deletion: how many script/draft references would become dangling.

        Deletion itself does not rewrite references (see :meth:`delete_asset`); dangling references are blocked
        only at generation. Preview is the sole opportunity to warn users beforehand. Reuse rename's rewrite
        scan for identical counts, including derivative references (``角色/衍生``) with their bases.

        Raises:
            FileNotFoundError: Project absent.
            KeyError: Asset absent."""
        asset_type = self._resolve_asset_type(table)
        spec = ASSET_SPECS[asset_type]
        if not self.project_exists(project_name):
            raise FileNotFoundError(f"项目不存在: {project_name}")
        with self._asset_cascade_locks(project_name) as (script_files, draft_files):
            bucket = self._read_project_raw_unlocked(project_name).get(spec.bucket_key)
            key = resolve_asset_key(bucket, name)
            if key is None:
                raise KeyError(f"{spec.label_zh} '{name}' 不存在")
            scan = self._scan_asset_references(
                project_name, script_files, draft_files, asset_type, key, _DELETION_SCAN_TARGET
            )
        by_episode: Counter[int] = Counter()
        for episode_dir, count in scan.episode_references.items():
            episode = self.filename_episode(episode_dir)
            if episode is not None:
                by_episode[episode] += count
        return AssetDeletionPreview(
            table=table,
            name=key,
            references=scan.references,
            episodes=tuple(
                AssetEpisodeReferences(episode=episode, references=count)
                for episode, count in sorted(by_episode.items())
            ),
        )

    # bucket_key (characters/scenes/props/products) → asset type, derived once from static ASSET_SPECS.
    _BUCKET_TO_ASSET_TYPE: ClassVar[dict[str, str]] = {spec.bucket_key: t for t, spec in ASSET_SPECS.items()}

    @classmethod
    def _resolve_asset_type(cls, table: str) -> str:
        r"""Map bucket_key to asset type; unknown tables raise ValueError listing valid values."""
        asset_type = cls._BUCKET_TO_ASSET_TYPE.get(table)
        if asset_type is None:
            raise ValueError(f"未知资产表: {table!r}，须是 {sorted(cls._BUCKET_TO_ASSET_TYPE)} 之一")
        return asset_type

    _LEGACY_ASSET_FIELDS = frozenset({"type", "importance"})

    @classmethod
    def _strip_legacy_asset_fields(cls, attrs: dict) -> dict:
        r"""Remove legacy type/importance fields left by schema evolution, returning a new dict."""
        return {k: v for k, v in attrs.items() if k not in cls._LEGACY_ASSET_FIELDS}

    #: script_plan formal content, editable drafts, and quarantined-draft filenames needing cascading rename (structured JSON carrying
    #: reference arrays / ``@[名称]`` bodies, becoming formal content on promotion). Draft names use
    #: the full ``lib.script.draft_quarantine`` registry rather than manual enumeration: missing one origin leaves its drafts
    #: with old names, failing unregistered-reference contracts on promotion until manually edited. Historical ``.md`` free-text aliases
    #: are excluded: reads support browsing them, but writes and generation no longer recognize them.
    _RENAME_DRAFT_FILENAMES = frozenset(
        {
            *SCRIPT_PLAN_FILENAMES.values(),
            REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
            *QUARANTINE_FILENAMES,
        }
    )

    @contextmanager
    def _asset_cascade_locks(self, project_name: str) -> Generator[tuple[list[str], list[Path]]]:
        r"""Hold locks for cascading all script/draft rewrites, yielding ``(剧本文件名, 草稿路径)``.

        Lock order is all script locks (filename-sorted) → draft file locks → project lock, matching
        ``locked_episode_script`` script → project order to avoid ABBA deadlocks. Shared by rename,
        derivative rename, and merge."""
        script_files = self._canonical_episode_scripts(project_name)
        drafts_root = self.get_project_path(project_name) / "drafts"
        draft_files = (
            sorted(p for p in drafts_root.glob("episode_*/*.json") if p.name in self._RENAME_DRAFT_FILENAMES)
            if drafts_root.is_dir()
            else []
        )
        with ExitStack() as stack:
            for filename in script_files:
                stack.enter_context(self._script_lock(project_name, filename))
            for path in draft_files:
                stack.enter_context(self.file_lock(path))
            stack.enter_context(self._project_lock(project_name))
            yield script_files, draft_files

    @staticmethod
    def _artifact_manifest_adapter(project_dir: Path):
        r"""Return an adapter if the project already has an artifact manifest, otherwise ``None``; renames do not create manifests."""
        from lib.artifacts.artifact_manifest import MANIFEST_FILENAME, ProjectArtifactManifestAdapter

        manifest_path = project_dir / MANIFEST_FILENAME
        if manifest_path.exists() or manifest_path.is_symlink():
            return ProjectArtifactManifestAdapter(project_dir)
        return None

    def _scan_asset_references(
        self,
        project_name: str,
        script_files: Sequence[str],
        draft_files: Sequence[Path],
        asset_type: str,
        old_name: str,
        new_name: str,
    ) -> _AssetReferenceScan:
        r"""Inside ``_asset_cascade_locks``, rewrite script/draft references from *old_name* to *new_name* in memory only.

        Rename preview/execution and pre-deletion reference preview share this scan, ensuring equal counts."""
        scan = _AssetReferenceScan()
        for filename in script_files:
            script, _migrated = self._read_script_unlocked(project_name, filename)
            before = copy.deepcopy(script)
            changes = rewrite_payload_references(script, asset_type, old_name, new_name)
            if changes:
                scan.scripts.append((filename, script, before))
                scan.episode_references[Path(filename).stem] += changes
        for path in draft_files:
            payload = load_json_or_none(path)
            if not isinstance(payload, dict):
                continue
            fingerprint = canonical_json_digest(payload)
            changes = rewrite_payload_references(payload, asset_type, old_name, new_name)
            if changes:
                scan.drafts.append((path, payload, fingerprint))
                scan.episode_references[path.parent.name] += changes
        return scan

    def _carry_script_plan_confirmations(
        self, project_dir: Path, project: dict, changed_drafts: Sequence[tuple[Path, dict, str]]
    ) -> None:
        r"""Cascade rewrites change asset names, not plan content: shift confirmation fingerprints for episodes confirmed against exactly the pre-rewrite content.

        ``changed_drafts`` contains ``(路径, 改写后载荷, 改写前指纹)``; fingerprints match confirmation records
        (canonical hashes of whole JSON, see ``content_fingerprint_of_data``). Only formal script plans enter
        confirmation checks. ``project`` is the project.json payload to commit with this rewrite batch.
        Without shifting, fingerprints mismatch rewritten plans: a name-only change would return an episode
        to pending confirmation, directing users to confirmation that overwrites the formal script."""
        # script_review imports this module at module level; lazy import breaks the cycle.
        from lib.script.script_review import (
            carry_confirmation_through_migration,
            content_fingerprint_of_data,
            script_plan_path,
        )

        for path, payload, before in changed_drafts:
            episode = self.filename_episode(path.parent.name)
            if episode is not None and script_plan_path(project_dir, project, episode) == path:
                carry_confirmation_through_migration(project, episode, before, content_fingerprint_of_data(payload))

    def rename_asset(
        self, project_name: str, table: str, old_name: str, new_name: str, *, dry_run: bool = False
    ) -> AssetRenameReport:
        r"""Single cascading asset-rename transaction entry for UI and Agent (docs/adr/0057).

        Under all script locks (filename-sorted) → draft file locks → project lock: scan name references in all
        episode scripts and script_plan drafts, plan related-file relocations, and no-worse validate project.json
        changes. ``dry_run=True`` stops here with an impact report; preview/execution share the scan and must
        have identical counts. Otherwise persist scripts → drafts → related files → version history → project.json
        → Artifact Manifest. Rekey the Manifest last via one whole-file CAS. If the process exits after committing
        project.json, rerunning the same rename uses old claims/new project bindings to finish remaining CAS.
        Lock order matches ``locked_episode_script`` script → project order, avoiding ABBA deadlocks.

        Raises:
            ValueError: Unknown table, invalid new name (``validate_asset_name``), or structural validation failure.
            AssetRenameNotFoundError: Old name absent; message includes idempotent-recovery guidance.
            AssetRenameConflictError: New name conflicts with an existing same-type asset under normalization.
            AssetRenameFileCollisionError: An orphan occupies a related-file relocation destination.
            AssetRenameHistoryCollisionError: New-name history already belongs to another asset."""
        # data_validator imports this module at module level; lazy import breaks the cycle, as in upsert_assets.
        from lib.artifacts.version_manager import VersionManager
        from lib.project.asset_derivative_rename import (
            owner_derivative_renames,
            plan_derivative_sheet_relocation,
            rewrite_derivative_sheet_paths,
        )
        from lib.project.data_validator import DataValidator

        asset_type = self._resolve_asset_type(table)
        spec = ASSET_SPECS[asset_type]
        new_clean = validate_asset_name(new_name)
        if not self.project_exists(project_name):
            raise FileNotFoundError(f"项目不存在: {project_name}")
        project_dir = self.get_project_path(project_name)

        with self._asset_cascade_locks(project_name) as (script_files, draft_files):
            project = self._read_project_raw_unlocked(project_name)
            bucket = project.get(spec.bucket_key)
            from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifest

            manifest_adapter = self._artifact_manifest_adapter(project_dir)

            def _plan_manifest_rekey(
                source_name: str,
                target_name: str,
                source_sheet: object,
                target_sheet: object,
            ):
                if manifest_adapter is None:
                    return None
                path_rewrites = (
                    {source_sheet: target_sheet}
                    if isinstance(source_sheet, str)
                    and source_sheet
                    and isinstance(target_sheet, str)
                    and target_sheet
                    and source_sheet != target_sheet
                    else {}
                )
                return ArtifactManifest(manifest_adapter).plan_entry_rekey(
                    ArtifactKey.asset_sheet(asset_type, source_name),
                    ArtifactKey.asset_sheet(asset_type, target_name),
                    artifact_path_rewrites=path_rewrites,
                )

            old_key = resolve_asset_key(bucket, old_name)
            if old_key is None:
                completed_key = resolve_asset_key(bucket, new_clean)
                if completed_key is not None:
                    completed_entry = bucket.get(completed_key) if isinstance(bucket, dict) else None
                    source_claim = (
                        manifest_adapter.get_entry(ArtifactKey.asset_sheet(asset_type, normalize_asset_name(old_name)))
                        if manifest_adapter is not None
                        else None
                    )
                    target_sheet = (
                        completed_entry.get(spec.sheet_field) if isinstance(completed_entry, Mapping) else None
                    )
                    if source_claim is not None and isinstance(target_sheet, str) and target_sheet:
                        recovery_plan = _plan_manifest_rekey(
                            normalize_asset_name(old_name),
                            completed_key,
                            source_claim.artifact_path,
                            target_sheet,
                        )
                        if not dry_run:
                            assert recovery_plan is not None
                            recovery_plan.commit()
                        return AssetRenameReport(
                            table=table,
                            old_name=normalize_asset_name(old_name),
                            new_name=completed_key,
                            episodes=0,
                            references=0,
                            files=0,
                            dry_run=dry_run,
                        )
                hint = (
                    "；新名已存在于资产表——可能上次重命名已成功（级联重命名可安全重试）"
                    if completed_key is not None
                    else ""
                )
                raise AssetRenameNotFoundError(f"{table} 中不存在名为 {old_name!r} 的资产{hint}")
            conflict_key = resolve_asset_key(bucket, new_clean)
            if conflict_key is not None and conflict_key != old_key:
                raise AssetRenameConflictError(conflict_key)
            ensure_project_asset_name_available(
                project,
                new_clean,
                requested_asset_type=asset_type,
                exclude_asset_type=asset_type,
                exclude_name=old_key,
            )

            # Scan: dry-run preview and execution share this logic.
            scan = self._scan_asset_references(project_name, script_files, draft_files, asset_type, old_key, new_clean)
            changed_scripts = scan.scripts
            changed_drafts = scan.drafts

            moves = plan_asset_file_renames(project_dir, spec, old_key, new_clean)
            version_manager = VersionManager(project_dir)
            version_files = version_manager.rename_resource(spec.bucket_key, old_key, new_clean, dry_run=True)
            # Derivatives' images, version snapshots, and manifest keys start with the base name and move with it (see
            # lib/project/asset_derivative_rename). Without derivatives the plan is empty and persistence does nothing.
            derivative_renames = (
                owner_derivative_renames(project[spec.bucket_key][old_key]) if spec.supports_derivatives else ()
            )
            derivatives = plan_derivative_sheet_relocation(
                project_dir,
                manifest_adapter=manifest_adapter,
                old_owner=old_key,
                new_owner=new_clean,
                renames=derivative_renames,
            )

            # Apply project.json changes to a copy and no-worse validate first; reject failures entirely without any writes.
            mutated = copy.deepcopy(project)
            validator = DataValidator(str(self.projects_dir))
            before_errors = _rename_agnostic_errors(validator.validate_project_payload(mutated), old_key, new_clean)
            entry = rekey_equivalent_entries(mutated[spec.bucket_key], old_key, new_clean)
            if isinstance(entry, dict):
                rewrite_entry_paths(entry, spec, old_key, new_clean)
                if ALIASES_FIELD in spec.extra_list_fields:
                    record_asset_aliases(entry, (old_key,), asset_name=new_clean)
                rewrite_derivative_sheet_paths(
                    entry, old_owner=old_key, new_owner=new_clean, renames=derivative_renames
                )
            if self._requires_unique_asset_namespace(mutated):
                ensure_project_asset_namespace(mutated)
            after_errors = _rename_agnostic_errors(validator.validate_project_payload(mutated), old_key, new_clean)
            new_errors = {after_errors[fingerprint] for fingerprint in after_errors.keys() - before_errors.keys()}
            if new_errors:
                raise ValueError("project.json 结构校验失败: " + "; ".join(sorted(new_errors)))

            manifest_rekey_plan = None
            if manifest_adapter is not None:
                source_asset_entry = project[spec.bucket_key][old_key]
                old_sheet = (
                    source_asset_entry.get(spec.sheet_field) if isinstance(source_asset_entry, Mapping) else None
                )
                new_sheet = entry.get(spec.sheet_field) if isinstance(entry, Mapping) else None
                manifest_rekey_plan = _plan_manifest_rekey(old_key, new_clean, old_sheet, new_sheet)

            report = AssetRenameReport(
                table=table,
                old_name=old_key,
                new_name=new_clean,
                episodes=len(scan.episode_references),
                references=scan.references,
                files=len(moves) + version_files + derivatives.files,
                dry_run=dry_run,
            )
            if dry_run:
                return report

            # Persist: every step is idempotent; rerun the same rename after mid-process failure to converge.
            for filename, script, before in changed_scripts:
                self._write_script_unlocked(
                    project_name,
                    script,
                    filename,
                    sync_project=False,
                    before=before,
                )
            self._carry_script_plan_confirmations(project_dir, mutated, changed_drafts)
            for path, payload, _fingerprint in changed_drafts:
                atomic_write_json(path, payload)
            self._rename_grid_references(
                project_dir, asset_type, ((old_key, new_clean),), moves=(*moves, *derivatives.moves)
            )
            for src, dst in moves:
                if src.exists():
                    os.replace(src, dst)
            version_manager.rename_resource(spec.bucket_key, old_key, new_clean)
            derivatives.relocate()
            self._touch_metadata(mutated)
            project_file = self._get_project_file_path(project_name)
            with formal_write_transaction(project_file):
                atomic_write_json(project_file, mutated)
                if manifest_rekey_plan is not None:
                    manifest_rekey_plan.commit()
                derivatives.commit_manifest()

        emit_project_change_hint(project_name, changed_paths=[self.PROJECT_FILE])
        return report

    @staticmethod
    def _rename_grid_references(
        project_dir: Path,
        asset_type: str,
        renames: Sequence[tuple[str, str]],
        *,
        moves: Iterable[tuple[Path, Path]],
    ) -> None:
        r"""Rename frozen reference-image names and paths in grid records (rules in ``rewrite_grid_reference_images``).

        *renames* maps old → new reference names: one pair for rename/derivative rename, one per derivative
        moved with images under the retained asset during merge. Write before file relocation: on same-operation
        reruns after failure, rewritten records no longer match old names, while unrewritten records' relocations
        remain planned. Both converge. Leave unreadable records unchanged."""
        from lib.script.grid.grid_manager import GridManager

        grids_dir = project_dir / "grids"
        if not grids_dir.is_dir():
            return
        path_rewrites = {
            unicodedata.normalize("NFC", src.relative_to(project_dir).as_posix()): dst.relative_to(
                project_dir
            ).as_posix()
            for src, dst in moves
        }

        def rewrite(grid: "GridGeneration") -> int:
            return sum(
                rewrite_grid_reference_images(
                    grid.reference_images or [], asset_type, old_name, new_name, path_rewrites
                )
                for old_name, new_name in renames
            )

        def mutate(grid: "GridGeneration") -> None:
            rewrite(grid)

        manager = GridManager(project_dir)
        for record in sorted(grids_dir.glob("grid_*.json")):
            grid_id = record.name.removesuffix(".json")
            try:
                grid = manager.get(grid_id)
            except (OSError, KeyError, TypeError, ValueError):
                continue
            if grid is not None and rewrite(grid):
                manager.update(grid_id, mutate, ignore_invalid=True)

    def rename_asset_derivative(
        self, asset_type: str, project_name: str, entry_name: str, old_name: str, new_name: str
    ) -> dict:
        r"""Cascading derivative-rename transaction: the base entry's derivative-table key and ``本体名/旧衍生名`` in every script/draft.

        Use :meth:`rename_asset` lock order (script → draft → project) and reference rewrite mechanism
        (``rewrite_payload_references``, old name ``本体名/旧衍生名``): derivatives share base names/identities
        with just one extra reference segment (``docs/adr/0072``), so should not use different rewrite rules.
        Return the renamed base entry.

        Raises:
            FileNotFoundError: Project absent.
            KeyError: Base entry absent.
            AssetRenameNotFoundError: Derivative absent under this base.
            AssetRenameConflictError: Another derivative under this base occupies the new name, including case-only differences.
            ValueError: Invalid new name (``validate_asset_name``) or non-object entry structure."""
        from lib.project.asset_derivative_rename import plan_derivative_sheet_relocation, rewrite_derivative_sheet_paths
        from lib.project.asset_derivatives import find_derivative_name

        spec = ASSET_SPECS[asset_type]
        new_clean = validate_asset_name(new_name)
        if not self.project_exists(project_name):
            raise FileNotFoundError(f"项目不存在: {project_name}")
        project_dir = self.get_project_path(project_name)

        with self._asset_cascade_locks(project_name) as (script_files, draft_files):
            project = self._read_project_raw_unlocked(project_name)
            bucket = project.get(spec.bucket_key)
            base_key = resolve_asset_key(bucket, entry_name)
            if not isinstance(bucket, dict) or base_key is None:
                raise KeyError(entry_name)
            entry = bucket[base_key]
            if not isinstance(entry, dict):
                raise ValueError(f"project asset {spec.bucket_key}/{base_key} must be an object")
            table = entry.get(DERIVATIVES_FIELD)
            if not isinstance(table, dict):
                raise AssetRenameNotFoundError(f"{base_key} 没有登记任何衍生")
            old_key = resolve_asset_key(table, old_name)
            if old_key is None:
                raise AssetRenameNotFoundError(f"{base_key} 下不存在名为 {old_name!r} 的衍生")
            taken = find_derivative_name(table, new_clean, exclude=old_key)
            if taken is not None:
                raise AssetRenameConflictError(taken)

            old_reference = derivative_reference(base_key, old_key)
            new_reference = derivative_reference(base_key, new_clean)

            # Scan, like rename_asset: complete in-memory changes, then persist scripts → drafts → project.json.
            changed_scripts: list[tuple[str, dict, dict]] = []
            for filename in script_files:
                script, _migrated = self._read_script_unlocked(project_name, filename)
                before = copy.deepcopy(script)
                if rewrite_payload_references(script, asset_type, old_reference, new_reference):
                    changed_scripts.append((filename, script, before))
            changed_drafts: list[tuple[Path, dict, str]] = []
            for path in draft_files:
                payload = load_json_or_none(path)
                if not isinstance(payload, dict):
                    continue
                fingerprint = canonical_json_digest(payload)
                if rewrite_payload_references(payload, asset_type, old_reference, new_reference):
                    changed_drafts.append((path, payload, fingerprint))

            # Derivative sheets' three coordinates (image, version snapshot, manifest key) all contain derivative names and move with entry keys;
            # plan before any write so conflicts reject the entire operation with zero bytes persisted.
            relocation = plan_derivative_sheet_relocation(
                project_dir,
                manifest_adapter=self._artifact_manifest_adapter(project_dir),
                old_owner=base_key,
                new_owner=base_key,
                renames=((old_key, new_clean),),
            )

            rekey_equivalent_entries(table, old_key, new_clean)
            rewrite_derivative_sheet_paths(
                entry, old_owner=base_key, new_owner=base_key, renames=((old_key, new_clean),)
            )

            for filename, script, before in changed_scripts:
                self._write_script_unlocked(
                    project_name,
                    script,
                    filename,
                    sync_project=False,
                    before=before,
                )
            self._carry_script_plan_confirmations(project_dir, project, changed_drafts)
            for path, payload, _fingerprint in changed_drafts:
                atomic_write_json(path, payload)
            self._rename_grid_references(
                project_dir, asset_type, ((old_reference, new_reference),), moves=relocation.moves
            )
            relocation.relocate()
            self._touch_metadata(project)
            project_file = self._get_project_file_path(project_name)
            with formal_write_transaction(project_file):
                atomic_write_json(project_file, project)
                relocation.commit_manifest()
            result = dict(entry)

        emit_project_change_hint(project_name, changed_paths=[self.PROJECT_FILE])
        return result

    def merge_asset(
        self,
        project_name: str,
        table: str,
        source_name: str,
        target_name: str,
        *,
        as_derivative: bool = False,
        dry_run: bool = False,
    ) -> "AssetMergeReport":
        r"""Single UI/Agent asset-merge transaction entry: merge the removed asset into the same-type retained asset.

        Use :meth:`rename_asset` locks and scan-before-persist process. Repoint all episode-script/script_plan
        draft references to the retained asset (rules in :mod:`lib.project.asset_merge`), leaving it unchanged
        except appended aliases (merge into base) or derivatives (merge as derivative). Do not merge removed
        assets' descriptions, sheets/version history, voice settings, originals, or reference audio: these join
        the entry in one deletion record. Move its derivatives under the retained asset, merging ones whose
        names match existing derivatives, including case-only differences.
        ``dry_run=True`` returns only an impact report; shared scanning ensures equal preview/execution counts.

        Persistence order: scripts → drafts → derivative sheets → project.json, manifest, and removed-asset
        deletion record in one formal-write transaction. Failure before project.json commit converges by
        rerunning the same merge (rewritten references no longer count).

        Raises:
            ValueError: Unknown table / structural validation failure.
            AssetMergeRejectedError: Unmergeable type, self-merge, or non-character merge as derivative.
            AssetMergeNotFoundError: Removed or retained asset absent.
            AssetRenameFileCollisionError: An orphan occupies a derivative-sheet relocation destination.
            AssetRenameHistoryCollisionError: Other version history exists under the destination derivative ID."""
        # Derivative/artifact modules depend back on this module via artifact planning; lazy import breaks the cycle, as in rename_asset.
        from lib.project import asset_trash
        from lib.project.asset_derivative_rename import (
            plan_derivative_sheet_relocation,
            rewrite_derivative_sheet_paths,
        )
        from lib.project.asset_derivatives import derivative_table, ensure_derivative_table
        from lib.project.asset_merge import (
            MERGEABLE_ASSET_TYPES,
            AssetMergeEpisodeImpact,
            AssetMergeNotFoundError,
            AssetMergeRejectedError,
            AssetMergeReport,
            DerivativeMergePlan,
            count_outdated_artifacts,
            current_artifact_probe,
            grid_merge_references,
            merge_payload_references,
            plan_derivative_merge,
            storyboard_reference_identities,
        )
        from lib.project.data_validator import DataValidator

        asset_type = self._resolve_asset_type(table)
        if asset_type not in MERGEABLE_ASSET_TYPES:
            raise AssetMergeRejectedError("type_not_mergeable", f"{table} 不支持合并")
        spec = ASSET_SPECS[asset_type]
        if as_derivative and not spec.supports_derivatives:
            raise AssetMergeRejectedError("derivative_needs_character", f"{table} 没有衍生，不能并为衍生")
        if not self.project_exists(project_name):
            raise FileNotFoundError(f"项目不存在: {project_name}")
        project_dir = self.get_project_path(project_name)

        with self._asset_cascade_locks(project_name) as (script_files, draft_files):
            project = self._read_project_raw_unlocked(project_name)
            bucket = project.get(spec.bucket_key)
            source = resolve_asset_key(bucket, asset_name_comparison_key(source_name))
            if not isinstance(bucket, dict) or source is None:
                raise AssetMergeNotFoundError(source_name)
            target = resolve_asset_key(bucket, asset_name_comparison_key(target_name))
            if target is None:
                raise AssetMergeNotFoundError(target_name)
            if source == target:
                raise AssetMergeRejectedError("same_asset", f"不能把 {source!r} 并入它自己")
            source_entry, target_entry = bucket[source], bucket[target]
            if not isinstance(source_entry, dict) or not isinstance(target_entry, dict):
                raise ValueError(f"project asset {spec.bucket_key} entries must be objects")

            # Removed character's derivatives: move images/versions/manifest keys with relocated ones; clear all records merged into existing names.
            # Reference rewrites need planned derivative names (using existing names for case-only matches), so plan before scanning.
            derivative_plan = (
                plan_derivative_merge(source_entry, target_entry, source, as_derivative=as_derivative)
                if spec.supports_derivatives
                else DerivativeMergePlan()
            )

            # Scan: dry-run preview and execution share this logic.
            impacts: dict[int, dict[str, int]] = {}

            def tally(episode: int | None, **counts: int) -> None:
                if episode is None:
                    return
                totals = impacts.setdefault(episode, {})
                for key, value in counts.items():
                    totals[key] = totals.get(key, 0) + value

            is_current = current_artifact_probe(project_dir)
            storyboard_identities = storyboard_reference_identities(project_dir, project, asset_type)
            carried_references = derivative_plan.carried_references(source, target)
            outdated_grid_ids, grid_reference_names = grid_merge_references(
                project_dir, asset_type, source, derivative_plan.folded
            )
            for new_reference, old_reference in carried_references.items():
                if old_reference in storyboard_identities:
                    storyboard_identities[new_reference] = storyboard_identities[old_reference]
            changed_scripts: list[tuple[str, dict, dict]] = []
            for filename in script_files:
                script, _migrated = self._read_script_unlocked(project_name, filename)
                before = copy.deepcopy(script)
                changes = merge_payload_references(
                    script, asset_type, source, target, as_derivative=as_derivative, derivatives=derivative_plan
                )
                if changes.total:
                    changed_scripts.append((filename, script, before))
                episode = self.canonical_script_episode(filename)
                storyboards, videos = (
                    count_outdated_artifacts(
                        before,
                        script,
                        asset_type,
                        episode,
                        is_current,
                        storyboard_identities=storyboard_identities,
                        carried_references=carried_references,
                        outdated_grid_ids=outdated_grid_ids,
                        grid_reference_names=grid_reference_names,
                    )
                    if episode is not None
                    else (0, 0)
                )
                if not changes.total and not storyboards and not videos:
                    continue
                tally(
                    episode,
                    script=changes.names,
                    speaker=changes.speakers,
                    prompt_text=changes.mentions,
                    storyboards=storyboards,
                    videos=videos,
                )
            changed_drafts: list[tuple[Path, dict, str]] = []
            for path in draft_files:
                payload = load_json_or_none(path)
                if not isinstance(payload, dict):
                    continue
                fingerprint = canonical_json_digest(payload)
                changes = merge_payload_references(
                    payload, asset_type, source, target, as_derivative=as_derivative, derivatives=derivative_plan
                )
                if not changes.total:
                    continue
                changed_drafts.append((path, payload, fingerprint))
                category = "draft" if path.name in QUARANTINE_FILENAMES else "script_plan"
                tally(
                    self.filename_episode(path.parent.name),
                    **{category: changes.names},
                    speaker=changes.speakers,
                    prompt_text=changes.mentions,
                )

            moved_pairs = tuple((name, name) for name in derivative_plan.moved)
            relocation = plan_derivative_sheet_relocation(
                project_dir,
                manifest_adapter=self._artifact_manifest_adapter(project_dir),
                old_owner=source,
                new_owner=target,
                renames=moved_pairs,
                carry_unlisted=False,
            )

            # Apply project.json changes to a copy and no-worse validate first; reject failures entirely without any writes.
            mutated = copy.deepcopy(project)
            validator = DataValidator(str(self.projects_dir))
            # Merge renames no retained assets; compare error fingerprints as written (folding is identity).
            before_errors = _rename_agnostic_errors(validator.validate_project_payload(mutated), target, target)
            merged_bucket = mutated[spec.bucket_key]
            discarded = merged_bucket.pop(source)
            kept = merged_bucket[target]

            def string_aliases(entry: Mapping[str, Any]) -> list[str]:
                aliases = entry.get(ALIASES_FIELD)
                return [alias for alias in aliases if isinstance(alias, str)] if isinstance(aliases, list) else []

            aliases_before = set(string_aliases(kept))
            if not as_derivative:
                record_asset_aliases(kept, (source, *string_aliases(discarded)), asset_name=target)
            if derivative_plan.created is not None or derivative_plan.moved:
                derivatives = ensure_derivative_table(kept)
                if derivative_plan.created is not None:
                    description = discarded.get("description")
                    derivatives[derivative_plan.created] = {
                        "description": description if isinstance(description, str) else "",
                        spec.sheet_field: "",
                    }
                source_derivatives = derivative_table(discarded)
                for name in derivative_plan.moved:
                    derivatives[name] = copy.deepcopy(source_derivatives[name])
                rewrite_derivative_sheet_paths(kept, old_owner=source, new_owner=target, renames=moved_pairs)
            if self._requires_unique_asset_namespace(mutated):
                ensure_project_asset_namespace(mutated)
            after_errors = _rename_agnostic_errors(validator.validate_project_payload(mutated), target, target)
            new_errors = {after_errors[fingerprint] for fingerprint in after_errors.keys() - before_errors.keys()}
            if new_errors:
                raise ValueError("project.json 结构校验失败: " + "; ".join(sorted(new_errors)))

            report = AssetMergeReport(
                table=table,
                source=source,
                target=target,
                as_derivative=as_derivative,
                aliases_added=tuple(alias for alias in string_aliases(kept) if alias not in aliases_before),
                derivative_created=derivative_plan.created,
                derivatives_moved=derivative_plan.moved,
                derivatives_folded=derivative_plan.folded,
                episodes=tuple(
                    AssetMergeEpisodeImpact(episode=episode, **counts) for episode, counts in sorted(impacts.items())
                ),
                dry_run=dry_run,
            )
            if dry_run:
                return report

            # Persist
            state_before = asset_trash.state_before(project_dir)
            for filename, script, before in changed_scripts:
                self._write_script_unlocked(project_name, script, filename, sync_project=False, before=before)
            self._carry_script_plan_confirmations(project_dir, mutated, changed_drafts)
            for path, payload, _fingerprint in changed_drafts:
                atomic_write_json(path, payload)
            # Relocated derivatives only change names; rewrite grid-record frozen names/paths in the same rename cascade.
            # Derivatives merged into existing ones change images; do not repoint records, so grids become outdated normally.
            self._rename_grid_references(
                project_dir,
                asset_type,
                tuple((old_reference, new_reference) for new_reference, old_reference in carried_references.items()),
                moves=relocation.moves,
            )
            relocation.relocate()
            self._touch_metadata(mutated)
            project_file = self._get_project_file_path(project_name)
            # Removed sheets, version history, named files, and derivatives merged into existing same-name ones enter project trash. Leaving them
            # would attach unrelated history to future same-name assets. Relocated derivatives already moved and are excluded from these records.
            with formal_write_transaction(*asset_trash.state_paths(project_dir)):
                atomic_write_json(project_file, mutated)
                relocation.commit_manifest()
                asset_trash.trash_asset(
                    project_dir,
                    spec,
                    source,
                    discarded,
                    state_before,
                    merged_into=target,
                    derivatives_moved=list(derivative_plan.moved),
                )

        emit_project_change_hint(project_name, changed_paths=[self.PROJECT_FILE])
        return report

    def _update_asset_sheet(
        self,
        asset_type: str,
        project_name: str,
        name: str,
        sheet_path: str,
        *,
        on_commit: Callable[[Path], None] | None = None,
    ) -> dict:
        r"""Update an asset's sheet-field path; raise KeyError if absent.

        Read-modify-write under one update_project file lock avoids lost updates with concurrent add/update tasks."""
        spec = ASSET_SPECS[asset_type]

        def _mutate(project):
            bucket = project.get(spec.bucket_key)
            key = resolve_asset_key(bucket, name)
            if not isinstance(bucket, dict) or key is None:
                raise KeyError(f"{spec.label_zh} '{name}' 不存在")
            bucket[key][spec.sheet_field] = sheet_path

        return self.update_project(project_name, _mutate, on_commit=on_commit)

    def install_asset_sheet_bytes(
        self,
        asset_type: str,
        project_name: str,
        name: str,
        sheet_path: str,
        content: bytes,
        *,
        on_commit: Callable[[Path], None] | None = None,
        require_asset: bool = False,
    ) -> dict | None:
        """Install sheet bytes after rechecking their asset under the project lock.

        Stable sheet paths may be uploaded before an asset definition exists.
        That case writes only unclaimed bytes while still holding the same lock
        used by asset creation. If the definition exists by the final check,
        the file, metadata pointer, and sidecar hook commit atomically instead.
        With ``require_asset`` a missing definition writes nothing: callers whose
        bytes belong to one existing asset (provider imports) must not leave a
        file behind once it has been deleted.
        """

        project_dir = self.get_project_path(project_name)
        target = safe_join(project_dir, sheet_path)
        project_file = self._get_project_file_path(project_name)
        spec = ASSET_SPECS[asset_type]

        with self._project_lock(project_name):
            project = self._read_project_raw_unlocked(project_name)
            bucket = project.get(spec.bucket_key)
            if resolve_asset_key(bucket, name) is None:
                if require_asset:
                    return None
                with formal_write_transaction(target):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_bytes(target, content)
                return None

            def _mutate(locked_project: dict) -> None:
                locked_bucket = locked_project.get(spec.bucket_key)
                key = resolve_asset_key(locked_bucket, name)
                if not isinstance(locked_bucket, dict) or key is None:
                    raise KeyError(f"{spec.label_zh} '{name}' 不存在")
                locked_bucket[key][spec.sheet_field] = sheet_path

            with formal_write_transaction(project_file, target):
                self._apply_project_mutation_unlocked(project_name, project, _mutate)
                atomic_write_json(project_file, project)
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, content)
                if on_commit is not None:
                    on_commit(target)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )
        return project

    def _get_asset(self, asset_type: str, project_name: str, name: str) -> dict:
        r"""Get an asset definition; raise KeyError if absent."""
        spec = ASSET_SPECS[asset_type]
        project = self.load_project(project_name)
        bucket = project.get(spec.bucket_key)
        key = resolve_asset_key(bucket, name)
        if not isinstance(bucket, dict) or key is None:
            raise KeyError(f"{spec.label_zh} '{name}' 不存在")
        return bucket[key]

    def _get_asset_path(self, asset_type: str, project_name: str, filename: str) -> Path:
        r"""Get the absolute asset-file path within the project directory."""
        spec = ASSET_SPECS[asset_type]
        return self.get_project_path(project_name) / spec.subdir / filename

    # ==================== Project-level character management ====================

    def add_project_character(
        self,
        project_name: str,
        name: str,
        description: str,
        voice_style: str | None = None,
        character_sheet: str | None = None,
    ) -> dict:
        r"""Add a project-level character.

        Args:
            project_name: Project name.
            name: Character name.
            description: Character description.
            voice_style: Voice style.
            character_sheet: Character sheet path.

        Returns:
            Updated project metadata."""
        name = validate_asset_name(name)

        def _mutate(project: dict) -> None:
            bucket = project["characters"]
            # Overwrites must use actual existing keys (possibly NFD), or visually duplicate old entries would remain.
            match = find_project_asset_name(project, name)
            if match is not None and not match.is_entry("character", name):
                raise ProjectAssetNameConflictError(name, match, "character")
            key = match.name if match is not None else name
            entry = self._build_asset_entry(
                "character",
                description,
                {"voice_style": voice_style or "", "character_sheet": character_sheet or ""},
            )
            # Overwrites replace only base fields: derivatives live inside base entries, so whole-entry replacement would erase them too.
            previous = bucket.get(key)
            if isinstance(previous, dict):
                inherited = previous.get(DERIVATIVES_FIELD)
                if isinstance(inherited, dict):
                    entry[DERIVATIVES_FIELD] = inherited
            bucket[key] = entry

        return self.update_project(project_name, _mutate)

    def update_project_character_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        r"""Update the project-level character sheet path."""
        return self._update_asset_sheet("character", project_name, name, sheet_path)

    def update_character_reference_image(self, project_name: str, char_name: str, ref_path: str) -> dict:
        r"""Update a character's reference-image path.

        Args:
            project_name: Project name.
            char_name: Character name.
            ref_path: Relative reference-image path.

        Returns:
            Updated project data."""

        def _mutate(project: dict) -> None:
            key = resolve_asset_key(project.get("characters"), char_name)
            if key is None:
                raise KeyError(f"角色 '{char_name}' 不存在")
            project["characters"][key]["reference_image"] = ref_path

        return self.update_project(project_name, _mutate)

    def update_character_reference_audio(self, project_name: str, char_name: str, ref_path: str) -> dict:
        r"""Update a character's reference-audio path (empty string clears it).

        Always stamp ``voice_updated_at`` for upload and clear paths. Compare with generated clips'
        ``generated_assets.video_generated_at`` to determine whether clips predate current voice settings.
        No extra dismissed boolean is needed: comparing ``voice_notice_dismissed_at`` with this timestamp
        naturally expresses a new version.

        Another stamping site is ``server/routers/assets.py::apply_to_project``: bulk global-library import
        writes characters within one update_project and cannot use this method, so stamps the same field there.

        Args:
            project_name: Project name.
            char_name: Character name.
            ref_path: Relative reference-audio path.

        Returns:
            Updated project data."""

        def _mutate(project: dict) -> None:
            self._set_character_reference_audio(project, char_name, ref_path)

        return self.update_project(project_name, _mutate)

    @staticmethod
    def _set_character_reference_audio(project: dict, char_name: str, ref_path: str) -> None:
        key = resolve_asset_key(project.get("characters"), char_name)
        if key is None:
            raise KeyError(f"角色 '{char_name}' 不存在")
        project["characters"][key]["reference_audio"] = ref_path
        project["characters"][key]["voice_updated_at"] = datetime.now(UTC).isoformat()

    def install_character_reference_audio(
        self,
        project_name: str,
        char_name: str,
        ref_path: str,
        content: bytes,
    ) -> dict:
        """Atomically install reference-audio bytes and point the character at them.

        Video currency selection hashes reference-audio bytes while holding the project lock.  Keeping every
        physical replacement in that same lock makes the project pointer and the bytes one coherent input snapshot.
        A replaced file with a different extension goes to the project trash inside the same commit.
        """

        project_dir = self.get_project_path(project_name)
        refs_audio_dir = project_dir / "characters" / "refs_audio"
        target = Path(self._safe_subpath(project_dir, ref_path))
        if os.path.realpath(target.parent) != os.path.realpath(refs_audio_dir):
            raise ValueError("reference audio target must be inside characters/refs_audio")
        stale: list[tuple[str, Path]] = []

        def _mutate(project: dict) -> None:
            key = resolve_asset_key(project.get("characters"), char_name)
            if key is None:
                raise KeyError(f"角色 '{char_name}' 不存在")
            old_audio = project["characters"][key].get("reference_audio")
            stale_audio = resolve_stale_reference_audio(project_dir, refs_audio_dir, old_audio, target)
            self._set_character_reference_audio(project, char_name, ref_path)
            if stale_audio is not None:
                stale.append((key, stale_audio))

        return self._update_project_with_files(
            project_name,
            _mutate,
            writes=[(content, target)],
            on_commit=lambda _file: self._trash_stale_reference_audio(project_dir, *stale[0]) if stale else None,
        )

    @staticmethod
    def _trash_stale_reference_audio(project_dir: Path, character: str, stale: Path) -> None:
        """Move the replaced or cleared audio to the project trash when it is the character's own file.

        Reference audio is owned by name and never shared, so a file under another name is not this character's to
        remove; the caller holds the lock.
        """

        rel = f"characters/refs_audio/{stale.name}"
        if owns_named_file(ASSET_SPECS["character"], character, rel) and stale.is_file() and not stale.is_symlink():
            # Trash landing rules depend on this module; a top-level import would cycle.
            from lib.project.media_trash import trash_reference_audio

            trash_reference_audio(project_dir, character, rel)

    def clear_character_reference_audio(self, project_name: str, char_name: str) -> dict:
        """Clear the reference and move the character's own audio file to the project trash in one commit."""

        project_dir = self.get_project_path(project_name)
        refs_audio_dir = project_dir / "characters" / "refs_audio"
        stale: list[tuple[str, Path]] = []

        def _mutate(project: dict) -> None:
            key = resolve_asset_key(project.get("characters"), char_name)
            if key is None:
                raise KeyError(f"角色 '{char_name}' 不存在")
            old_audio = project["characters"][key].get("reference_audio")
            stale_audio = resolve_audio_ref_path(
                project_dir,
                refs_audio_dir,
                old_audio if isinstance(old_audio, str) else None,
            )
            self._set_character_reference_audio(project, char_name, "")
            if stale_audio is not None:
                stale.append((key, stale_audio))

        return self.update_project(
            project_name,
            _mutate,
            on_commit=lambda _file: self._trash_stale_reference_audio(project_dir, *stale[0]) if stale else None,
        )

    def get_project_character(self, project_name: str, name: str) -> dict:
        r"""Get a project-level character definition."""
        return self._get_asset("character", project_name, name)

    # ==================== Scene management (scene) ====================

    def update_scene_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        r"""Update the scene sheet path."""
        return self._update_asset_sheet("scene", project_name, name, sheet_path)

    def get_scene(self, project_name: str, name: str) -> dict:
        r"""Get a scene definition."""
        return self._get_asset("scene", project_name, name)

    def get_scene_path(self, project_name: str, filename: str) -> Path:
        r"""Get the scene sheet path."""
        return self._get_asset_path("scene", project_name, filename)

    # ==================== Prop management (prop) ====================

    def update_prop_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        r"""Update the prop sheet path."""
        return self._update_asset_sheet("prop", project_name, name, sheet_path)

    def get_prop(self, project_name: str, name: str) -> dict:
        r"""Get a prop definition."""
        return self._get_asset("prop", project_name, name)

    def get_prop_path(self, project_name: str, filename: str) -> Path:
        r"""Get the prop sheet path."""
        return self._get_asset_path("prop", project_name, filename)

    # ==================== Product management (product) ====================

    def update_product_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        r"""Update the standard product reference-image (product sheet) path."""
        return self._update_asset_sheet("product", project_name, name, sheet_path)

    def get_product(self, project_name: str, name: str) -> dict:
        r"""Get a product definition."""
        return self._get_asset("product", project_name, name)

    def get_product_path(self, project_name: str, filename: str) -> Path:
        r"""Get the product-image path."""
        return self._get_asset_path("product", project_name, filename)

    def add_product_reference_image(self, project_name: str, product_name: str, ref_path: str) -> dict:
        r"""Append an original-image path to a product's reference_images without duplicates.

        Originals anchor product-fidelity acceptance and are append-only here; deletion/reordering uses asset PATCH."""

        def _mutate(project: dict) -> None:
            bucket = project.get("products")
            key = resolve_asset_key(bucket, product_name)
            if not isinstance(bucket, dict) or key is None:
                raise KeyError(f"商品 '{product_name}' 不存在")
            refs = bucket[key].setdefault("reference_images", [])
            if not isinstance(refs, list):
                raise ValueError(
                    f"products['{product_name}'].reference_images 必须是列表，当前为 {type(refs).__name__}"
                )
            if ref_path not in refs:
                refs.append(ref_path)

        return self.update_project(project_name, _mutate)

    # ==================== Direct project-asset write utilities ====================

    @staticmethod
    def _build_asset_entry(asset_type: str, description: str, source: dict | None = None) -> dict:
        r"""Construct a new entry from ASSET_SPECS; see :func:`lib.project.asset_types.build_asset_entry`."""
        return build_asset_entry(asset_type, description, source)

    def add_character(self, project_name: str, name: str, description: str, voice_style: str = "") -> bool:
        r"""Add a character directly to project.json; return False if present in the same type, raise on cross-type conflicts."""
        entry = self._build_asset_entry("character", description, {"voice_style": voice_style})
        return self._add_asset("character", project_name, name, entry)

    def add_project_scene(self, project_name: str, name: str, description: str) -> bool:
        r"""Add a scene directly to project.json; return False if present in the same type, raise on cross-type conflicts."""
        entry = self._build_asset_entry("scene", description)
        return self._add_asset("scene", project_name, name, entry)

    def add_prop(self, project_name: str, name: str, description: str) -> bool:
        r"""Add a prop directly to project.json; return False if present in the same type, raise on cross-type conflicts."""
        entry = self._build_asset_entry("prop", description)
        return self._add_asset("prop", project_name, name, entry)

    def add_product(self, project_name: str, name: str, description: str, brand: str = "") -> bool:
        r"""Add a product directly to project.json; return False if present in the same type, raise on cross-type conflicts."""
        entry = self._build_asset_entry("product", description, {"brand": brand})
        return self._add_asset("product", project_name, name, entry)

    def add_characters_batch(self, project_name: str, characters: dict[str, dict]) -> int:
        r"""Add characters in bulk; skip existing same-type assets, preventing all batch writes on cross-type conflicts."""
        entries = {
            name: self._build_asset_entry("character", data.get("description", ""), data)
            for name, data in characters.items()
        }
        return self._add_assets_batch("character", project_name, entries)

    def add_scenes_batch(self, project_name: str, scenes: dict[str, dict]) -> int:
        r"""Add scenes in bulk; skip existing same-type assets, preventing all batch writes on cross-type conflicts."""
        entries = {
            name: self._build_asset_entry("scene", data.get("description", ""), data) for name, data in scenes.items()
        }
        return self._add_assets_batch("scene", project_name, entries)

    def add_props_batch(self, project_name: str, props: dict[str, dict]) -> int:
        r"""Add props in bulk; skip existing same-type assets, preventing all batch writes on cross-type conflicts."""
        entries = {
            name: self._build_asset_entry("prop", data.get("description", ""), data) for name, data in props.items()
        }
        return self._add_assets_batch("prop", project_name, entries)

    # ==================== Reference-image collection utilities ====================

    def collect_reference_images(self, project_name: str, scene: dict) -> list[Path]:
        r"""Collect all reference images needed by a scene.

        Args:
            project_name: Project name.
            scene: Scene dictionary.

        Returns:
            Reference-image path list.

        Script asset names and bucket keys may use NFC or NFD (registration gates use NFC; no migration needed
        for existing scripts/buckets). Normalize through ``lib.project.asset_types`` comparison coordinates
        before indexing."""
        project = self.load_project(project_name)
        project_dir = self.get_project_path(project_name)
        refs = []

        characters = normalize_asset_bucket(project.get("characters"))
        props = normalize_asset_bucket(project.get("props"))

        # Character reference images
        for char in scene.get("characters_in_scene", []):
            char_data = characters.get(normalize_asset_name(char), {})
            sheet = char_data.get("character_sheet")
            if sheet:
                sheet_path = project_dir / sheet
                if sheet_path.exists():
                    refs.append(sheet_path)

        # Prop reference images
        for prop in scene.get("props_in_scene", []):
            prop_data = props.get(normalize_asset_name(prop), {})
            sheet = prop_data.get("prop_sheet")
            if sheet:
                sheet_path = project_dir / sheet
                if sheet_path.exists():
                    refs.append(sheet_path)

        return refs

    # ==================== Project-overview generation ====================

    def _read_source_files(self, project_name: str, max_chars: int = 50000) -> str:
        r"""Read all UTF-8 text-file contents in the project's source directory.

        Non-UTF-8 files raise SourceDecodeError. Uploads normalize to UTF-8 and startup migrations repaired
        historical projects; remaining non-UTF-8 means users copied files directly, bypassing APIs. Report
        explicitly rather than misleadingly claiming an empty source directory."""
        from lib.script.source_loader.errors import SourceDecodeError

        project_dir = self.get_project_path(project_name)
        source_dir = project_dir / "source"

        if not source_dir.exists():
            return ""

        contents = []
        total_chars = 0
        for file_path in sorted(source_dir.glob("*")):
            if not (file_path.is_file() and file_path.suffix.lower() in SOURCE_TEXT_SUFFIXES):
                continue

            raw = file_path.read_bytes()
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SourceDecodeError(
                    filename=file_path.name,
                    tried_encodings=["utf-8"],
                ) from exc

            remaining = max_chars - total_chars
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[:remaining]
            contents.append(f"--- {file_path.name} ---\n{content}")
            total_chars += len(content)

        return "\n\n".join(contents)

    async def generate_overview(self, project_name: str) -> dict:
        r"""Generate a project overview asynchronously using the Gemini API.

        Args:
            project_name: Project name.

        Returns:
            Generated overview dictionary with synopsis, genre, theme, world_setting, generated_at."""
        from lib.backends.providers import CallPurpose
        from lib.backends.text_backends.base import TextGenerationRequest, TextTaskType
        from lib.backends.text_generator import TextGenerator
        from lib.prompts.prompt_builders_script import build_overview_prompt

        # Read source-file contents
        source_content = await asyncio.to_thread(self._read_source_files, project_name)
        if not source_content:
            raise EmptySourceError("source 目录为空，无法生成概述")

        # Create TextGenerator with automatic usage tracking
        generator = await TextGenerator.create(
            TextTaskType.OVERVIEW, project_name, purpose=CallPurpose.PROJECT_OVERVIEW
        )

        # Call TextGenerator (Structured Outputs). When all sources are screenplays, prefer extraction:
        # use the author's creative-plan preface first, summarizing the body only if absent; mixed sources follow novel rules.
        project_data = await asyncio.to_thread(self.load_project, project_name)
        source_kind = project_overview_source_kind(project_data)
        # source_language from project.json may be corrupt non-string data; non-strings/empty strings use the default language.
        raw_source_language = project_data.get("source_language")
        target_language = (
            raw_source_language if isinstance(raw_source_language, str) and raw_source_language.strip() else "中文"
        )
        prompt = build_overview_prompt(source_content, source_kind=source_kind, target_language=target_language)

        result = await generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview,
            ),
            project_name=project_name,
        )
        response_text = result.text

        # Parse and validate the response
        overview = ProjectOverview.model_validate_json(response_text)
        overview_dict = overview.model_dump()
        overview_dict["generated_at"] = datetime.now(UTC).isoformat()

        # Save project.json with RMW under one _project_lock to avoid overwriting concurrent field changes.
        def _mutate(project: dict) -> None:
            project["overview"] = overview_dict
            project["source_language"] = overview_dict["language"]

        await run_sync_transaction(self.update_project, project_name, _mutate)

        logger.info("项目概述已生成并保存")
        return overview_dict


@functools.cache
def _project_manager_singleton() -> ProjectManager:
    # Construction ensures the project directory exists; no-argument caching runs it once per process.
    return ProjectManager(app_data_dir())


def get_project_manager() -> ProjectManager:
    r"""Return the lazy global ProjectManager singleton for the standard project root."""
    return _project_manager_singleton()


def reset_project_manager_for_tests() -> None:
    r"""Clear the :func:`get_project_manager` singleton; rebuild from the current data root on next call."""
    _project_manager_singleton.cache_clear()
