"""Data-root layout migration entry point: migrate an old data root in place to the current :class:`DataRootLayout` layout.

Run at startup after attaching file log handlers and before any step traversing projects
(source encoding migration, project schema migration, session import or profile sync).
Execute steps in order; each must be safe to rerun. Write a completion marker in ``runtime/``
only after all steps finish; subsequent startups just check it. On failure, log ERROR,
do not write the marker and propagate the original exception. Callers must not continue
traversing projects in a partially migrated layout.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.agent.agent_session_store import AgentSessionEntry, AgentSessionSummary, make_project_key
from lib.db.models.api_call import ApiCall
from lib.db.models.asset import Asset, AssetDerivative
from lib.db.models.credential import ProviderCredential
from lib.infra.data_root_layout import PROJECT_NAME_PATTERN, DataRootLayout, is_project_dir
from lib.project.project_migrations.staged_swap import rollback_project_name, staging_project_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataRootMigrationContext:
    """Shared inputs for all layout migration steps."""

    layout: DataRootLayout
    session_factory: async_sessionmaker[AsyncSession]
    #: Claude SDK configuration directory (``CLAUDE_CONFIG_DIR`` or ``~/.claude``), containing SDK local session data.
    sdk_config_dir: Path


MigrationStep = Callable[[DataRootMigrationContext], Awaitable[None]]

#: Temporary name for a legacy project named ``projects`` before creating the project container; invalid as a project name, so it is not moved as a legacy project.
_PROJECTS_NAMESAKE_STAGING = ".projects-namesake-migrating"


def _move_entry(source: Path, target: Path) -> None:
    """Move ``source`` to ``target`` within one filesystem; if the target exists, raise ``FileExistsError`` and leave both untouched.

    Moving a relative symlink one level deeper would change its target. Instead create
    an absolute link to the original target at the new location, then delete the old link.
    If the new link exists and the old link remains (a previous run stopped between these steps), delete only the old link.
    """
    rebased_link = None
    if source.is_symlink() and not Path(os.readlink(source)).is_absolute():
        rebased_link = os.path.normpath(source.parent / os.readlink(source))
    if target.is_symlink() and rebased_link is not None and os.readlink(target) == rebased_link:
        source.unlink()  # hard-delete: infra -- data-root migration: old symlink after retargeting
        return
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"数据根布局迁移：{target} 已存在，无法把 {source} 挪过去，请人工处理后重启")
    if rebased_link is not None:
        target.symlink_to(rebased_link, target_is_directory=True)
        source.unlink()  # hard-delete: infra -- data-root migration: old symlink after retargeting
        return
    source.rename(target)


def _is_project_named_dir(entry: Path) -> bool:
    """Directory whose name satisfies project-name rules, whether or not it has ``project.json``."""
    return bool(PROJECT_NAME_PATTERN.fullmatch(entry.name)) and entry.is_dir()


def _is_project_swap_dir(entry: Path) -> bool:
    """Rollback / staging directories left by project schema migration swaps; must share the project parent directory for cleanup or manual restoration."""
    name = entry.name
    return (
        (rollback_project_name(name) is not None or staging_project_name(name) is not None)
        and entry.is_dir()
        and not entry.is_symlink()
    )


def _projects_to_move(layout: DataRootLayout) -> list[Path]:
    """Legacy entries under the data root to move into the project container.

    Move all validly named directories: those with ``project.json`` are projects; move
    others unchanged to preserve data. Only names matching registered top-level layout
    entries stay in place. Move project-swap intermediate directories with projects.
    Exclude the project container itself and other invalid names containing underscores, dots, etc.
    """
    system_names = {entry.name for entry in layout.top_level_entries}
    return [
        entry
        for entry in sorted(layout.root.iterdir())
        if entry != layout.projects_dir
        and (
            (_is_project_named_dir(entry) and (entry.name not in system_names or is_project_dir(entry)))
            or _is_project_swap_dir(entry)
        )
    ]


async def _move_projects_into_projects_dir(context: DataRootMigrationContext) -> None:
    """Move legacy projects stored directly under the data root into ``projects/``.

    If a legacy project is named ``projects``, first give it the temporary name, create
    the container, then move it to ``projects/projects/`` without changing its project name.
    A remaining temporary name indicates a previous run stopped between these steps; finish it now.
    """
    layout = context.layout
    projects_dir = layout.projects_dir
    staging = layout.root / _PROJECTS_NAMESAKE_STAGING
    if is_project_dir(projects_dir):
        await asyncio.to_thread(_move_entry, projects_dir, staging)
    await asyncio.to_thread(projects_dir.mkdir, exist_ok=True)
    moved: list[str] = []
    if staging.exists() or staging.is_symlink():
        await asyncio.to_thread(_move_entry, staging, projects_dir / projects_dir.name)
        moved.append(projects_dir.name)
    for entry in await asyncio.to_thread(_projects_to_move, layout):
        await asyncio.to_thread(_move_entry, entry, projects_dir / entry.name)
        moved.append(entry.name)
    if moved:
        logger.info("数据根布局迁移：%d 个目录移入 %s：%s", len(moved), projects_dir, ", ".join(moved))


def _rename_sdk_session_dir(sdk_projects_dir: Path, legacy_key: str, current_key: str) -> None:
    """Rename SDK local session directories to their new keys; best effort, warning only on failure."""
    legacy_dir = sdk_projects_dir / legacy_key
    target = sdk_projects_dir / current_key
    try:
        if not legacy_dir.is_dir():
            return
        if target.exists():
            logger.warning("数据根布局迁移：SDK 会话目录 %s 已存在，%s 保持原样", target, legacy_dir)
            return
        legacy_dir.rename(target)
    except OSError:
        logger.warning("数据根布局迁移：SDK 会话目录 %s 改名失败", legacy_dir, exc_info=True)


def _moved_project_names(projects_dir: Path) -> set[str]:
    """Project names under ``projects/``: valid directory names plus projects owning swap-intermediate directories (which become those projects after manual restoration)."""
    names: set[str] = set()
    for entry in projects_dir.iterdir():
        if _is_project_named_dir(entry):
            names.add(entry.name)
        elif _is_project_swap_dir(entry):
            names.add(rollback_project_name(entry.name) or staging_project_name(entry.name) or "")
    names.discard("")
    return names


async def _rewrite_session_store_keys(context: DataRootMigrationContext) -> None:
    """After moving projects into ``projects/``, rewrite Agent session storage keys from old to new project-directory-derived keys.

    Session storage keys derive from absolute project paths (ADR 0029). Rewrite only
    records still under old keys; if the same session already exists under the new key,
    preserve both and warn. Rename the SDK's own local session directories as well
    (the only session source with ``ARCREEL_SDK_SESSION_STORE=off``), warning only on failure.
    """
    layout = context.layout
    project_names = sorted(await asyncio.to_thread(_moved_project_names, layout.projects_dir))
    key_pairs = [
        (make_project_key(layout.root / name), make_project_key(layout.projects_dir / name)) for name in project_names
    ]
    key_pairs = [(legacy, current) for legacy, current in key_pairs if legacy != current]
    rewritten: set[tuple[str, str]] = set()
    async with context.session_factory() as session:
        for legacy_key, current_key in key_pairs:
            for table in (AgentSessionEntry, AgentSessionSummary):
                legacy_ids = set(
                    (await session.scalars(select(table.session_id).where(table.project_key == legacy_key))).all()
                )
                if not legacy_ids:
                    continue
                conflicting = set(
                    (
                        await session.scalars(
                            select(table.session_id).where(
                                table.project_key == current_key, table.session_id.in_(legacy_ids)
                            )
                        )
                    ).all()
                )
                if conflicting:
                    logger.warning(
                        "数据根布局迁移：会话 %s 在新旧存储键下都有记录，旧键 %s 下的记录保持原样",
                        sorted(conflicting),
                        legacy_key,
                    )
                await session.execute(
                    update(table)
                    .where(table.project_key == legacy_key, table.session_id.not_in(conflicting))
                    .values(project_key=current_key)
                )
                rewritten.update((current_key, session_id) for session_id in legacy_ids - conflicting)
        await session.commit()
    sdk_projects_dir = context.sdk_config_dir / "projects"
    for legacy_key, current_key in key_pairs:
        await asyncio.to_thread(_rename_sdk_session_dir, sdk_projects_dir, legacy_key, current_key)
    if rewritten:
        logger.info("数据根布局迁移：%d 个 Agent 会话改用新项目目录的存储键", len(rewritten))


#: Prefixes of POSIX absolute paths, Windows drive paths and UNC paths.
_ABSOLUTE_PATH_PREFIX = re.compile(r"^(?:[\\/]|[A-Za-z]:[\\/])")


def _project_relative_output_path(stored: str, *, project_name: str, project_dirs: tuple[Path, ...]) -> str | None:
    """Recover a project-relative artifact path from an absolute call-record path; return None when containment cannot be established.

    First strip a ``project_dirs`` prefix. If the data root moved after the record was
    written and prefixes no longer match, find a project-name path segment and take
    its suffix. Multiple matches make the old root ambiguous, so retain the old value.
    Also retain it if the suffix includes ``..`` segments that could escape the project.
    """
    relative = _relative_to_any(stored, project_dirs)
    if relative is None:
        segments = [segment for segment in re.split(r"[\\/]", stored) if segment]
        candidates = [
            "/".join(segments[index + 1 :]) for index, segment in enumerate(segments[:-1]) if segment == project_name
        ]
        relative = candidates[0] if len(candidates) == 1 else None
    return relative if relative is not None and ".." not in Path(relative).parts else None


def _relative_to_any(stored: str, project_dirs: tuple[Path, ...]) -> str | None:
    for project_dir in project_dirs:
        try:
            return Path(stored).relative_to(project_dir).as_posix()
        except ValueError:
            continue
    return None


async def _relativize_call_output_paths(context: DataRootMigrationContext) -> None:
    """Store call-record artifact paths relative to the project; process only still-absolute paths, leaving nothing to change on rerun."""
    layout = context.layout
    async with context.session_factory() as session:
        rows = (
            await session.execute(
                select(ApiCall.id, ApiCall.project_name, ApiCall.output_path).where(
                    or_(
                        ApiCall.output_path.startswith("/"),
                        ApiCall.output_path.startswith("\\", autoescape=True),
                        ApiCall.output_path.like("_:%"),
                    )
                )
            )
        ).all()
        rewritten = 0
        for call_id, project_name, stored in rows:
            if not project_name or not _ABSOLUTE_PATH_PREFIX.match(stored):
                continue
            relative = _project_relative_output_path(
                stored,
                project_name=project_name,
                project_dirs=(layout.projects_dir / project_name, layout.root / project_name),
            )
            if relative is None:
                logger.warning("调用记录 %s 的产物路径无法还原为项目内路径，保持原值：%s", call_id, stored)
                continue
            await session.execute(update(ApiCall).where(ApiCall.id == call_id).values(output_path=relative))
            rewritten += 1
        await session.commit()
    if rewritten:
        logger.info("数据根布局迁移：%d 条调用记录的产物路径改为项目内相对路径", rewritten)


#: Legacy global asset-library directory name; asset-record paths use this prefix.
_LEGACY_GLOBAL_ASSETS_DIRNAME = "_global_assets"


def _merge_dir_into(source: Path, target: Path) -> None:
    """Rename entries from ``source`` into ``target`` one by one; preserve both and warn on destination name collisions."""
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        destination = target / entry.name
        if not destination.exists() and not destination.is_symlink():
            entry.rename(destination)
        elif entry.is_dir() and not entry.is_symlink() and destination.is_dir():
            _merge_dir_into(entry, destination)
        else:
            logger.warning("数据根布局迁移：%s 已存在，保留旧位置的 %s", destination, entry)
    if not any(source.iterdir()):
        source.rmdir()  # hard-delete: infra -- data-root migration: empty directory after merging


async def _rename_global_assets_dir(context: DataRootMigrationContext) -> None:
    """Move the global asset library from its old directory name to the current location and rewrite asset-record paths carrying the old prefix.

    If the destination is absent, rename the directory as a whole; for a symlink, rename
    the link itself. If the destination exists (for example, an empty subdirectory created
    before migration), merge entries individually, but leave a symlink source untouched.
    Update only rows still using the old prefix whose files no longer exist at the old
    location. Files left there (name collisions or unmoved symlinks) retain their old paths.
    Rewritten rows lose the old prefix and are not selected on rerun.
    """
    layout = context.layout
    legacy_dir = layout.root / _LEGACY_GLOBAL_ASSETS_DIRNAME
    target_dir = layout.global_assets_dir
    if legacy_dir.is_dir() or legacy_dir.is_symlink():
        if not target_dir.exists() and not target_dir.is_symlink():
            legacy_dir.rename(target_dir)
        elif legacy_dir.is_symlink():
            logger.warning("数据根布局迁移：%s 已存在，符号链接 %s 保持原样", target_dir, legacy_dir)
        else:
            _merge_dir_into(legacy_dir, target_dir)

    legacy_prefix = f"{_LEGACY_GLOBAL_ASSETS_DIRNAME}/"
    current_prefix = f"{target_dir.relative_to(layout.root).as_posix()}/"
    rewritten = 0
    async with context.session_factory() as session:
        for table, column in (
            (Asset, Asset.image_path),
            (Asset, Asset.audio_path),
            (AssetDerivative, AssetDerivative.image_path),
        ):
            rows = (
                await session.execute(select(table.id, column).where(column.startswith(legacy_prefix, autoescape=True)))
            ).all()
            for row_id, stored in rows:
                legacy_file = layout.root / stored
                if legacy_file.exists() or legacy_file.is_symlink():
                    continue
                await session.execute(
                    update(table)
                    .where(table.id == row_id)
                    .values({column.key: current_prefix + stored.removeprefix(legacy_prefix)})
                )
                rewritten += 1
        await session.commit()
    if rewritten:
        logger.info("数据根布局迁移：%d 处全局资产路径改到 %s", rewritten, current_prefix)


#: Legacy internal directory relative to the data root; its ``users/`` subdirectory contains user data.
_LEGACY_INTERNAL_DIR = ".arcreel"
#: Legacy runtime state stored directly under the data root (paths relative to that root).
_LEGACY_SESSION_IMPORT_MARKER = ".session_store_migration_done"
_LEGACY_PROJECT_MIGRATION_ERROR_LOG = "_migration_errors.log"
_LEGACY_GENERATION_ADMISSION_LOCKS_DIR = ".generation-admission-locks"


async def _move_user_data_to_users_dir(context: DataRootMigrationContext) -> None:
    """Move ``.arcreel/users/`` to ``users/``; do nothing if the old directory is absent."""
    layout = context.layout
    legacy_users = layout.root / _LEGACY_INTERNAL_DIR / "users"
    if not legacy_users.is_dir():
        return
    _merge_dir_into(legacy_users, layout.users_dir)
    with contextlib.suppress(OSError):
        legacy_users.parent.rmdir()  # hard-delete: infra -- data-root migration: empty .arcreel/ after moving its contents
    logger.info("数据根布局迁移：用户数据移入 %s", layout.users_dir)


async def _move_runtime_state_to_runtime_dir(context: DataRootMigrationContext) -> None:
    """Collect runtime state stored directly under the data root into ``runtime/``; inherit completion markers from the old location.

    Generation admission locks matter only while held, so delete the legacy lock directory directly.
    """
    layout = context.layout
    root = layout.root
    legacy_marker = root / _LEGACY_SESSION_IMPORT_MARKER
    legacy_error_log = root / _LEGACY_PROJECT_MIGRATION_ERROR_LOG
    legacy_locks = root / _LEGACY_GENERATION_ADMISSION_LOCKS_DIR
    if not (legacy_marker.exists() or legacy_error_log.exists() or legacy_locks.exists()):
        return
    layout.runtime_dir.mkdir(exist_ok=True)
    if legacy_marker.exists():
        if layout.session_import_marker_path.exists():
            legacy_marker.unlink()  # hard-delete: infra -- data-root migration: duplicate session-import marker
        else:
            legacy_marker.rename(layout.session_import_marker_path)
    if legacy_error_log.exists():
        if layout.project_migration_error_log_path.exists():
            with layout.project_migration_error_log_path.open("a", encoding="utf-8") as merged:
                merged.write(legacy_error_log.read_text(encoding="utf-8", errors="replace"))
            legacy_error_log.unlink()  # hard-delete: infra -- data-root migration: merged legacy migration-error log
        else:
            legacy_error_log.rename(layout.project_migration_error_log_path)
    shutil.rmtree(legacy_locks, ignore_errors=True)  # hard-delete: infra -- migrated legacy admission locks
    logger.info("数据根布局迁移：运行时状态收进 %s", layout.runtime_dir)


def _copy_into_place(source: Path, dest: Path) -> None:
    """Copy to a temporary name, then replace ``dest``: the old location may be on another volume, and a crash must not leave a partial destination."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_name(f"{dest.name}.migrating")
    shutil.copy2(source, staging)
    os.replace(staging, dest)


