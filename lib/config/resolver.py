"""统一运行时配置解析器。

将散落在多个文件中的配置读取和默认值定义集中到一处。
每次调用从 DB 读取，不缓存（本地 SQLite 开销可忽略）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, get_args

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

from collections.abc import AsyncGenerator, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import DurationConstraints
from lib.backends.audio_profiles import AudioProfile
from lib.backends.input_limits import InputLimit
from lib.backends.providers import is_custom_provider
from lib.capabilities.kernel import video_bucket_unmet
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError, ModelNotSelectedError
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import (
    DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
    DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
    ConfigService,
)
from lib.db.repositories.credential_repository import CredentialRepository
from lib.speech.character_voice import CharacterVoiceBinding
from lib.speech.narration_config import (
    TTS_GROUP_FIELDS,
    USE_TTS,
    NarrationConfigError,
    TtsSynthesisSettings,
    project_narration_delivery,
    require_project_tts_generation,
)

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"true", "1", "yes"})


@dataclass(frozen=True)
class ProviderModel:
    """provider 解析的结果值对象：一对 (规范 provider_id, model_id)。

    见 GLOSSARY.md「ProviderModel」。这是"选了哪个 provider 及其 model"，**不是** backend
    （未构造任何客户端）；命名刻意避开 ``*Backend`` 以保持 provider 身份与 backend 构造的区分。
    ``provider_id`` 一律为规范 id——解析链假设输入即规范形态（由项目迁移 + 写边界保证），不做归一化。
    """

    provider_id: str
    model_id: str

    @property
    def pair_key(self) -> str:
        """复合键形态 ``"<provider>/<model>"``，与 ``_split_pair`` 互逆。

        配置层与 payload 层的 provider 字段、按执行模型取键的 per-model 存储都用这一形态；
        写入方一律经此属性成形，格式只此一处定义。
        """
        return f"{self.provider_id}/{self.model_id}"


def _parse_bool(raw: str) -> bool:
    """将配置字符串解析为布尔值。"""
    return raw.strip().lower() in _TRUTHY


def _parse_int(raw: object, default: int) -> int:
    """将配置值解析为正整数；空串 / 非数字 / 非正一律回 default（容错，不抛）。"""
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw if raw > 0 else default
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        return value if value > 0 else default
    return default


_REFERENCE_TOTAL_MAX_BYTES_KEY = "reference_total_max_bytes"
_REFERENCE_SINGLE_MAX_BYTES_KEY = "reference_single_max_bytes"


def _split_pair(raw: object) -> tuple[str, str] | None:
    """解析 ``"<provider>/<model>"`` → (provider, model)；不合法返回 None。

    provider 或 model 为空/纯空白（如 ``"openai/"`` / ``"/m"``）均视为不合法返回 None，
    交由调用方按裸供应商报错或回退——避免把空 model 带到执行层。"""
    if not isinstance(raw, str) or "/" not in raw:
        return None
    provider, model = raw.split("/", 1)
    provider, model = provider.strip(), model.strip()
    if not provider or not model:
        return None
    return provider, model


def _bare_provider(raw: object) -> str | None:
    """project.json provider 字段的裸供应商覆盖（裸 ``"provider"``，或带尾斜杠缺 model 的脏值如 ``"openai/"``）→ 供应商 id。

    完整的 ``"provider/model"``、非字符串与空值为 None。"""
    if not isinstance(raw, str) or _split_pair(raw) is not None:
        return None
    return raw.strip().rstrip("/").strip() or None


def _parse_project_provider(raw: object) -> tuple[str, str] | None:
    """解析 project.json 的 provider 字段：只认 ``"provider/model"``，其余（含裸供应商）为 None。

    运行时不按目录推荐补全裸供应商（docs/adr/0099）：存量裸供应商由 v19→v20 项目迁移固化为
    ``provider/model``。供没有配置库会话的同步路径使用。"""
    return _split_pair(raw)


def _reject_bare_builtin_provider(provider: str, media_type: str) -> None:
    """项目层只写了内置供应商、没写模型（迁移没能补全的裸供应商）时报错。

    报「没有选定模型」让用户重新选择，不按目录推荐补模型，也不回退到另一层或另一供应商去生成和计费。该供应商
    在模型清单里根本没有这一媒体类型的模型时，改报它给不出这一类生成。
    """
    if provider not in PROVIDER_REGISTRY:
        return
    if media_type not in catalog.provider_media_types(provider):
        raise ModelNotInCatalogError(provider_id=provider, model_id="", media_type=media_type)
    raise ModelNotSelectedError(provider_id=provider, media_type=media_type)


def _trusted_payload_provider(provider_id: object) -> str | None:
    """返回可信任的规范 provider_id（已知供应商），否则 None。

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


