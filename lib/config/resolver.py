"""统一运行时配置解析器。

将散落在多个文件中的配置读取和默认值定义集中到一处。
每次调用从 DB 读取，不缓存（本地 SQLite 开销可忽略）。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import async_sessionmaker

from sqlalchemy.ext.asyncio import AsyncSession

from lib.app_data_dir import app_data_dir
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import (
    _DEFAULT_IMAGE_BACKEND,
    _DEFAULT_TEXT_BACKEND,
    _DEFAULT_VIDEO_BACKEND,
    ConfigService,
)
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.project_manager import ProjectManager
from lib.reference_video.limits import DEFAULT_MAX_REFS, PROVIDER_MAX_REFS, normalize_provider_id
from lib.text_backends.base import TextTaskType

_project_manager: ProjectManager | None = None


def get_project_manager() -> ProjectManager:
    """返回共享的 ProjectManager 单例（使用标准项目根目录）。"""
    global _project_manager
    if _project_manager is None:
        _project_manager = ProjectManager(app_data_dir())
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
    # 与 Seedance / Grok 默认开启、storyboard 用户期望一致。
    # server/routers/system_config.py 与 lib/media_generator.py 均通过引用此常量读取。
    _DEFAULT_VIDEO_GENERATE_AUDIO = True

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        _bound_session: AsyncSession | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bound_session = _bound_session

    # ── Session 管理 ──

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ConfigResolver]:
        """打开共享 session，返回绑定到该 session 的 ConfigResolver。"""
        if self._bound_session is not None:
            yield self
        else:
            async with self._session_factory() as sess:
                yield ConfigResolver(self._session_factory, _bound_session=sess)

    @asynccontextmanager
    async def _open_session(self) -> AsyncIterator[tuple[AsyncSession, ConfigService]]:
        """获取 (session, ConfigService)，优先复用 bound session。"""
        if self._bound_session is not None:
            yield self._bound_session, ConfigService(self._bound_session)
        else:
            async with self._session_factory() as session:
                yield session, ConfigService(session)

    # ── 公开 API ──

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """解析 video_generate_audio。

        优先级：项目级覆盖 > 全局配置 > 默认值(True)。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_generate_audio(svc, project_name)

    async def default_video_backend(self) -> tuple[str, str]:
        """返回系统级默认 (provider_id, model_id)（不含项目级覆盖）。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_video_backend(svc, session)

    async def video_backend(self, project_name: str | None = None) -> tuple[str, str]:
        """解析当前项目应使用的视频 (provider_id, model_id)。

        优先级：项目级 `project.json.video_backend` > 系统设置 `default_video_backend` >
        系统默认 `_DEFAULT_VIDEO_BACKEND` > auto-resolve（按 registry 顺序挑第一个 ready）。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_backend(svc, session, project_name)

    async def video_capabilities(self, project_name: str | None = None) -> dict:
        """解析当前项目视频 model 的综合能力 + 用户项目偏好。

        Returns:
            {
              "provider_id": str,
              "model": str,
              "supported_durations": list[int],    # 来自 model (单一真相源)
              "max_duration": int,                 # max(supported_durations) 派生
              "max_reference_images": int,         # 按归一化 provider 查 PROVIDER_MAX_REFS，缺省 DEFAULT_MAX_REFS
              "source": "registry" | "custom",
              "default_duration": int | None,      # 用户在 project.json 里设置的偏好
              "content_mode": str | None,
              "generation_mode": str | None,
            }

        Raises:
            ValueError: 当 video_backend 解析失败 / model 找不到 / supported_durations 为空。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_capabilities(svc, session, project_name)

    async def video_capabilities_for_project(self, project: dict) -> dict:
        """同 `video_capabilities`，但使用调用方已加载的 project dict。

        优先用此变体，可避免按名称二次加载、也不依赖 `PROJECT_ROOT/projects/<name>` 目录结构
        （例如 `ScriptGenerator` 在非标准路径实例化、或测试用 tmp_path 时，防止目录名
        与全局项目碰撞读到错误能力）。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_capabilities_from_project(svc, session, project)

    async def default_image_backend_t2i(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)，T2I 默认。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_image_backend(svc, session, "t2i")

    async def default_image_backend_i2i(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)，I2I 默认。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_image_backend(svc, session, "i2i")

    async def default_image_backend(self) -> tuple[str, str]:
        """兼容 shim：旧调用方仍可调；返回 T2I 变体。"""
        return await self.default_image_backend_t2i()

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """获取单个供应商配置。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_provider_config(svc, session, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """批量获取所有供应商配置。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_all_provider_configs(svc, session)

    # ── 内部解析方法（可独立测试，接收已创建的 svc） ──

    async def _resolve_video_generate_audio(
        self,
        svc: ConfigService,
        project_name: str | None,
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

    async def _resolve_default_video_backend(self, svc: ConfigService, session: AsyncSession) -> tuple[str, str]:
        raw = await svc.get_setting("default_video_backend", "")
        if raw and "/" in raw:
            return ConfigService._parse_backend(raw, _DEFAULT_VIDEO_BACKEND)
        return await self._auto_resolve_backend(svc, session, "video")

    async def _resolve_video_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project_name: str | None,
    ) -> tuple[str, str]:
        """三级解析当前项目应使用的 video backend。

        模式对齐 `_resolve_text_backend`：项目级 > 系统设置 > 系统默认 / auto。
        """
        project = get_project_manager().load_project(project_name) if project_name else None
        return await self._resolve_video_backend_from_project(svc, session, project)

    async def _resolve_video_backend_from_project(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
    ) -> tuple[str, str]:
        if project is not None:
            project_val = project.get("video_backend")
            if project_val and isinstance(project_val, str) and "/" in project_val:
                return ConfigService._parse_backend(project_val, _DEFAULT_VIDEO_BACKEND)
        return await self._resolve_default_video_backend(svc, session)

    async def _resolve_video_capabilities(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project_name: str | None,
    ) -> dict:
        """按两步解析：先选 model，再读 model 能力。"""
        project = get_project_manager().load_project(project_name) if project_name else None
        return await self._resolve_video_capabilities_from_project(svc, session, project)

    async def _resolve_video_capabilities_from_project(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
    ) -> dict:
        provider_id, model_id = await self._resolve_video_backend_from_project(svc, session, project)

        if is_custom_provider(provider_id):
            source = "custom"
            try:
                db_pid = parse_provider_id(provider_id)
            except ValueError as exc:
                raise ValueError(f"invalid custom provider_id: {provider_id}") from exc
            repo = CustomProviderRepository(session)
            model = await repo.get_model_by_ids(db_pid, model_id)
            if model is None:
                raise ValueError(f"custom model not found: {provider_id}/{model_id}")

            from lib.custom_provider.endpoints import endpoint_to_media_type

            derived_media = endpoint_to_media_type(model.endpoint)
            if derived_media != "video":
                raise ValueError(
                    f"endpoint media_type mismatch: {provider_id}/{model_id} endpoint={model.endpoint!r} "
                    f"is {derived_media}, not video"
                )
            raw_durations = model.supported_durations
            supported_durations: list[int] = []
            if raw_durations:
                try:
                    parsed = json.loads(raw_durations)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid supported_durations JSON on custom model {provider_id}/{model_id}"
                    ) from exc
                if isinstance(parsed, list):
                    supported_durations = [int(d) for d in parsed]
        else:
            source = "registry"
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta is None:
                raise ValueError(f"provider not in PROVIDER_REGISTRY: {provider_id}")
            model_info = provider_meta.models.get(model_id)
            if model_info is None:
                raise ValueError(f"model not found in registry: {provider_id}/{model_id}")
            supported_durations = list(model_info.supported_durations or [])

        if not supported_durations:
            raise ValueError(f"supported_durations is empty for {provider_id}/{model_id}; cannot derive capabilities")

        max_duration = max(supported_durations)
        normalized_provider = normalize_provider_id(provider_id)
        max_reference_images = PROVIDER_MAX_REFS.get(normalized_provider, DEFAULT_MAX_REFS)

        default_duration: int | None = None
        content_mode: str | None = None
        generation_mode: str | None = None
        if project is not None:
            raw_default = project.get("default_duration")
            if isinstance(raw_default, int):
                default_duration = raw_default
            elif isinstance(raw_default, str) and raw_default.strip().isdigit():
                default_duration = int(raw_default.strip())
            cm = project.get("content_mode")
            if isinstance(cm, str) and cm:
                content_mode = cm
            gm = project.get("generation_mode")
            if isinstance(gm, str) and gm:
                generation_mode = gm

        return {
            "provider_id": provider_id,
            "model": model_id,
            "supported_durations": supported_durations,
            "max_duration": max_duration,
            "max_reference_images": max_reference_images,
            "source": source,
            "default_duration": default_duration,
            "content_mode": content_mode,
            "generation_mode": generation_mode,
        }

    async def _resolve_default_image_backend(
        self, svc: ConfigService, session: AsyncSession, capability: Literal["t2i", "i2i"] = "t2i"
    ) -> tuple[str, str]:
        """优先读 default_image_backend_<cap>；新 key **不存在**才回退旧 default_image_backend；都缺则自动解析。

        新 key 存在但值为空字符串 = 用户显式清空 = 跟随自动选择，不再回退 legacy。
        一次 get_all_settings 把候选 key 都拿到，避免迁移期 / 未配置场景两次串行 DB 查询。
        """
        settings = await svc.get_all_settings()
        cap_key = f"default_image_backend_{capability}"
        if cap_key in settings:
            raw = settings[cap_key]
        else:
            raw = settings.get("default_image_backend", "")
        if "/" in raw:
            return ConfigService._parse_backend(raw, _DEFAULT_IMAGE_BACKEND)
        return await self._auto_resolve_backend(svc, session, "image")

    async def _resolve_provider_config(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> dict[str, str]:
        config = await svc.get_provider_config(provider_id)
        cred_repo = CredentialRepository(session)
        active = await cred_repo.get_active(provider_id)
        if active:
            active.overlay_config(config)
        return config

    async def _resolve_all_provider_configs(
        self,
        svc: ConfigService,
        session: AsyncSession,
    ) -> dict[str, dict[str, str]]:
        configs = await svc.get_all_provider_configs()
        cred_repo = CredentialRepository(session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        for provider_id, cred in active_creds.items():
            cfg = configs.setdefault(provider_id, {})
            cred.overlay_config(cfg)
        return configs

    async def default_text_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。"""
        async with self._open_session() as (session, svc):
            return await svc.get_default_text_backend()

    async def text_backend_for_task(
        self,
        task_type: TextTaskType,
        project_name: str | None = None,
    ) -> tuple[str, str]:
        """解析文本 backend。优先级：项目级任务配置 → 全局任务配置 → 全局默认 → 自动推断"""
        async with self._open_session() as (session, svc):
            return await self._resolve_text_backend(svc, session, task_type, project_name)

    async def _resolve_text_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        task_type: TextTaskType,
        project_name: str | None,
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
        return await self._auto_resolve_backend(svc, session, "text")

    async def _auto_resolve_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        media_type: str,
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

        from lib.custom_provider import make_provider_id
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        repo = CustomProviderRepository(session)
        custom_models = await repo.list_enabled_models_by_media_type(media_type)
        for model in custom_models:
            if model.is_default:
                return make_provider_id(model.provider_id), model.model_id

        raise ValueError(f"未找到可用的 {media_type} 供应商。请在「全局设置 → 供应商」页面配置至少一个供应商。")
