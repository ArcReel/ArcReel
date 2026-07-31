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

from lib.backend_assembly.specs import get_provider_spec
from lib.config.registry import (
    PROVIDER_REGISTRY,
    default_model_for_provider,
    model_has_audio_track,
    model_info_for,
)
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
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.project_manager import get_project_manager
from lib.text_backends.base import TEXT_TASK_TIERS, VISION_REQUIRED_TASKS, TextTaskTier, TextTaskType
from lib.video_backends.registry import (
    effective_generate_audio_for_model as builtin_effective_generate_audio_for_model,
)
from lib.video_backends.registry import video_capabilities_for_model as builtin_video_capabilities_for_model

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


# 档位 → 设置键。全局（system_settings）与项目级（project.json）同名同构。
_TEXT_TIER_SETTING_KEYS: dict[TextTaskTier, str] = {
    TextTaskTier.SIMPLE: "text_backend_simple",
    TextTaskTier.COMPLEX: "text_backend_complex",
}


# 当 resolve_resolution 返回 None 时下游的保底分辨率。Grok 即便 registry 声明 1080p
# 也可能被 xai_sdk 拒收，故按 provider 区分。
PROVIDER_FALLBACK_RESOLUTION: dict[str, str] = {
    "gemini": "1080p",
    "ark": "720p",
    "grok": "720p",
    "openai": "720p",
    # MiniMax 海螺缺省 768P：1080P 仅 6s，默认落 768P 避免与 10s 档冲突。
    "minimax": "768p",
}


def get_provider_fallback(provider_id: str | None, default: str = "1080p") -> str:
    """纯查表：对 registry ID（如 ``gemini-aistudio``）归一化到短前缀后查 fallback。不触 DB。"""
    if not provider_id:
        return default
    if provider_id in PROVIDER_FALLBACK_RESOLUTION:
        return PROVIDER_FALLBACK_RESOLUTION[provider_id]
    short = provider_id.split("-", 1)[0]
    return PROVIDER_FALLBACK_RESOLUTION.get(short, default)


#: 请求里不下发 ``generate_audio`` 开关、供应商恒按含音档出账的 video provider。
_VIDEO_AUDIO_ALWAYS_BILLED_PROVIDERS = frozenset({"gemini-aistudio"})


def _video_audio_always_billed(provider_id: str) -> bool:
    """该 provider 是否无视请求值恒按含音档出账。

    AI Studio 的 Veo 请求不下发 ``generate_audio``（该开关仅存在于 Vertex 定价页，AI Studio
    定价页无 audio-off 档），``GeminiVideoBackend`` 结算时对 ``backend_type != "vertex"`` 强制
    True；这是 provider 级出账规则，registry 名 ``gemini`` 同时映射 aistudio / vertex，故按
    provider_id 而非 backend 静态接口判定。
    """
    return provider_id in _VIDEO_AUDIO_ALWAYS_BILLED_PROVIDERS


#: 声音一致性三级标识。前端 `VoiceConsistencyTier` 与之一一对应，档位增减须两侧同步。
VoiceConsistency = Literal["native", "soft", "none"]


def derive_voice_consistency(
    *,
    reference_audio_mode: str,
    generation_mode: str | None,
    has_audio: bool,
) -> VoiceConsistency:
    """三级声音一致性标识派生（native / soft / none），模型能力 × 项目 generation_mode 二维。

    全仓库唯一派生点：项目内场景经 `_resolve_video_caps_for_model` 走这里，无项目上下文的
    目录场景由 `server/routers/providers.py` 以 ``generation_mode=None`` 调同一函数，前端不
    复制第二份公式。

    ``reference_audio_mode`` 按字面量比较（``ReferenceAudioMode`` 是 ``StrEnum``，两者可
    直接 ``==``），不在 lib.config 层导入 lib.video_backends（分层契约，config 是最底层）。

    native 蕴含有音轨：generation_mode 非参考生视频时一律降格 soft，不降到 none。soft/none
    之分不看 ``generate_audio`` token 是否声明——该 token 语义是「开关可控」而非「有无音轨」，
    恒有声但开关不可控的 provider 经 ``model_has_audio_track`` 单独识别为有音轨。
    """
    if reference_audio_mode == "direct" and generation_mode == "reference_video":
        return "native"
    return "soft" if has_audio else "none"