def _payload_model(raw_model: object) -> str | None:
    """payload 显式 model（非空字符串）；缺失时为 None，由调用方回退 project/global。

    「半截 payload」（只有 provider、缺 model）不按目录推荐补模型（docs/adr/0099），也不把空 model 带到执行层。"""
    if isinstance(raw_model, str) and raw_model.strip():
        return raw_model.strip()
    return None


@dataclass(frozen=True)
class _LayeredBackendKeys:
    """「默认 + 任务类型桶」四级解析骨架的键位声明，媒体类型无关（见 ``docs/adr/0054``）。

    每个媒体类型的每个任务类型桶声明一份键位，由 ``ConfigResolver._resolve_layered_backend``
    按固定顺序消费：项目桶 > 项目默认 > 全局桶 > 全局默认 > 自动推断。键为 None 表示该层
    不存在、直接跳过——新媒体 / 新桶接入只需补一份键位声明，不改骨架本身。
    """

    media_type: str
    project_bucket_key: str | None = None
    project_default_key: str | None = None
    global_bucket_key: str | None = None
    global_default_key: str | None = None
    generation_type: str | None = None


_IMAGE_LAYERED_KEYS: dict[str, _LayeredBackendKeys] = {
    generation_type: _LayeredBackendKeys(
        media_type="image",
        project_bucket_key=f"image_provider_{generation_type}",
        project_default_key="default_image_backend",
        global_bucket_key=f"default_image_backend_{generation_type}",
        global_default_key="default_image_backend",
        generation_type=generation_type,
    )
    for generation_type in ("t2i", "i2i")
}


_VIDEO_LAYERED_KEYS: dict[str, _LayeredBackendKeys] = {
    generation_type: _LayeredBackendKeys(
        media_type="video",
        project_bucket_key=f"video_provider_{generation_type}",
        project_default_key="video_backend",
        global_bucket_key=f"default_video_backend_{generation_type}",
        global_default_key="default_video_backend",
    )
    for generation_type in ("i2v", "r2v")
}


_VIDEO_DEFAULT_LAYERED_KEYS = _LayeredBackendKeys(
    media_type="video",
    project_default_key="video_backend",
    global_default_key="default_video_backend",
)


_AUDIO_LAYERED_KEYS = _LayeredBackendKeys(
    media_type="audio",
    project_default_key="audio_backend",
    global_default_key="default_audio_backend",
)

VideoGenerationType = Literal["i2v", "r2v"]

VIDEO_BUCKET_BY_TASK_TYPE: dict[str, VideoGenerationType] = {
    "video": "i2v",
    "reference_video": "r2v",
}

VIDEO_BUCKET_BY_GENERATION_MODE: dict[str, VideoGenerationType] = {
    "storyboard": "i2v",
    "reference_video": "r2v",
}

_DEFAULT_VIDEO_BUCKET: VideoGenerationType = "i2v"


def video_bucket_for_generation_mode(generation_mode: str | None) -> VideoGenerationType:
    """项目的 generation_mode 归到哪个视频任务类型桶——读侧定桶的唯一入口。

    project.json 是明文文件，``generation_mode`` 可能被写成非字符串，一并落默认桶。
    """
    if not isinstance(generation_mode, str):
        return _DEFAULT_VIDEO_BUCKET
    return VIDEO_BUCKET_BY_GENERATION_MODE.get(generation_mode, _DEFAULT_VIDEO_BUCKET)


def payload_video_pinned_pair(
    payload: dict, generation_type: VideoGenerationType | None
) -> tuple[VideoGenerationType, tuple[str, str]] | None:
    """读请求 payload 里显式的视频执行身份（桶键 ``video_provider_<generation_type>``），连同命中的桶一并返回。

    值是 ``ProviderModel.pair_key`` 形态的复合值。正常队列执行会先移除 enqueue payload
    中的这类旧键，再按当前配置求值。``generation_type`` 未声明时按固定桶序
    扫两个桶键——至多命中一个，桶序不产生歧义。

    provider 不可信（见 ``_trusted_payload_provider``）不在此丢弃：视频侧身份可用性由
    ``_ensure_video_identity_resolvable`` 收口，供应商已下线时该报错，回退等于换供应商执行。
    """
    candidates: tuple[VideoGenerationType, ...] = (
        (generation_type,) if generation_type is not None else get_args(VideoGenerationType)
    )
    for candidate in candidates:
        pair = _split_pair(payload.get(f"video_provider_{candidate}"))
        if pair is not None:
            return candidate, pair
    return None


