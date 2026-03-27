"""统一运行时配置解析器。

将散落在多个文件中的配置读取和默认值定义集中到一处。
每次调用从 DB 读取，不缓存（本地 SQLite 开销可忽略）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService, _DEFAULT_TEXT_BACKEND
from lib.config.registry import PROVIDER_REGISTRY
from lib.db.repositories.credential_repository import CredentialRepository
from lib.project_manager import ProjectManager
from lib.env_init import PROJECT_ROOT
from lib.text_backends.base import TextTaskType


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


_TEXT_TASK_SETTING_KEYS: dict[TextTaskType, str] = {
    TextTaskType.SCRIPT: "text_backend_script",
    TextTaskType.OVERVIEW: "text_backend_overview",
    TextTaskType.STYLE_ANALYSIS: "text_backend_style",
}


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
            return await self._resolve_provider_config(svc, session, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """批量获取所有供应商配置。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_all_provider_configs(svc, session)

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

    async def _resolve_provider_config(
        self, svc: ConfigService, session: AsyncSession, provider_id: str,
    ) -> dict[str, str]:
        config = await svc.get_provider_config(provider_id)
        cred_repo = CredentialRepository(session)
        active = await cred_repo.get_active(provider_id)
        if active:
            if active.api_key:
                config["api_key"] = active.api_key
            if active.credentials_path:
                config["credentials_path"] = active.credentials_path
            if active.base_url:
                config["base_url"] = active.base_url
        return config

    async def _resolve_all_provider_configs(
        self, svc: ConfigService, session: AsyncSession,
    ) -> dict[str, dict[str, str]]:
        configs = await svc.get_all_provider_configs()
        cred_repo = CredentialRepository(session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        for provider_id, cred in active_creds.items():
            cfg = configs.setdefault(provider_id, {})
            if cred.api_key:
                cfg["api_key"] = cred.api_key
            if cred.credentials_path:
                cfg["credentials_path"] = cred.credentials_path
            if cred.base_url:
                cfg["base_url"] = cred.base_url
        return configs

    async def default_text_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_default_text_backend()

    async def text_backend_for_task(
        self, task_type: TextTaskType, project_name: str | None = None,
    ) -> tuple[str, str]:
        """解析文本 backend。优先级：项目级任务配置 → 全局任务配置 → 全局默认 → 自动推断"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_text_backend(svc, task_type, project_name)

    async def _resolve_text_backend(
        self, svc: ConfigService, task_type: TextTaskType, project_name: str | None,
    ) -> tuple[str, str]:
        setting_key = _TEXT_TASK_SETTING_KEYS[task_type]

        # 1. Project-level task override
        if project_name:
            project = get_project_manager().load_project(project_name)
            project_val = project.get(setting_key)
            if project_val and "/" in str(project_val):
                return ConfigService._parse_backend(str(project_val), _DEFAULT_TEXT_BACKEND)

        # 2. Global task-type setting
        task_val = await svc.get_setting(setting_key, "")
        if task_val and "/" in task_val:
            return ConfigService._parse_backend(task_val, _DEFAULT_TEXT_BACKEND)

        # 3. Global default text backend
        default_val = await svc.get_setting("default_text_backend", "")
        if default_val and "/" in default_val:
            return ConfigService._parse_backend(default_val, _DEFAULT_TEXT_BACKEND)

        # 4. Auto-resolve
        return await self._auto_resolve_backend(svc, "text")

    async def _auto_resolve_backend(
        self, svc: ConfigService, media_type: str,
    ) -> tuple[str, str]:
        """遍历 PROVIDER_REGISTRY（按注册顺序），找到第一个 ready 且支持该 media_type 的供应商。"""
        statuses = await svc.get_all_providers_status()
        ready = {s.name for s in statuses if s.status == "ready"}

        for provider_id, meta in PROVIDER_REGISTRY.items():
            if provider_id not in ready:
                continue
            for model_id, model_info in meta.models.items():
                if model_info.media_type == media_type and model_info.default:
                    return provider_id, model_id

        raise ValueError(
            f"未找到可用的 {media_type} 供应商。"
            "请在「全局设置 → 供应商」页面配置至少一个供应商。"
        )
