"""统一运行时配置解析器。

将散落在多个文件中的配置读取和默认值定义集中到一处。
每次调用从 DB 读取，不缓存（本地 SQLite 开销可忽略）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.config.service import ConfigService
from lib.project_manager import ProjectManager
from lib.env_init import PROJECT_ROOT


_project_manager: ProjectManager | None = None


def get_project_manager() -> ProjectManager:
    """返回共享的 ProjectManager 单例（使用标准项目根目录）。"""
    global _project_manager
    if _project_manager is None:
        _project_manager = ProjectManager(PROJECT_ROOT / "projects")
    return _project_manager

logger = logging.getLogger(__name__)

# 布尔字符串解析的 truthy 值集合
_TRUTHY = frozenset({"true", "1", "yes"})


def _parse_bool(raw: str) -> bool:
    """将配置字符串解析为布尔值。"""
    return raw.strip().lower() in _TRUTHY


class ConfigResolver:
    """运行时配置解析器。

    作为 ConfigService 的上层薄封装，提供：
    - 唯一的默认值定义点
    - 类型化输出（bool / tuple / dict）
    - 内置优先级解析（全局配置 → 项目级覆盖）
    """

    # ── 唯一的默认值定义点 ──
    _DEFAULT_VIDEO_GENERATE_AUDIO = False

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ── 公开 API：每次调用打开新 session ──

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """解析 video_generate_audio。

        优先级：项目级覆盖 > 全局配置 > 默认值(False)。
        """
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_video_generate_audio(svc, project_name)

    async def default_video_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_default_video_backend(svc)

    async def default_image_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_default_image_backend(svc)

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """获取单个供应商配置。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_provider_config(svc, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """批量获取所有供应商配置。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_all_provider_configs(svc)

    # ── 内部解析方法（可独立测试，接收已创建的 svc） ──

    async def _resolve_video_generate_audio(
        self, svc: ConfigService, project_name: str | None,
    ) -> bool:
        raw = await svc.get_setting("video_generate_audio", "")
        value = _parse_bool(raw) if raw else self._DEFAULT_VIDEO_GENERATE_AUDIO

        if project_name:
            project = get_project_manager().load_project(project_name)
            override = project.get("video_generate_audio")
            if override is not None:
                if isinstance(override, str):
                    value = _parse_bool(override)
                else:
                    value = bool(override)

        return value

    async def _resolve_default_video_backend(self, svc: ConfigService) -> tuple[str, str]:
        return await svc.get_default_video_backend()

    async def _resolve_default_image_backend(self, svc: ConfigService) -> tuple[str, str]:
        return await svc.get_default_image_backend()

    async def _resolve_provider_config(self, svc: ConfigService, provider_id: str) -> dict[str, str]:
        return await svc.get_provider_config(provider_id)

    async def _resolve_all_provider_configs(self, svc: ConfigService) -> dict[str, dict[str, str]]:
        return await svc.get_all_provider_configs()