def caps_generation_mode(project: dict | None) -> str | None:
    """能力查询口径的 generation_mode：直读项目字段，无项目上下文时为 None。

    生成模式创建即定、整个项目按同一条路径生成，能力解析因此不需要剧集上下文：定桶、声音
    一致性、以及下游按 caps ``generation_mode`` 求值的分辨率与参考图约束全部按项目生成模式定轴。

    返回 None 而非默认档，是因为 ``generation_mode`` 是 caps 的对外字段（回前端与 Agent）：
    无项目上下文（provider 目录查询等）时「未声明」不该渲染成用户显式选过某种生成模式。
    """
    if project is None:
        return None
    mode = project.get("generation_mode")
    return mode if isinstance(mode, str) and mode else None


def project_video_backend_ids(
    project: dict, *, generation_type: VideoGenerationType | None = None
) -> tuple[str, str] | None:
    """project.json 自报的视频模型身份：取给定桶（缺省按 generation_mode 定桶）的桶键，缺则取项目默认键。

    纯读 project.json、不查 DB，供没有配置库会话的同步路径（归档导入按自报身份查 registry 档位）
    使用：桶键与默认键都在同一个明文文件里，不该把桶口径降成项目默认层——否则配了
    ``video_provider_r2v`` 的参考生视频项目会拿 ``video_backend`` 的档位取档。只认 ``provider/model``：
    裸供应商跳过、继续读下一个键，不报错；``_resolve_layered_backend`` 的项目层遇到裸内置供应商则报错。
    """
    bucket = generation_type or video_bucket_for_generation_mode(project.get("generation_mode"))
    keys = _VIDEO_LAYERED_KEYS[bucket]
    for key in (keys.project_bucket_key, keys.project_default_key):
        if key is None:
            continue
        parsed = _parse_project_provider(project.get(key))
        if parsed is not None:
            return parsed
    return None


def video_capability_satisfied(
    *,
    generation_type: VideoGenerationType,
    first_frame: bool,
    max_reference_images: int | None,
    text_to_video: bool = True,
    has_image: bool = True,
) -> bool:
    """检查请求对应的任务类型桶；i2v 桶内无首帧时检查文生路径。"""
    unmet = video_bucket_unmet(first_frame if has_image else text_to_video, InputLimit.from_json(max_reference_images))
    return generation_type not in unmet


TextTierKey = Literal["simple", "complex"]

_TEXT_TIER_SETTING_KEYS: dict[TextTierKey, str] = {
    "simple": "text_backend_simple",
    "complex": "text_backend_complex",
}


_TEXT_LAYERED_KEYS: dict[TextTierKey, _LayeredBackendKeys] = {
    tier: _LayeredBackendKeys(
        media_type="text",
        project_bucket_key=tier_key,
        project_default_key="default_text_backend",
        global_bucket_key=tier_key,
        global_default_key="default_text_backend",
    )
    for tier, tier_key in _TEXT_TIER_SETTING_KEYS.items()
}


AutoResolvePurpose = Literal["t2i", "i2i", "i2v", "r2v", "text", "audio"]


VoiceConsistency = Literal["native", "soft", "none"]


def derive_voice_consistency(
    *,
    reference_audio_mode: str,
    generation_mode: str | None,
    has_audio: bool,
    character_voice_binding: CharacterVoiceBinding | None,
) -> VoiceConsistency:
    """三级声音一致性标识派生（native / soft / none）：模型能力 × 生效 generation_mode × 绑定方式。

    全仓库唯一派生点：项目内场景经 `_resolve_video_caps_for_model` 走这里，无项目上下文的
    目录场景由 `server/routers/providers.py` 以 ``generation_mode=None`` /
    ``character_voice_binding=None`` 调同一函数，前端不复制第二份公式。

    ``reference_audio_mode`` 按字面量比较（``ReferenceAudioMode`` 是 ``StrEnum``，两者可
    直接 ``==``），不在 lib.config 层导入 lib.backends.video_backends（分层契约，config 是最底层）。

    native 蕴含有音轨：generation_mode 非参考生视频、或项目选的是提示词软约束
    （``character_voice_binding == "prompt"``，默认档）时一律降格 soft，不降到 none。降格是全链路
    的总闸：脚本预览的参考音频类 warning、提示词里的 ``@音频N`` 编号与执行期的参考音频挂线都只认
    ``native``，因此绑定方式不必再沿渲染链逐层传递。``has_audio`` 问的是「成片有没有音轨」而非
    「开关可不可控」，故恒有声（开关不可控）的型号同样算有音轨；调用方由
    :func:`builtin_video_audio_track` 得出该位，不各自解读一份声明。
    """
    if (
        reference_audio_mode == "direct"
        and generation_mode == "reference_video"
        and character_voice_binding == "reference_audio"
    ):
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