def _is_same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


def _discard_legacy_file(source: Path) -> None:
    """Delete old files already copied into the data root; if a read-only legacy volume prevents deletion, leave them in place without blocking migration."""
    try:
        source.unlink(missing_ok=True)  # hard-delete: infra -- legacy credential copied to data root
    except OSError:
        logger.warning("Vertex 凭证旧文件删除失败，保留在原位置：%s", source, exc_info=True)


def _place_recorded_vertex_credentials(
    layout: DataRootLayout, legacy_dir: Path, rows: list[tuple[int, str]]
) -> list[tuple[int, str]]:
    """Place files referenced by credential records at ID-derived locations; return ``(id, 记录路径)`` for files in place.

    Use the recorded path first, then the same filename in the old credential directory
    if absent (for recorded container paths or other locations invisible to this process).
    Do not copy if the derived destination already exists. Delete sources inside the old
    credential directory only after processing all records, so records sharing one file
    each receive a copy. Sources outside that directory are user-managed: copy, never delete.
    Omit records missing files in both locations and retain their values. Reruns before
    the completion marker search again; later credential reads fall back to the recorded path.
    """
    legacy_resolved = legacy_dir.resolve()
    placed: list[tuple[int, str]] = []
    consumed: dict[Path, None] = {}
    for cred_id, recorded in rows:
        dest = layout.vertex_credential_path(cred_id)
        recorded_path = Path(recorded)
        source = next(
            (
                candidate
                for candidate in (recorded_path, legacy_dir / recorded_path.name)
                if candidate.is_file() and not _is_same_file(candidate, dest)
            ),
            None,
        )
        if not dest.is_file():
            if source is None:
                logger.warning("Vertex 凭证 %s 的文件找不到，保持记录路径：%s", cred_id, recorded)
                continue
            _copy_into_place(source, dest)
        if source is not None and source.resolve().is_relative_to(legacy_resolved):
            consumed[source] = None
        placed.append((cred_id, recorded))
    for source in consumed:
        _discard_legacy_file(source)
    return placed


