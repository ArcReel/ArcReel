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
    """把调用记录里的绝对产物路径还原为项目内相对路径；找不到项目名路径段时返回 None。

    先按 ``project_dirs`` 前缀截取；记录写下后数据根挪过位置时前缀对不上，改在路径里找
    项目名路径段，取其后的部分。多处命中时无法确认旧项目根，保持原值。
    """
    for project_dir in project_dirs:
        try:
            return Path(stored).relative_to(project_dir).as_posix()
        except ValueError:
            continue
    segments = [segment for segment in re.split(r"[\\/]", stored) if segment]
    candidates = [
        "/".join(segments[index + 1 :]) for index, segment in enumerate(segments[:-1]) if segment == project_name
    ]
    return candidates[0] if len(candidates) == 1 and ".." not in Path(candidates[0]).parts else None


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


#: 按执行顺序排列的迁移步骤。
_STEPS: tuple[MigrationStep, ...] = (_relativize_call_output_paths,)


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