def reference_limits_from_config(cfg: Mapping[str, str | None]) -> tuple[int, int]:
    total = _parse_int(cfg.get(_REFERENCE_TOTAL_MAX_BYTES_KEY), DEFAULT_REFERENCE_TOTAL_MAX_BYTES)
    single = _parse_int(cfg.get(_REFERENCE_SINGLE_MAX_BYTES_KEY), DEFAULT_REFERENCE_SINGLE_MAX_BYTES)
    return total, single


def project_resolution(project: dict, provider_id: str, model_id: str) -> str | None:
    """project.model_settings（``provider/model`` 复合 key）> legacy video_model_settings > None。

    逐层用 ``_safe_dict`` 防御非 dict 中间层（见 ``_safe_dict`` docstring）；叶子值同样按
    非空字符串收口并去空白——下游 ``constrain_durations`` 对它做 ``.strip().lower()``，手改
    project.json 或历史脏数据留下的 int / list / dict 会在那里抛 ``AttributeError``。非字符串
    与空白串一律按「未配置」继续回退，与写入侧的档位语义一致。
    """
    key = f"{provider_id}/{model_id}"
    model_settings = _safe_dict(project.get("model_settings"), field="model_settings")
    override = _safe_dict(model_settings.get(key), field=f"model_settings.{key}").get("resolution")
    if cleaned := _clean_resolution(override, field=f"model_settings.{key}.resolution"):
        return cleaned
    video_model_settings = _safe_dict(project.get("video_model_settings"), field="video_model_settings")
    legacy = _safe_dict(video_model_settings.get(model_id), field=f"video_model_settings.{model_id}").get("resolution")
    return _clean_resolution(legacy, field=f"video_model_settings.{model_id}.resolution")


def project_image_quality(project: dict, provider_id: str, model_id: str) -> str | None:
    """项目为这个图片执行模型选择的画质（``model_settings["<供应商>/<模型>"].quality``）；未选择为 ``None``。

    键规则与分辨率相同（docs/adr/0054）。只读项目这一层，没有 legacy 表与自定义供应商默认值的回落；清洗同
    :func:`_clean_resolution`：非字符串与空白串按未选择，去首尾空白，不做大小写归一。存值是否生效由
    :func:`lib.backends.image_capabilities.effective_image_quality` 按模型的有效画质可选值判定。
    """
    key = f"{provider_id}/{model_id}"
    model_settings = _safe_dict(project.get("model_settings"), field="model_settings")
    value = _safe_dict(model_settings.get(key), field=f"model_settings.{key}").get("quality")
    return _clean_resolution(value, field=f"model_settings.{key}.quality", setting="quality")


