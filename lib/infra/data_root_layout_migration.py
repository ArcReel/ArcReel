"""数据根布局迁移入口：把旧布局的数据根就地迁到 :class:`DataRootLayout` 描述的当前布局。

启动时在挂文件日志 handler 之后、任何遍历项目的步骤（源文件编码迁移、项目 schema
迁移、会话导入、profile 同步）之前执行一次。步骤按顺序执行，每一步都须能安全重跑；
某一步抛出的异常原样上抛，调用方不得在半迁移的布局上继续遍历项目。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.api_call import ApiCall
from lib.db.models.asset import Asset, AssetDerivative
from lib.infra.data_root_layout import DataRootLayout

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataRootMigrationContext:
    """布局迁移各步骤的共同输入。"""

    layout: DataRootLayout
    session_factory: async_sessionmaker[AsyncSession]
    #: Claude SDK 配置目录（``CLAUDE_CONFIG_DIR`` 或 ``~/.claude``），SDK 本地会话数据在其下。
    sdk_config_dir: Path


MigrationStep = Callable[[DataRootMigrationContext], Awaitable[None]]

#: POSIX 绝对路径、Windows 盘符路径与 UNC 路径的开头。
_ABSOLUTE_PATH_PREFIX = re.compile(r"^(?:[\\/]|[A-Za-z]:[\\/])")


def _project_relative_output_path(stored: str, *, project_name: str, project_dirs: tuple[Path, ...]) -> str | None:
    """把调用记录里的绝对产物路径还原为项目内相对路径；无法确认落在项目内时返回 None。

    先按 ``project_dirs`` 前缀截取；记录写下后数据根挪过位置时前缀对不上，改在路径里找
    项目名路径段，取其后的部分。多处命中时无法确认旧项目根，保持原值。截取结果含
    ``..`` 段时可能越出项目目录，同样保持原值。
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
    """调用记录的产物路径改存项目内相对路径；只处理仍是绝对路径的行，重跑时没有可改的行。"""
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


#: 旧布局的全局资产库目录名；资产记录里的路径以它为前缀。
_LEGACY_GLOBAL_ASSETS_DIRNAME = "_global_assets"


def _merge_dir_into(source: Path, target: Path) -> None:
    """把 ``source`` 下的条目逐个 rename 进 ``target``；目标已有同名文件时保留两边并记告警。"""
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
        source.rmdir()


async def _rename_global_assets_dir(context: DataRootMigrationContext) -> None:
    """全局资产库从旧目录名改到当前位置，资产记录里以旧目录为前缀的路径随之改写。

    目标不存在时整体改名，旧目录是符号链接时改名的是链接本身；目标已存在（例如迁移前已被
    建出空子目录）时逐条并入，旧目录是符号链接则不动。改库只处理仍带旧前缀、且旧位置已
    没有该文件的行：留在旧目录里的文件（同名冲突、未挪动的符号链接）继续按旧路径登记。
    已改写的行不再带旧前缀，重跑时不会被选中。
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


#: 按执行顺序排列的迁移步骤。
_STEPS: tuple[MigrationStep, ...] = (_relativize_call_output_paths, _rename_global_assets_dir)


def default_sdk_config_dir() -> Path:
    """Claude SDK 配置目录：``CLAUDE_CONFIG_DIR`` > ``~/.claude``。"""
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


async def migrate_data_root_layout(
    data_root: Path,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    sdk_config_dir: Path,
) -> None:
    """把 ``data_root`` 迁到当前布局；已是当前布局时什么都不做。"""
    context = DataRootMigrationContext(
        layout=DataRootLayout(data_root),
        session_factory=session_factory,
        sdk_config_dir=sdk_config_dir,
    )
    for step in _STEPS:
        logger.info("数据根布局迁移：执行 %s", getattr(step, "__name__", step))
        await step(context)
