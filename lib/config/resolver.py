"""统一运行时配置解析器。

将散落在多个文件中的配置读取和默认值定义集中到一处。
每次调用从 DB 读取，不缓存（本地 SQLite 开销可忽略）。
"""

from __future__ import annotations

import json
import logging
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import async_sessionmaker

from sqlalchemy.ext.asyncio import AsyncSession

from lib.app_data_dir import app_data_dir
from lib.config.registry import PROVIDER_REGISTRY, default_model_for_provider
from lib.config.service import (
    _DEFAULT_AUDIO_BACKEND,
    _DEFAULT_IMAGE_BACKEND,
    _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
    _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
    _DEFAULT_TEXT_BACKEND,
    _DEFAULT_VIDEO_BACKEND,
    ConfigService,
)
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.custom_provider.endpoints import get_endpoint_spec
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.project_manager import ProjectManager
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


@dataclass(frozen=True)
class ProviderModel:
    """provider 解析的结果值对象：一对 (规范 provider_id, model_id)。

    见 CONTEXT.md「ProviderModel」。这是"选了哪个 provider 及其 model"，**不是** backend
    （未构造任何客户端）；命名刻意避开 ``*Backend`` 以保持 provider 身份与 backend 构造的区分。
    ``provider_id`` 一律为规范 id——解析链假设输入即规范形态（由项目迁移 + 写边界保证），不做归一化。
    """

    provider_id: str
    model_id: str


def _parse_bool(raw: str) -> bool:
    """将配置字符串解析为布尔值。"""
    return raw.strip().lower() in _TRUTHY


def _parse_int(raw: object, default: int) -> int:
    """将配置值解析为正整数；空串 / 非数字 / 非正一律回 default（容错，不抛）。"""
    if isinstance(raw, bool):  # bool 是 int 子类，显式排除避免 True→1
        return default
    if isinstance(raw, int):
        return raw if raw > 0 else default
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        return value if value > 0 else default
    return default


# 参考上传副本上限的 per-provider 覆盖 key（裸 API PATCH /providers/{id}/config 可设置）。
_REFERENCE_TOTAL_MAX_BYTES_KEY = "reference_total_max_bytes"
_REFERENCE_SINGLE_MAX_BYTES_KEY = "reference_single_max_bytes"


def _split_pair(raw: object) -> tuple[str, str] | None:
    """解析 ``"<provider>/<model>"`` → (provider, model)；不合法返回 None。

    provider 或 model 为空/纯空白（如 ``"openai/"`` / ``"/m"``）均视为不合法返回 None，
    交由调用方走裸 provider 补默认 model 或回退——避免把空 model 带到执行层。"""
    if not isinstance(raw, str) or "/" not in raw:
        return None
    provider, model = raw.split("/", 1)
    provider, model = provider.strip(), model.strip()
    if not provider or not model:
        return None
    return provider, model


def _parse_project_provider(raw: object, media_type: str) -> tuple[str, str] | None:
    """解析 project.json 的 provider 字段，兼容裸 provider 覆盖。

    - ``"provider/model"`` → (provider, model)
    - 裸 ``"provider"``（registry 中存在且有该 media_type 默认 model）→ (provider, 默认 model)
    - 其余 → None（交由全局默认解析）

    裸 provider 经写边界（``validate_backend_value`` 只放行 registry key）保证是规范 id，这里
    pin 住该 provider 并补全其默认 model，避免静默回退到全局默认的**另一**供应商。"""
    pair = _split_pair(raw)
    if pair is not None:
        return pair
    if isinstance(raw, str):
        # 裸 provider，或带尾斜杠缺 model 的脏值（如 "openai/"）→ 取该 provider 默认 model
        provider = raw.strip().rstrip("/").strip()
        if provider:
            model = default_model_for_provider(provider, media_type)
            if model is not None:
                return provider, model
    return None