def _move_legacy_vertex_keys(legacy_dir: Path, keys_dir: Path) -> int:
    """Move remaining files from the old credential directory into the data root under their original names; preserve the old file on name collisions."""
    if legacy_dir == keys_dir or not legacy_dir.is_dir():
        return 0
    moved = 0
    for source in sorted(legacy_dir.glob("*.json")):
        if not source.is_file():
            continue
        target = keys_dir / source.name
        if target.exists():
            logger.warning("数据根里已有同名 Vertex 凭证文件，保留旧位置的文件：%s", source)
            continue
        _copy_into_place(source, target)
        _discard_legacy_file(source)
        moved += 1
    return moved


async def _move_vertex_credentials_into_data_root(context: DataRootMigrationContext) -> None:
    """Move Vertex credential files from the data-root parent (a separate volume in Docker deployments) into the data root and clear credential-record path columns.

    Process only records with populated path columns and files remaining in the old directory; reruns have nothing to do.
    """
    layout = context.layout
    legacy_dir = layout.root.parent / "vertex_keys"
    async with context.session_factory() as session:
        rows = [
            (cred_id, recorded)
            for cred_id, recorded in (
                await session.execute(
                    select(ProviderCredential.id, ProviderCredential.credentials_path).where(
                        ProviderCredential.provider == "gemini-vertex",
                        ProviderCredential.credentials_path.is_not(None),
                        ProviderCredential.credentials_path != "",
                    )
                )
            ).all()
            if recorded
        ]
        placed = await asyncio.to_thread(_place_recorded_vertex_credentials, layout, legacy_dir, rows)
        for cred_id, recorded in placed:
            await session.execute(
                update(ProviderCredential)
                .where(ProviderCredential.id == cred_id, ProviderCredential.credentials_path == recorded)
                .values(credentials_path=None)
            )
        await session.commit()
    moved = await asyncio.to_thread(_move_legacy_vertex_keys, legacy_dir, layout.vertex_keys_dir)
    if placed or moved:
        logger.info("数据根布局迁移：%d 条 Vertex 凭证记录、%d 个旧目录凭证文件移入数据根", len(placed), moved)


