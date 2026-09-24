"""数据根布局迁移入口：把旧布局的数据根就地迁到 :class:`DataRootLayout` 描述的当前布局。

启动时在挂文件日志 handler 之后、任何遍历项目的步骤（源文件编码迁移、项目 schema
迁移、会话导入、profile 同步）之前执行一次。步骤按顺序执行，每一步都须能安全重跑；
某一步抛出的异常原样上抛，调用方不得在半迁移的布局上继续遍历项目。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

#: 按执行顺序排列的迁移步骤。
_STEPS: tuple[MigrationStep, ...] = ()


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