def _trusted_payload_provider(provider_id: object) -> str | None:
    """返回可信任的规范 provider_id（已知 provider），否则 None。

    payload 是解析链唯一绕过写边界校验的输入来源（in-flight 队列任务在旧代码入队时即序列化）。
    据此守卫：非字符串 / 空白 / 不可识别的 provider（如 legacy ``seedance``/``vertex``）一律不予
    信任，返回 None 让解析回退到已迁移的 project/global——不做 legacy→规范映射，仅拒绝不可信输入。"""
    if not isinstance(provider_id, str):
        return None
    provider_id = provider_id.strip()
    if not provider_id:
        return None
    if provider_id in PROVIDER_REGISTRY or is_custom_provider(provider_id):
        return provider_id
    return None


def _payload_model_or_default(raw_model: object, provider_id: str, media_type: str) -> str | None:
    """payload 显式 model（非空字符串）优先；缺失则补该 provider 的 registry 默认 model。

    避免「半截 payload」（只有 provider、缺 model）把空 model 带到执行层。补不出默认 model 时
    返回 None，由调用方回退 project/global。"""
    if isinstance(raw_model, str) and raw_model.strip():
        return raw_model.strip()
    return default_model_for_provider(provider_id, media_type)


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

    async def resolve_image_backend(
        self,
        project: dict | None,
        payload: dict | None,
        *,
        capability: Literal["t2i", "i2i"],
    ) -> ProviderModel:
        """解析图片任务应使用的 ProviderModel。

        优先级：payload（本次请求/历史任务）> project（``image_provider_<cap>``）> 全局默认。
        capability 决定走 t2i 还是 i2i 槽（见 ``docs/adr/0001``）。不做任何 provider 归一化。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_image_provider_model(svc, session, project, payload, capability)

    async def resolve_video_backend(
        self,
        project: dict | None,
        payload: dict | None,
    ) -> ProviderModel:
        """解析视频任务应使用的 ProviderModel。

        优先级：payload（历史任务携带的 ``video_provider``）> project（``video_backend``）> 全局默认。
        视频任务无 capability 维度。不做任何 provider 归一化。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_provider_model(svc, session, project, payload)

    async def default_audio_backend(self) -> tuple[str, str]:
        """返回系统级默认音频 (provider_id, model_id)（不含项目级覆盖）。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_audio_backend(svc, session)

    async def resolve_audio_backend(
        self,
        project: dict | None,
        payload: dict | None,
    ) -> ProviderModel:
        """解析语音合成任务应使用的 ProviderModel。

        优先级：payload（历史任务携带的 ``audio_provider``）> project（``audio_backend``）> 全局默认。
        语音任务无 capability 维度。不做任何 provider 归一化。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_audio_provider_model(svc, session, project, payload)

    async def resolve_narration_voice(self, project: dict | None) -> str:
        """解析旁白音色：project.json 顶层 ``narration_voice`` > 全局 setting > 服务默认。"""
        async with self._open_session() as (session, svc):
            if project is not None:
                override = project.get("narration_voice")
                if isinstance(override, str) and override.strip():
                    return override.strip()
            return await svc.get_narration_voice()

    async def resolve_narration_speed(self, project: dict | None) -> float | None:
        """解析旁白语速：project.json 顶层 ``narration_speed`` > 全局 setting > None（不传给 backend）。

        覆盖值宽容解析：数字与数字字符串均接受（口径与 ``default_duration`` 一致）；
        损坏的覆盖值（非数值/非正/非有限）按未设置处理，回退下一级。
        """
        async with self._open_session() as (session, svc):
            if project is not None:
                override = project.get("narration_speed")
                if isinstance(override, (int, float)) and not isinstance(override, bool):
                    try:
                        speed = float(override)
                    except OverflowError:
                        # 超出 float 范围的巨大整数等同非有限值，按未设置回退下一级
                        speed = None
                    if speed is not None and math.isfinite(speed) and speed > 0:
                        return speed
                elif isinstance(override, str):
                    speed_from_str = ConfigService.parse_narration_speed(override)
                    if speed_from_str is not None:
                        return speed_from_str
            return await svc.get_narration_speed()

    async def video_capabilities(self, project_name: str | None = None) -> dict:
        """解析当前项目视频 model 的综合能力 + 用户项目偏好。

        Returns:
            {
              "provider_id": str,
              "model": str,
              "supported_durations": list[int],    # 来自 model (单一真相源)
              "max_duration": int,                 # max(supported_durations) 派生
              "max_reference_images": int,         # registry: model.max_reference_images；custom: endpoint.video_max_reference_images
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

    async def video_capabilities_for_model(self, provider_id: str, model_id: str, project: dict | None = None) -> dict:
        """读取指定 provider/model 的视频能力，不再二次解析 provider。

        供执行层使用：调用方已通过 `resolve_video_backend(project, payload)` 解析出实际
        要调用的 ProviderModel（含历史任务 payload 覆盖），用此变体取能力可保证 duration
        守卫所依据的 supported_durations 与实际调用的 model 一致，避免「按项目默认 model
        的能力去校验 payload 解析出的 model」的错配。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_caps_for_model(svc, session, provider_id, model_id, project)

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

    async def reference_payload_limits(self, provider_id: str | None) -> tuple[int, int]:
        """解析参考上传副本的 (total_max_bytes, single_max_bytes)。

        优先级：per-provider 配置覆盖 > service 层保守通用默认。provider_id 为 None（零配置 /
        通用上限场景）直接返回默认元组、不触 DB。返回纯 int 元组、不 import reference_compression，
        避免把 PIL 间接拖进被广泛依赖的 resolver。
        """
        if provider_id is None:
            return _DEFAULT_REFERENCE_TOTAL_MAX_BYTES, _DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        async with self._open_session() as (session, svc):
            return await self._resolve_reference_payload_limits(svc, session, provider_id)

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
            parsed = _parse_project_provider(project.get("video_backend"), "video")
            if parsed is not None:
                return parsed
        return await self._resolve_default_video_backend(svc, session)

    async def _resolve_image_provider_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        payload: dict | None,
        capability: Literal["t2i", "i2i"],
    ) -> ProviderModel:
        """payload > project > 全局默认 三级解析图片 ProviderModel。

        payload 层保留 ``payload>project>global`` 的规范骨架，当前服务于部署时队列里
        历史任务（携带 ``image_provider_<cap>`` 或旧 ``image_provider``/``image_model``）的排空，
        并作为未来"单请求显式覆盖"的落点。payload provider 须是已知 provider（见
        ``_trusted_payload_provider``），否则不予信任、回退 project/global。
        """
        cap_key = f"image_provider_{capability}"
        if payload:
            pair = _split_pair(payload.get(cap_key))
            if pair is not None and _trusted_payload_provider(pair[0]) is not None:
                return ProviderModel(*pair)
            provider_id = _trusted_payload_provider(payload.get("image_provider"))
            if provider_id is not None:
                model = _payload_model_or_default(payload.get("image_model"), provider_id, "image")
                if model is not None:
                    return ProviderModel(provider_id, model)
        if project:
            parsed = _parse_project_provider(project.get(cap_key), "image")
            if parsed is not None:
                return ProviderModel(*parsed)
        provider_id, model_id = await self._resolve_default_image_backend(svc, session, capability)
        return ProviderModel(provider_id, model_id)

    async def _resolve_video_provider_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        payload: dict | None,
    ) -> ProviderModel:
        """payload > project > 全局默认 三级解析视频 ProviderModel。

        payload 层服务于历史任务（携带 ``video_provider`` + ``video_model`` /
        ``video_provider_settings.model``）的排空。payload provider 须是已知 provider（见
        ``_trusted_payload_provider``），否则不予信任、回退 project/global。
        """
        if payload:
            provider_id = _trusted_payload_provider(payload.get("video_provider"))
            if provider_id is not None:
                settings = payload.get("video_provider_settings")
                settings_model = settings.get("model") if isinstance(settings, dict) else None
                model = _payload_model_or_default(payload.get("video_model") or settings_model, provider_id, "video")
                if model is not None:
                    return ProviderModel(provider_id, model)
        provider_id, model_id = await self._resolve_video_backend_from_project(svc, session, project)
        return ProviderModel(provider_id, model_id)

    async def _resolve_default_audio_backend(self, svc: ConfigService, session: AsyncSession) -> tuple[str, str]:
        raw = await svc.get_setting("default_audio_backend", "")
        if raw and "/" in raw:
            return ConfigService._parse_backend(raw, _DEFAULT_AUDIO_BACKEND)
        return await self._auto_resolve_backend(svc, session, "audio")

    async def _resolve_audio_backend_from_project(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
    ) -> tuple[str, str]:
        if project is not None:
            parsed = _parse_project_provider(project.get("audio_backend"), "audio")
            if parsed is not None:
                return parsed
        return await self._resolve_default_audio_backend(svc, session)

    async def _resolve_audio_provider_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        project: dict | None,
        payload: dict | None,
    ) -> ProviderModel:
        """payload > project > 全局默认 三级解析音频 ProviderModel。

        payload 层服务于历史任务（携带 ``audio_provider`` + ``audio_model``）的排空。payload
        provider 须是已知 provider（见 ``_trusted_payload_provider``），否则回退 project/global。
        """
        if payload:
            provider_id = _trusted_payload_provider(payload.get("audio_provider"))
            if provider_id is not None:
                model = _payload_model_or_default(payload.get("audio_model"), provider_id, "audio")
                if model is not None:
                    return ProviderModel(provider_id, model)
        provider_id, model_id = await self._resolve_audio_backend_from_project(svc, session, project)
        return ProviderModel(provider_id, model_id)

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
        return await self._resolve_video_caps_for_model(svc, session, provider_id, model_id, project)

    async def _resolve_video_caps_for_model(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
        model_id: str,
        project: dict | None,
    ) -> dict:
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

            endpoint_spec = get_endpoint_spec(model.endpoint)
            if endpoint_spec.media_type != "video":
                raise ValueError(
                    f"endpoint media_type mismatch: {provider_id}/{model_id} endpoint={model.endpoint!r} "
                    f"is {endpoint_spec.media_type}, not video"
                )
            endpoint_cap = endpoint_spec.video_max_reference_images
            if endpoint_cap is not None:
                max_reference_images = endpoint_cap
            else:
                # endpoint cap 未声明（多 model 共享端点、容量随 model 变）→ 用 endpoint 绑定的纯
                # caps 函数按 model_id 读 backend 声明的上限。纯函数不查 provider 行、不构造 SDK
                # client，故每镜头解析无 DB/网络/client 构造副作用（也不因 api_key 缺失而抛）。
                caps_fn = endpoint_spec.video_caps_for_model
                if caps_fn is None:
                    raise ValueError(
                        f"video endpoint {model.endpoint!r} declares neither video_max_reference_images "
                        f"nor video_caps_for_model: {provider_id}/{model_id}"
                    )
                max_reference_images = caps_fn(model_id).max_reference_images
                if max_reference_images < 0:
                    # backend caps 是这条链路的真相源，负数直接抛错，不静默下传——
                    # 否则 reference_video_tasks 会按坏值跳过裁剪。
                    raise ValueError(
                        f"invalid backend max_reference_images: {provider_id}/{model_id} "
                        f"endpoint={model.endpoint!r} value={max_reference_images!r}"
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
            max_reference_images = model_info.max_reference_images

        if not supported_durations:
            raise ValueError(f"supported_durations is empty for {provider_id}/{model_id}; cannot derive capabilities")

        max_duration = max(supported_durations)

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

    async def _resolve_reference_payload_limits(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> tuple[int, int]:
        try:
            cfg = await self._resolve_provider_config(svc, session, provider_id)
        except ValueError:
            # 未知 / 自定义 provider（_validate_provider 抛 ValueError）→ 回退保守通用默认
            return _DEFAULT_REFERENCE_TOTAL_MAX_BYTES, _DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        total = _parse_int(cfg.get(_REFERENCE_TOTAL_MAX_BYTES_KEY), _DEFAULT_REFERENCE_TOTAL_MAX_BYTES)
        single = _parse_int(cfg.get(_REFERENCE_SINGLE_MAX_BYTES_KEY), _DEFAULT_REFERENCE_SINGLE_MAX_BYTES)
        return total, single

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