#: Migration steps in execution order.
_STEPS: tuple[MigrationStep, ...] = (
    # Move projects before system entries: users, runtime, etc. are valid project names, so move
    # legacy projects with those names first to make room for subsequent steps in this migration.
    _move_projects_into_projects_dir,
    _rewrite_session_store_keys,
    _relativize_call_output_paths,
    _rename_global_assets_dir,
    _move_user_data_to_users_dir,
    _move_runtime_state_to_runtime_dir,
    _move_vertex_credentials_into_data_root,
)


async def migrate_data_root_layout(
    data_root: Path,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    sdk_config_dir: Path,
) -> None:
    """Migrate ``data_root`` to the current layout; do nothing if the completion marker exists."""
    layout = DataRootLayout(data_root)
    marker = layout.layout_migration_marker_path
    if await asyncio.to_thread(marker.is_file):
        return
    context = DataRootMigrationContext(layout=layout, session_factory=session_factory, sdk_config_dir=sdk_config_dir)
    for step in _STEPS:
        name = getattr(step, "__name__", repr(step))
        logger.info("数据根布局迁移：执行 %s", name)
        try:
            await step(context)
        except Exception:
            logger.exception("数据根布局迁移在 %s 失败，未写完成标记，下次启动从头续跑", name)
            raise
    await asyncio.to_thread(_write_marker, marker)
    logger.info("数据根布局迁移完成：%s", marker)


def _write_marker(marker: Path) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(UTC).isoformat() + "\n", encoding="utf-8")