def _clean_resolution(value: object, *, field: str, setting: str = "resolution") -> str | None:
    """非空字符串 → 去空白后的值；其余（含非字符串）→ None。``setting`` 只用于告警文字。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        logger.warning("project %s is %s, expected str; treating %s as unset", field, type(value).__name__, setting)
        return None
    return value.strip() or None


def constrain_durations(
    provider_id: str | None,
    model_id: str | None,
    durations: list[int],
    *,
    resolution: str | None = None,
    uses_reference_images: bool = False,
    uses_end_frame: bool = False,
) -> list[int]:
    """按内置型号在目录上声明的「分辨率↔时长」「参考图↔时长」「尾帧↔时长」约束收窄候选。

    型号不在目录中时返回原候选。自定义供应商的约束经 :meth:`ConfigResolver.video_duration_constraints`
    取得后交给 :func:`apply_duration_constraints`。
    """
    return apply_duration_constraints(
        durations,
        builtin_duration_constraints(provider_id, model_id),
        resolution=resolution,
        uses_reference_images=uses_reference_images,
        uses_end_frame=uses_end_frame,
    )


def builtin_duration_constraints(provider_id: str | None, model_id: str | None) -> DurationConstraints | None:
    """内置型号在目录上声明的时长联动约束；型号不在目录中时为 None。"""
    model_info = catalog.get_model(provider_id, model_id) if provider_id and model_id else None
    if model_info is None:
        return None
    return DurationConstraints(
        by_resolution={res: tuple(allowed) for res, allowed in model_info.duration_resolution_constraints.items()},
        with_reference_images=tuple(model_info.reference_image_durations),
        with_end_frame=tuple(model_info.end_frame_durations),
    )


def apply_duration_constraints(
    durations: list[int],
    constraints: DurationConstraints | None,
    *,
    resolution: str | None = None,
    uses_reference_images: bool = False,
    uses_end_frame: bool = False,
) -> list[int]:
    """按时长联动约束收窄候选。

    三条约束各自独立触发、可同时生效，取交集。没有约束时返回原候选。交集为空时返回空集，由消费方按
    阶段处理。分辨率键按小写比较。
    """
    if not durations or constraints is None:
        return durations
    allowed = list(durations)
    if uses_reference_images and constraints.with_reference_images:
        allowed = [d for d in allowed if d in constraints.with_reference_images]
    if uses_end_frame and constraints.with_end_frame:
        allowed = [d for d in allowed if d in constraints.with_end_frame]
    by_resolution = constraints.by_resolution.get(resolution.strip().lower()) if resolution else None
    if by_resolution:
        allowed = [d for d in allowed if d in by_resolution]
    return allowed


DurationExclusionReason = Literal["resolution", "reference", "end_frame"]


DURATION_ENDPOINT_FIXED_REASON = "endpoint"


def duration_endpoint_fixed_reason(fixed: bool) -> str | None:
    """端点固定标志对应的成因：标志为真给 :data:`DURATION_ENDPOINT_FIXED_REASON`，否则 None。"""
    return DURATION_ENDPOINT_FIXED_REASON if fixed else None


class NoAvailableProviderError(ValueError):
    """各层都没有模型设置，也没有可用的自定义供应商默认模型。

    ``media_type`` 是缺默认模型的媒体类型。面向用户的入口按它给出「到默认模型设置里选择」的指引，须在把
    ``ValueError`` 笼统当作「未配置供应商」的宽捕获之前单独处理。
    """

    def __init__(self, message: str, *, media_type: str) -> None:
        super().__init__(message)
        self.media_type = media_type


class VisionCapabilityError(ValueError):
    """解析出的文本模型不支持图像输入（vision），无法执行需要 vision 的任务。

    携带结构化字段供调用方（如面向用户的 router）按需本地化；``str(exc)`` 是英文技术
    消息，供 log / 非用户可见路径直接使用。"""

    def __init__(self, *, task_type: object, provider_id: str, model_id: str):
        self.task_type: Any = task_type
        self.provider_id = provider_id
        self.model_id = model_id
        task_label = getattr(task_type, "value", str(task_type))
        super().__init__(
            f"text model {provider_id}/{model_id} does not support vision, cannot perform task {task_label}"
        )


class VideoBucketCapabilityError(ValueError):
    """视频解析闸报错：解析出的模型缺所属任务类型桶要求的能力，或配置引用已不可用。

    ``code`` 是 errors 目录 key、``params`` 是其渲染参数：router 可直接
    ``_t(exc.code, **exc.params)`` 本地化，worker 落库经 ``lib.generation.task_failure.encode_failure``
    结构化编码。``str(exc)`` 是英文技术消息，供 log / 非用户可见路径直接使用。"""

    def __init__(
        self,
        *,
        code: str,
        generation_type: VideoGenerationType,
        provider_id: str,
        model_id: str,
        message: str,
    ):
        self.code = code
        self.generation_type = generation_type
        self.provider_id = provider_id
        self.model_id = model_id
        self.params: dict[str, str] = {"provider": provider_id, "model": model_id}
        super().__init__(message)


class VideoSupportedDurationsError(ValueError):
    """能力合成时模型行的时长档位声明缺失（``missing``）。

    ``provider_id`` / ``model_id`` 是收敛后的有效身份。消费方按 ``kind`` 映射到各自路线的问题码，
    不解析 ``str(exc)``；消息文本保持原样，供 log 与仍按 ``ValueError`` 捕获的调用方使用。
    """

    kind: Literal["missing"]

    def __init__(self, kind: Literal["missing"], *, provider_id: str, model_id: str, message: str):
        self.kind = kind
        self.provider_id = provider_id
        self.model_id = model_id
        super().__init__(message)


class ImageBucketCapabilityError(ValueError):
    """图片解析闸报错：解析出的模型缺所属任务类型桶（t2i / i2i）要求的能力。

    ``code`` 与执行层 ``lib.backends.image_backends.base.ImageCapabilityError`` 同一批
    （``image_capability_missing_<桶>``）：同一件事在解析期与执行期报出，读侧渲染与失败编码不分叉。
    ``params`` 是其渲染参数；``str(exc)`` 是英文技术消息，供 log / 非用户可见路径直接使用。
    """

    def __init__(self, *, generation_type: str, provider_id: str, model_id: str):
        self.code = f"image_capability_missing_{generation_type}"
        self.generation_type = generation_type
        self.provider_id = provider_id
        self.model_id = model_id
        self.params: dict[str, str] = {"provider": provider_id, "model": model_id}
        super().__init__(
            f"image model {provider_id}/{model_id} lacks the capability required by the {generation_type} bucket"
        )


def video_bucket_capability_missing(
    generation_type: VideoGenerationType, provider_id: str, model_id: str
) -> VideoBucketCapabilityError:
    return VideoBucketCapabilityError(
        code=f"video_capability_missing_{generation_type}",
        generation_type=generation_type,
        provider_id=provider_id,
        model_id=model_id,
        message=f"video model {provider_id}/{model_id} lacks the capability required by the {generation_type} bucket",
    )


def video_bucket_reference_unavailable(
    generation_type: VideoGenerationType, provider_id: str, model_id: str
) -> VideoBucketCapabilityError:
    return VideoBucketCapabilityError(
        code="video_capability_reference_unavailable",
        generation_type=generation_type,
        provider_id=provider_id,
        model_id=model_id,
        message=f"configured video model {provider_id}/{model_id} is no longer resolvable for the {generation_type} bucket",
    )


class _SnapshotConfigService(ConfigService):
    _settings: dict[str, str] | None = None

    async def get_all_settings(self) -> dict[str, str]:
        if self._settings is None:
            self._settings = await super().get_all_settings()
        return self._settings

    async def get_setting(self, key: str, default: str = "") -> str:
        return (await self.get_all_settings()).get(key, default)


class ConfigResolver:
    """运行时配置读取器。

    作为 ConfigService 的上层薄封装，只承载全局/项目设置、凭证与项目偏好读取，
    不承载跨内置与自定义供应商的目标选型、能力合成或项目能力投影。
    """

    _DEFAULT_VIDEO_GENERATE_AUDIO = True

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        _bound_session: AsyncSession | None = None,
        _snapshot_settings: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._bound_session = _bound_session
        self._bound_service = (
            _SnapshotConfigService(_bound_session) if _bound_session is not None and _snapshot_settings else None
        )

    @asynccontextmanager
    async def session(self, *, snapshot_settings: bool = False) -> AsyncGenerator[ConfigResolver]:
        """打开共享 session，返回绑定到该 session 的 ConfigResolver。"""
        if self._bound_session is not None:
            if snapshot_settings and self._bound_service is None:
                yield ConfigResolver(self._session_factory, _bound_session=self._bound_session, _snapshot_settings=True)
            else:
                yield self
        else:
            async with self._session_factory() as sess:
                yield ConfigResolver(self._session_factory, _bound_session=sess, _snapshot_settings=snapshot_settings)

    @asynccontextmanager
    async def _open_session(self) -> AsyncGenerator[tuple[AsyncSession, ConfigService]]:
        """获取 (session, ConfigService)，优先复用 bound session。"""
        if self._bound_session is not None:
            yield self._bound_session, self._bound_service or ConfigService(self._bound_session)
        else:
            async with self._session_factory() as session:
                yield session, ConfigService(session)

    async def video_generate_audio(self) -> bool:
        """读取全局 video_generate_audio 设置（默认值 True）。"""
        async with self._open_session() as (_session, svc):
            return await self._resolve_video_generate_audio_from_project(svc, None)

    async def video_generate_audio_for_project(self, project: dict | None) -> bool:
        """解析项目级 video_generate_audio 偏好：项目级覆盖 > 全局配置 > 默认值(True)。"""
        async with self._open_session() as (_session, svc):
            return await self._resolve_video_generate_audio_from_project(svc, project)

    async def default_text_backend(self) -> tuple[str, str]:
        """返回全局默认文本 (provider_id, model_id)。"""
        async with self._open_session() as (_session, svc):
            return await svc.get_default_text_backend()

    async def audio_model_profile(self, provider: str, model: str) -> AudioProfile:
        from lib.config.tts import builtin_audio_profile, model_profile

        if not is_custom_provider(provider):
            return builtin_audio_profile(provider, model)
        async with self._open_session() as (session, _):
            return await model_profile(session, provider, model)

    async def resolve_tts_synthesis_settings(
        self, project: dict, *, validate_profile: bool = True
    ) -> TtsSynthesisSettings:
        if project_narration_delivery(project) != USE_TTS or any(key in project for key in TTS_GROUP_FIELDS):
            settings = require_project_tts_generation(project)
        else:
            async with self._open_session() as (session, svc):
                saved = await svc.get_all_settings()
                backend = (saved.get("default_audio_backend") or "").strip()
                if not backend:
                    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

                    repo = CustomProviderRepository(session)
                    for row in await repo.list_enabled_models_by_media_type("audio"):
                        if row.is_default and await repo.get_provider(row.provider_id) is not None:
                            raise NarrationConfigError("narration_tts_voice_required")
                    raise NarrationConfigError("narration_tts_model_required")
                settings = require_project_tts_generation(
                    {
                        "narration_delivery": USE_TTS,
                        "audio_backend": backend,
                        "narration_voice": saved.get("narration_voice"),
                        "narration_speed": saved.get("narration_speed") or None,
                    }
                )
        if validate_profile:
            profile = await self.audio_model_profile(settings.provider_id, settings.model_id)
            profile.validate(settings.voice, settings.speed)
        return settings

    async def narration_defaults(self) -> tuple[str, float | None]:
        """返回全局默认旁白音色与配音语速。"""
        async with self._open_session() as (_session, svc):
            settings = await svc.get_all_settings()
            if not settings.get("default_audio_backend"):
                return "", None
            return settings.get("narration_voice", "").strip(), svc.parse_narration_speed(
                settings.get("narration_speed", "")
            )

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """获取单个供应商配置。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_provider_config(svc, session, provider_id)

    async def provider_config_with_credential(self, provider_id: str) -> tuple[dict[str, str], int | None]:
        """获取单个供应商配置，连同叠加进来的生效凭证记录 ID；没有生效凭证时 ID 为 None。

        两者在同一次读取里取得：素材库绑定按凭证记录与它的实际内容核对，分开读会在中途切换凭证时对不上。
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_provider_config_and_credential(svc, session, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """批量获取所有供应商配置。"""
        async with self._open_session() as (session, svc):
            return await self._resolve_all_provider_configs(svc, session)

    async def reference_payload_limits(self, provider_id: str | None) -> tuple[int, int]:
        """解析参考上传副本的 (total_max_bytes, single_max_bytes)。"""
        if provider_id is None:
            return DEFAULT_REFERENCE_TOTAL_MAX_BYTES, DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        async with self._open_session() as (session, svc):
            return await self._resolve_reference_payload_limits(svc, session, provider_id)

    async def configured_image_identity(
        self,
        project: dict | None,
        payload: dict | None,
        bucket: Literal["t2i", "i2i"],
    ) -> ProviderModel | None:
        if payload:
            provider_id = _trusted_payload_provider(payload.get("image_provider"))
            if provider_id is not None:
                model = _payload_model(payload.get("image_model"))
                if model is not None:
                    return ProviderModel(provider_id, model)
        async with self._open_session() as (_, svc):
            pair = await self._configured_backend_pair(
                svc, project, _IMAGE_LAYERED_KEYS[bucket], require_fallback_allowed=True
            )
            return ProviderModel(*pair) if pair else None

    async def configured_video_identity(
        self, project: dict | None, bucket: VideoGenerationType | None
    ) -> ProviderModel | None:
        keys = _VIDEO_LAYERED_KEYS[bucket] if bucket else _VIDEO_DEFAULT_LAYERED_KEYS
        async with self._open_session() as (_, svc):
            pair = await self._configured_backend_pair(svc, project, keys, require_fallback_allowed=True)
            return ProviderModel(*pair) if pair else None

    async def configured_text_identity(self, tier: TextTierKey, project: dict | None = None) -> ProviderModel | None:
        keys = _TEXT_LAYERED_KEYS[tier]
        async with self._open_session() as (_, svc):
            pair = await self._configured_backend_pair(svc, project, keys, require_fallback_allowed=True)
            return ProviderModel(*pair) if pair else None

    async def configured_audio_identity(self, project: dict | None, payload: dict | None) -> ProviderModel | None:
        if payload:
            frozen = payload.get("tts_settings")
            if isinstance(frozen, dict):
                settings = require_project_tts_generation({**frozen, "narration_delivery": USE_TTS})
                return ProviderModel(settings.provider_id, settings.model_id)
            provider_id = _trusted_payload_provider(payload.get("audio_provider"))
            if provider_id is not None:
                model = _payload_model(payload.get("audio_model"))
                if model is not None:
                    return ProviderModel(provider_id, model)
        async with self._open_session() as (_, svc):
            pair = await self._configured_backend_pair(svc, project, _AUDIO_LAYERED_KEYS, require_fallback_allowed=True)
            return ProviderModel(*pair) if pair else None

    async def require_auto_fallback(self, media_type: str) -> None:
        from lib.config.recommended_models import FROZEN_SETTING_KEY

        async with self._open_session() as (_, svc):
            self._require_auto_fallback(await svc.get_setting(FROZEN_SETTING_KEY, ""), media_type)

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
                value = _parse_bool(override) if isinstance(override, str) else bool(override)

        return value

    async def _configured_backend_pair(
        self,
        svc: ConfigService,
        project: dict | None,
        keys: _LayeredBackendKeys,
        *,
        require_fallback_allowed: bool = False,
    ) -> tuple[str, str] | None:
        if project:
            for project_key in (keys.project_bucket_key, keys.project_default_key):
                if project_key is None:
                    continue
                raw = project.get(project_key)
                provider = _bare_provider(raw)
                if provider is None:
                    pair = _split_pair(raw)
                    if pair is not None:
                        return pair
                    continue
                _reject_bare_builtin_provider(provider, keys.media_type)
        settings = await svc.get_all_settings()
        if keys.global_bucket_key is not None:
            raw = settings.get(keys.global_bucket_key, "")
            if "/" in raw:
                provider_id, model_id = raw.split("/", 1)
                return provider_id, model_id
        if keys.global_default_key is not None:
            raw = settings.get(keys.global_default_key, "")
            if "/" in raw:
                provider_id, model_id = raw.split("/", 1)
                return provider_id, model_id
        if require_fallback_allowed:
            from lib.config.recommended_models import FROZEN_SETTING_KEY

            self._require_auto_fallback(settings.get(FROZEN_SETTING_KEY, ""), keys.media_type)
        return None

    async def _resolve_provider_config(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> dict[str, str]:
        config, _credential_id = await self._resolve_provider_config_and_credential(svc, session, provider_id)
        return config

    async def _resolve_provider_config_and_credential(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> tuple[dict[str, str], int | None]:
        config = await svc.get_provider_config(provider_id)
        cred_repo = CredentialRepository(session)
        active = await cred_repo.get_active(provider_id)
        if active:
            await asyncio.to_thread(active.overlay_config, config)
        return config, active.id if active else None

    async def _resolve_reference_payload_limits(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> tuple[int, int]:
        try:
            cfg = await self._resolve_provider_config(svc, session, provider_id)
        except ValueError:
            return DEFAULT_REFERENCE_TOTAL_MAX_BYTES, DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        return reference_limits_from_config(cfg)

    async def _resolve_all_provider_configs(
        self,
        svc: ConfigService,
        session: AsyncSession,
    ) -> dict[str, dict[str, str]]:
        configs = await svc.get_all_provider_configs()
        cred_repo = CredentialRepository(session)
        active_creds = await cred_repo.get_active_credentials_bulk()

        def overlay_all() -> None:
            for provider_id, cred in active_creds.items():
                cred.overlay_config(configs.setdefault(provider_id, {}))

        await asyncio.to_thread(overlay_all)
        return configs

    @staticmethod
    def _require_auto_fallback(marker: str, media_type: str) -> None:
        from lib.config.recommended_models import auto_fallback_allowed

        if not auto_fallback_allowed(marker, media_type):
            raise NoAvailableProviderError(
                f"没有设置 {media_type} 默认模型。请在「全局设置 → 默认模型」中选择。",
                media_type=media_type,
            )