def _safe_dict(value: object, *, field: str) -> dict:
    """确保 value 可当作 dict 继续链式解析；非 dict（含 None）一律降级为空 dict。

    project.json 是明文文件，用户手编或外部脚本写坏后，嵌套字段可能变成 string / list，
    直接 ``.get()`` 会抛 ``AttributeError``。None 是显式「未配置」，静默降级；其余非 dict
    类型记录 warning（字段名 + 实际类型）便于定位脏数据来源。
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    logger.warning(
        "project.json field %r has non-dict type %s, falling back to empty dict",
        field,
        type(value).__name__,
    )
    return {}


def _resolution_from_project(project: dict, provider_id: str, model_id: str) -> str | None:
    """project.model_settings（``provider/model`` 复合 key）> legacy video_model_settings > None。

    逐层用 ``_safe_dict`` 防御非 dict 中间层（见 ``_safe_dict`` docstring）。
    """
    key = f"{provider_id}/{model_id}"
    model_settings = _safe_dict(project.get("model_settings"), field="model_settings")
    override = _safe_dict(model_settings.get(key), field=f"model_settings.{key}").get("resolution")
    if override:
        return override
    video_model_settings = _safe_dict(project.get("video_model_settings"), field="video_model_settings")
    legacy = _safe_dict(video_model_settings.get(model_id), field=f"video_model_settings.{model_id}").get("resolution")
    if legacy:
        return legacy
    return None


# ---------------------------------------------------------------------------
# 时长联动约束
#
# 时长并非只由 supported_durations 决定：部分型号（当前是 Veo 全系与 MiniMax 海螺）在高分辨率
# 或参考图路径下把时长收窄到单一取值，声明在 registry 的 ModelInfo 上（单一真相源）。这里是
# 后端唯一的消费入口——候选集合在交给 LLM、写进动态 schema、或作为默认值取首项之前都经此收窄，
# 否则产出的时长在执行期必然被 backend 拒。
# ---------------------------------------------------------------------------


def constrain_durations(
    provider_id: str | None,
    model_id: str | None,
    durations: list[int],
    *,
    resolution: str | None = None,
    uses_reference_images: bool = False,
) -> list[int]:
    """按型号声明的「分辨率↔时长」「参考图↔时长」约束收窄候选。

    两条约束各自独立触发、可同时生效，取交集。无声明、型号不在注册表（自定义供应商不表达
    这类约束）、或交集为空时返回原候选——空集说明声明之间自相矛盾，清空候选会让上游拿不到
    任何可用时长，而执行期仍有 backend 校验兜底。
    """
    if not durations:
        return durations
    model_info = model_info_for(provider_id, model_id) if provider_id and model_id else None
    if model_info is None:
        return durations
    allowed = list(durations)
    if uses_reference_images and model_info.reference_image_durations:
        allowed = [d for d in allowed if d in model_info.reference_image_durations]
    by_resolution = model_info.duration_resolution_constraints.get(resolution.strip().lower()) if resolution else None
    if by_resolution:
        allowed = [d for d in allowed if d in by_resolution]
    if not allowed:
        logger.warning(
            "duration constraints for %s/%s have no overlap with candidate durations "
            "(resolution=%r, uses_reference_images=%r), falling back to unconstrained candidates %r",
            provider_id,
            model_id,
            resolution,
            uses_reference_images,
            durations,
        )
        return list(durations)
    return allowed


def _resolution_for_constraints(
    project: dict, provider_id: str | None, model_id: str | None, *, generation_mode: str | None
) -> str | None:
    """约束求值用的生效分辨率：项目已保存的档位，参考视频模式下补 provider 兜底。

    联动约束必须按**执行期真正下发给供应商的那个档位**求值，而两条视频路径下发的值不同源：

    - 普通图生视频路径下发 ``resolve_resolution()`` 的原始结果，``None`` 即「不传 resolution
      参数」（见 ``docs/adr/0019``），供应商按自己的默认档位处理——Veo 省略时是 720p，4/6/8 全
      合法。此时按兜底档位求值会凭空收窄：未配置分辨率的 Veo 项目剧本节奏会被锁死 8 秒，而
      供应商本来就接受 4/6 秒。故未配置时返回 ``None``（不施加分辨率约束）。
    - 参考视频路径是唯一需要非空档位的调用方，执行期取 ``resolution_or_fallback``（见
      ``server/services/reference_video_tasks.py``），故这里同样补 ``get_provider_fallback``，
      让约束与实际下发的档位描述同一件事。

    ``get_provider_fallback`` 本身是费用估算与参考视频路径的内部口径，不是「用户没配分辨率时
    的生效值」，不可当作后者施加到普通路径上。自定义供应商的 DB 默认档位不在此解析：该类
    供应商不声明联动约束，解析出来也不改变结果，不值得为此把纯函数变成 async。

    返回值只用于约束求值，不得作为 SDK 的 resolution 参数下传。
    """
    if not provider_id or not model_id:
        return None
    saved = _resolution_from_project(project, provider_id, model_id)
    if saved or generation_mode != "reference_video":
        return saved
    return get_provider_fallback(provider_id)


def constrain_durations_for_project(
    project: dict,
    durations: list[int],
    *,
    provider_id: str | None,
    model_id: str | None,
    generation_mode: str | None,
    uses_reference_images: bool | None = None,
) -> list[int]:
    """按项目当前配置收窄时长候选：分辨率取生效档位，参考图约束按是否真的带参考图判定。

    ``uses_reference_images`` 缺省时退回「生成模式即参考视频」的近似判定。调用方能看到本次
    实际的参考图情况时应显式传入：参考视频路径允许单元不带任何引用，执行层与 backend 都只在
    ``reference_images`` 非空时施加该约束，按模式一刀切会把无引用单元本可申请的档位也收掉。
    """
    return constrain_durations(
        provider_id,
        model_id,
        durations,
        resolution=_resolution_for_constraints(project, provider_id, model_id, generation_mode=generation_mode),
        uses_reference_images=(
            generation_mode == "reference_video" if uses_reference_images is None else uses_reference_images
        ),
    )


class VisionCapabilityError(ValueError):
    """解析出的文本模型不支持图像输入（vision），无法执行需要 vision 的任务。

    携带结构化字段供调用方（如面向用户的 router）按需本地化；``str(exc)`` 是英文技术
    消息，供 log / 非用户可见路径直接使用。"""

    def __init__(self, *, task_type: TextTaskType, provider_id: str, model_id: str):
        self.task_type = task_type
        self.provider_id = provider_id
        self.model_id = model_id
        super().__init__(
            f"text model {provider_id}/{model_id} does not support vision, cannot perform task {task_type.value}"
        )


def _ensure_text_model_vision_capable(task_type: TextTaskType, provider_id: str, model_id: str) -> None:
    """校验解析出的模型支持图像输入；不满足直接报错，不静默换模型。

    仅对 PROVIDER_REGISTRY 中登记的模型判定；registry 之外（自定义供应商等）无逐模型
    能力事实，放行交由供应商 API 把关，不做猜测。"""
    meta = PROVIDER_REGISTRY.get(provider_id)
    model_info = meta.models.get(model_id) if meta else None
    if model_info is not None and "vision" not in model_info.capabilities:
        raise VisionCapabilityError(task_type=task_type, provider_id=provider_id, model_id=model_id)


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

        返回字面配置结果，不做自定义 provider 的身份收敛——供配置展示与「当前选的是哪个」类
        判断使用；要拿运行时实际执行的身份请用 ``resolve_video_backend()``。
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
        视频任务无 capability 维度；provider id 不做归一化。

        返回的是**运行时有效身份**：自定义 provider 的 model 不存在、已禁用或 endpoint 的
        media_type 不是 video 时，收敛到该 provider 默认启用的 video model，无可用默认则抛
        ``ValueError``。能力查询与价格查询因此拿到同一身份。只要字面配置结果（不经收敛）
        请改用 ``video_backend()``。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_provider_model(svc, session, project, payload)

    async def resolve_resolution(self, project: dict, provider_id: str, model_id: str) -> str | None:
        """按 project.model_settings → legacy video_model_settings → 自定义供应商默认 → None。

        None 代表"调用时不传 SDK resolution 参数"（见 ``docs/adr/0019``）。前两级纯读 project
        dict、无副作用；自定义供应商默认（``CustomProviderModel.resolution``）需 DB，故本方法
        整体为 async 并在同一 session 内完成。
        """
        from_project = _resolution_from_project(project, provider_id, model_id)
        if from_project:
            return from_project
        # 仅自定义供应商才有 DB 侧默认；预置供应商在此直接 None，避免为热路径上的
        # 每次生成任务白开一个 session（原独立模块正是先判 is_custom_provider 再触 DB）。
        if not provider_id or not model_id or not is_custom_provider(provider_id):
            return None
        async with self._open_session() as (session, _svc):
            return await self._resolve_custom_resolution_default(session, provider_id, model_id)

    async def _resolve_custom_resolution_default(
        self,
        session: AsyncSession,
        provider_id: str,
        model_id: str,
    ) -> str | None:
        """自定义供应商的模型默认 resolution（``CustomProviderModel.resolution``），其他一律 None。"""
        if not provider_id or not model_id or not is_custom_provider(provider_id):
            return None
        try:
            db_id = parse_provider_id(provider_id)
        except ValueError:
            return None
        repo = CustomProviderRepository(session)
        model = await repo.get_model_by_ids(db_id, model_id)
        return model.resolution if (model and model.resolution) else None

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
              "max_reference_images": int,         # registry: model.max_reference_images；custom: 合成后的生效值
              "first_frame": bool,                 # 生效值（系统判定 ⊕ 用户覆盖），与执行层同源
              "last_frame": bool,                  # 同上
              "generate_audio": bool,              # backend 默认执行档生效后的计价参数
              "source": "registry" | "custom",
              "default_duration": int | None,      # 用户在 project.json 里设置的偏好
              "content_mode": str | None,
              "generation_mode": str | None,
              "voice_consistency": "native" | "soft" | "none",  # 模型能力 × generation_mode 二维派生
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

        入参身份仍会再收敛一次（口径同 ``resolve_video_backend``），因此直接传字面配置也能
        拿到有效身份的能力；自定义 provider 无可用默认 model 时抛 ``ValueError``。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_caps_for_model(svc, session, provider_id, model_id, project)

    async def video_pricing_generate_audio(
        self,
        provider_id: str,
        model_id: str,
        project: dict | None = None,
    ) -> bool:
        """费用预估用的有效 ``generate_audio``：读能力接口，解析不出时降级，绝不抛错。

        能力解析会对注册表里已下线的 model id 抛错，而价目查询对同一 id 仍会回落到该 provider
        的默认模型出价（见 ``lib/pricing/lookup.py``）——此时估算仍要出数。降级口径分两层：
        恒含音出账的 provider 按 provider 级规则取 True；其余 provider 没有默认执行档的信息，
        只能回到请求值（backend 也正是照请求值下发并结算），若一律取 False，这些历史 model
        会被按静音档低估。
        """
        try:
            caps = await self.video_capabilities_for_model(provider_id, model_id, project)
        except Exception as exc:
            logger.info(
                "视频能力解析失败（%s/%s），计价 generate_audio 降级到 provider 级规则与请求值：%s",
                provider_id,
                model_id,
                exc,
            )
            if _video_audio_always_billed(provider_id):
                return True
            async with self._open_session() as (_session, svc):
                return await self._resolve_video_generate_audio_from_project(svc, project)
        return bool(caps["generate_audio"])

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
        project = get_project_manager().load_project(project_name) if project_name else None
        return await self._resolve_video_generate_audio_from_project(svc, project)

    async def _resolve_video_generate_audio_from_project(
        self,
        svc: ConfigService,
        project: dict | None,
    ) -> bool:
        raw = await svc.get_setting("video_generate_audio", "")
        value = _parse_bool(raw) if raw else self._DEFAULT_VIDEO_GENERATE_AUDIO

        if project is not None:
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
        selected: ProviderModel | None = None
        if payload:
            provider_id = _trusted_payload_provider(payload.get("video_provider"))
            if provider_id is not None:
                settings = payload.get("video_provider_settings")
                settings_model = settings.get("model") if isinstance(settings, dict) else None
                model = _payload_model_or_default(payload.get("video_model") or settings_model, provider_id, "video")
                if model is not None:
                    selected = ProviderModel(provider_id, model)
        if selected is None:
            provider_id, model_id = await self._resolve_video_backend_from_project(svc, session, project)
            selected = ProviderModel(provider_id, model_id)
        return await self._resolve_effective_video_provider_model(session, selected)

    async def _resolve_effective_video_provider_model(
        self,
        session: AsyncSession,
        selected: ProviderModel,
    ) -> ProviderModel:
        """把选择身份收敛为 backend 构造时会实际使用的视频身份。

        内置 provider 的 registry 身份已是有效身份；自定义 provider 需与 loader 共用同一规则：
        model 不存在、禁用或 endpoint 已改成其它 media_type 时，回退到默认启用 video model。
        """
        if not is_custom_provider(selected.provider_id):
            return selected

        # 延迟导入：分层契约（pyproject.toml [tool.importlinter]）以 lib.config 为下层，
        # 而该符号所在的装配层反过来依赖 lib.config，模块级导入会成环；内置分支不用它，
        # 也就不必为此拉起整个装配层。
        from lib.custom_provider.endpoints import endpoint_to_media_type

        try:
            db_pid = parse_provider_id(selected.provider_id)
        except ValueError as exc:
            raise ValueError(f"invalid custom provider_id: {selected.provider_id}") from exc
        repo = CustomProviderRepository(session)
        model = await repo.get_model_by_ids(db_pid, selected.model_id)
        if model is not None and model.is_enabled and endpoint_to_media_type(model.endpoint) == "video":
            return selected

        logger.warning(
            "自定义模型 %s/%s 已不存在 / 已禁用 / 媒体类型不符（期望 video），身份解析回退到默认模型",
            selected.provider_id,
            selected.model_id,
        )
        default_model = await repo.get_default_model(db_pid, "video")
        if default_model is None:
            raise ValueError(f"custom model not found: {selected.provider_id}/{selected.model_id}")
        return ProviderModel(selected.provider_id, default_model.model_id)

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
        # 只传选择身份：有效身份收敛由 `_resolve_video_caps_for_model` 统一做，
        # 在此先做一遍会让自定义 provider 多跑一轮 model 查询。
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
        effective = await self._resolve_effective_video_provider_model(session, ProviderModel(provider_id, model_id))
        provider_id, model_id = effective.provider_id, effective.model_id
        if is_custom_provider(provider_id):
            # 延迟导入：分层契约（pyproject.toml [tool.importlinter]）以 lib.config 为下层，
            # 而该符号所在的装配层反过来依赖 lib.config，模块级导入会成环；注册表分支不用它，
            # 也就不必为此拉起整个装配层。
            from lib.custom_provider.capabilities import synthesize_video_capabilities

            source = "custom"
            try:
                db_pid = parse_provider_id(provider_id)
            except ValueError as exc:
                raise ValueError(f"invalid custom provider_id: {provider_id}") from exc
            repo = CustomProviderRepository(session)
            model = await repo.get_model_by_ids(db_pid, model_id)
            if model is None:
                raise ValueError(f"custom model not found after identity resolution: {provider_id}/{model_id}")

            # 生效能力（系统判定 ⊕ 用户覆盖）只此一个合成点：工厂给执行层注入的也是它的返回值，
            # 展示层与执行层因此严格同源，不在此处自行合并覆盖或重算系统判定。纯函数不查
            # provider 行、不构造 SDK client，故每镜头解析无 DB/网络/client 构造副作用
            # （也不因 api_key 缺失而抛）。
            try:
                caps = synthesize_video_capabilities(
                    endpoint=model.endpoint,
                    model_id=model_id,
                    overrides=model.capability_overrides,
                )
            except ValueError as exc:
                raise ValueError(f"cannot resolve video capabilities for {provider_id}/{model_id}: {exc}") from exc
            max_reference_images = caps.max_reference_images
            first_frame = caps.first_frame
            last_frame = caps.last_frame
            reference_audio_mode = caps.reference_audio_mode
            # 自定义供应商按声明单价计费（`CustomProviderPrice` 无音频维度），计价参数不因
            # 默认执行档收窄，故沿用项目请求值。
            default_tier_generates_audio = True
            # 自定义供应商无 generate_audio 目录声明，与上一行 default_tier_generates_audio
            # 同口径：无信号时假定有声，不凭空判定为真无声模型。
            has_audio = True
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
            # 布尔能力位读 backend 声明，不复制进 ModelInfo：backend 是这几位的唯一真相源。
            # max_reference_images 维持读 ModelInfo（注册表按 model 分档，backend 侧不区分）。
            try:
                spec = get_provider_spec(provider_id, "video")
                builtin_caps = builtin_video_capabilities_for_model(spec.registry_backend, model_id)
            except ValueError as exc:
                raise ValueError(f"cannot resolve video capabilities for {provider_id}/{model_id}: {exc}") from exc
            first_frame = builtin_caps.first_frame
            last_frame = builtin_caps.last_frame
            reference_audio_mode = builtin_caps.reference_audio_mode
            has_audio = model_has_audio_track(provider_id, model_info)
            try:
                default_tier_generates_audio = builtin_effective_generate_audio_for_model(
                    spec.registry_backend, model_id
                )
            except ValueError as exc:
                raise ValueError(
                    f"cannot resolve video pricing capabilities for {provider_id}/{model_id}: {exc}"
                ) from exc

        if not supported_durations:
            raise ValueError(f"supported_durations is empty for {provider_id}/{model_id}; cannot derive capabilities")

        max_duration = max(supported_durations)

        requested_generate_audio = await self._resolve_video_generate_audio_from_project(svc, project)
        # 恒含音出账的 provider 无视请求值；其余 backend 只有在项目请求开启、且无上下文能力
        # 接口确认默认执行档会产出人声时，计价参数才为 True。
        generate_audio = (
            True
            if _video_audio_always_billed(provider_id)
            else requested_generate_audio and default_tier_generates_audio
        )

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

        voice_consistency = derive_voice_consistency(
            reference_audio_mode=reference_audio_mode,
            generation_mode=generation_mode,
            has_audio=has_audio,
        )

        return {
            "provider_id": provider_id,
            "model": model_id,
            "supported_durations": supported_durations,
            "max_duration": max_duration,
            "max_reference_images": max_reference_images,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "generate_audio": generate_audio,
            "source": source,
            "default_duration": default_duration,
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "voice_consistency": voice_consistency,
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
        """按任务档位解析文本 backend。

        优先级（项目优先）：项目档位 > 项目默认模型 > 全局档位 > 全局默认模型 > 自动推断。
        任务需要 vision 时校验解析结果的能力，不满足直接报错、不静默换模型（docs/adr/0051）。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_text_backend(svc, session, task_type, project_name)

    async def _resolve_text_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        task_type: TextTaskType,
        project_name: str | None,
    ) -> tuple[str, str]:
        tier_key = _TEXT_TIER_SETTING_KEYS[TEXT_TASK_TIERS[task_type]]
        resolved: tuple[str, str] | None = None

        # 1/2. 项目档位 > 项目默认模型（「项目默认」读作「本项目整体用它」，遮蔽全局配置）
        if project_name:
            project = get_project_manager().load_project(project_name)
            for key in (tier_key, "default_text_backend"):
                project_val = project.get(key)
                if project_val and "/" in str(project_val):
                    resolved = ConfigService._parse_backend(str(project_val), _DEFAULT_TEXT_BACKEND)
                    break

        # 3/4. 全局档位 > 全局默认模型
        if resolved is None:
            for key in (tier_key, "default_text_backend"):
                global_val = await svc.get_setting(key, "")
                if global_val and "/" in global_val:
                    resolved = ConfigService._parse_backend(global_val, _DEFAULT_TEXT_BACKEND)
                    break

        # 5. 自动推断
        if resolved is None:
            resolved = await self._auto_resolve_backend(svc, session, "text")

        if task_type in VISION_REQUIRED_TASKS:
            _ensure_text_model_vision_capable(task_type, *resolved)
        return resolved

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
