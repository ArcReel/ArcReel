"""
自定义供应商管理 API。

提供自定义供应商 CRUD、模型管理、模型发现和连接测试端点。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from lib.api_errors import BadRequestError
from lib.config.repository import mask_secret
from lib.custom_provider import make_provider_id
from lib.custom_provider.capabilities import (
    AUDIO_OVERRIDE_KEYS,
    CAPABILITY_OVERRIDE_FIELDS,
    audio_capability_pair_is_coherent,
    capability_value_matches,
    filter_valid_overrides,
    resolve_audio_pair,
    strip_incoherent_audio_overrides,
    system_video_capabilities,
)
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    endpoint_spec_to_dict,
    endpoint_to_image_capabilities,
    endpoint_to_media_type,
    get_endpoint_spec,
)
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.i18n import Translator
from lib.image_backends.base import ImageCapability
from lib.video_backends.base import ReferenceAudioMode
from server.auth import CurrentUser


def _validate_endpoint(value: str) -> str:
    """Endpoint 校验：值必须存在于 ENDPOINT_REGISTRY，避免硬编码 Literal 漂移。"""
    if value not in ENDPOINT_REGISTRY:
        raise ValueError(f"unknown endpoint: {value!r}")
    return value


# 写入路径上的 endpoint 字段统一走运行时校验，键集合自动跟随 ENDPOINT_REGISTRY；
# 响应路径不需校验，直接 str。
EndpointType = Annotated[str, AfterValidator(_validate_endpoint)]
DiscoveryFormatLiteral = Literal["openai", "google"]

# 并发上限定型字段：可空正整数（≥1）；None = 未设置 → 容量装载回退全局默认。
MaxWorkers = Annotated[int | None, Field(default=None, ge=1)]

# 开放给用户覆盖的能力维度。DB 列与合成函数对 VideoCapabilities 全字段通用，写入侧在此收窄：
# 未列入的维度即便是合法字段名也不落库，扩容只需往这里加键名，无需 DB 迁移或改合成语义。
CAPABILITY_OVERRIDE_ALLOWLIST = frozenset({"last_frame", "reference_audio_mode", "max_reference_audio_count"})

# 白名单必须是 VideoCapabilities 字段名的子集：值类型校验直接按字段名取期望类型，键名写错
# 要在导入期炸掉，而不是等到一次真实写入才 KeyError 成 500。
if not CAPABILITY_OVERRIDE_ALLOWLIST <= CAPABILITY_OVERRIDE_FIELDS.keys():
    raise RuntimeError(
        f"能力覆盖白名单含非 VideoCapabilities 字段: "
        f"{sorted(CAPABILITY_OVERRIDE_ALLOWLIST - set(CAPABILITY_OVERRIDE_FIELDS))}"
    )


def _narrow_to_allowlist(overrides: dict[str, object]) -> dict[str, object]:
    """收窄至开放白名单。写入侧与回显侧共用，两处键集合同源而非各自推导后靠注释约定一致。"""
    return {k: v for k, v in overrides.items() if k in CAPABILITY_OVERRIDE_ALLOWLIST}


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["Custom Providers"])

_CONNECTION_TEST_TIMEOUT = 15  # 秒

# 全局 DB settings 中可能引用自定义供应商的键（删除 provider / 删除 model 时清理悬空引用）
_BACKEND_SETTING_KEYS = (
    "default_video_backend",
    "default_image_backend",
    "default_image_backend_t2i",
    "default_image_backend_i2i",
    "default_text_backend",
    "default_audio_backend",
    "text_backend_simple",
    "text_backend_complex",
)

# project.json 中的项目级覆盖键（与全局键名不同：resolver 按媒体读 video_backend /
# audio_backend / image_provider_*，文本档位键与项目默认模型键与全局同名），清理项目悬空引用时用此集合。
# 刻意不含视频桶键（video_provider_i2v / video_provider_r2v）：视频桶的悬空引用由解析闸
# _ensure_video_bucket_capability 报错兜底，写入侧不级联清理（docs/adr/0054）；图片桶无对应能力闸，
# 故仍在此清理
_PROJECT_BACKEND_KEYS = (
    "video_backend",
    "audio_backend",
    "image_provider_t2i",
    "image_provider_i2i",
    "default_image_backend",
    "text_backend_simple",
    "text_backend_complex",
    "default_text_backend",
)

# 全局 DB settings 中可能引用自定义模型的全部键，供能力编辑界面只读提示影响面用（`docs/adr/0054`：
# 提示不拦截保存）。与 _BACKEND_SETTING_KEYS 分开维护：后者界定删除时的级联清理范围，视频桶键按
# ADR 0054 不在清理之列，但仍需提示。每个键须在 frontend/src/i18n/*/dashboard.ts 有
# global_bucket_label_<key> 文案，由 test_global_bucket_keys_have_i18n_labels 守住。
_GLOBAL_BUCKET_REFERENCE_KEYS = (
    *_BACKEND_SETTING_KEYS,
    "default_video_backend_i2v",
    "default_video_backend_r2v",
)

# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class ModelInput(BaseModel):
    model_id: str
    display_name: str
    endpoint: EndpointType
    is_default: bool = False
    is_enabled: bool = True
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None
    supported_durations: list[int] | None = None
    resolution: str | None = None
    # 稀疏覆盖字典，键名对齐 VideoCapabilities 字段名；None 或键缺席 = 跟随系统判定。
    # 保存模型列表是整体替换语义，本字段必须随列表回传，否则存量覆盖被清空。
    capability_overrides: dict[str, object] | None = None

    @field_validator("capability_overrides")
    @classmethod
    def _keep_open_capabilities_only(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        """静默剔除开放白名单外的覆盖键，空字典收敛为 None。

        白名单外的键只能由手工改库产生：界面既不显示也没有入口能删，写入侧若为此报错，用户
        会被堵在一个自己无法处置的 422 上，连改显示名都保存不了。剔除即这类键的唯一出口，
        落库的覆盖字典因此只含当前开放的维度。剔除对用户静默，但落一条 warning：这是数据被
        丢弃的唯一痕迹，运维排查"我改的库值怎么没了"时需要它。
        """
        if not value:
            return None
        kept = _narrow_to_allowlist(value)
        if dropped := sorted(value.keys() - kept.keys()):
            logger.warning("能力覆盖含未开放键，保存时已剔除: %s", ", ".join(dropped))
        return kept or None

    def to_db_dict(self) -> dict:
        """返回适合写入数据库的字典（supported_durations 序列化为 JSON 字符串）。

        视频类 endpoint：supported_durations 缺省（None）或显式传 []（空列表，下游视为非法）时，
        统一归一为缺省并由 duration_presets 启发式填补。
        非视频类 endpoint 保持 None。
        """
        from lib.custom_provider.duration_presets import infer_supported_durations
        from lib.custom_provider.endpoints import endpoint_to_media_type

        d = self.model_dump()
        durations = self.supported_durations
        is_video = endpoint_to_media_type(self.endpoint) == "video"
        # video endpoint：把 [] 当作缺省（下游/前端都不接受空列表），交给 preset 兜底
        if is_video and durations is not None and len(durations) == 0:
            durations = None
        if durations is None and is_video:
            # endpoint 经 EndpointType 校验，值必在 ENDPOINT_REGISTRY 内，无需 ValueError 兜底
            durations = infer_supported_durations(self.model_id)
        d["supported_durations"] = json.dumps(durations) if durations is not None else None
        return d


class CreateProviderRequest(BaseModel):
    display_name: str
    discovery_format: DiscoveryFormatLiteral
    base_url: str
    api_key: str
    models: list[ModelInput] = []
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class UpdateProviderRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class FullUpdateProviderRequest(BaseModel):
    """PUT 全量更新：provider 元数据 + 模型列表在同一事务中。"""

    display_name: str
    base_url: str
    api_key: str | None = None  # None = 不修改
    models: list[ModelInput]
    # 并发上限随 PUT 全量提交（空输入 → None → 全局默认）；None 即清除，非"不修改"。
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class ProviderConnectionRequest(BaseModel):
    # 连接测试故意接受任意字符串，由 _run_connection_test 软失败返回 200 + success=False。
    discovery_format: str
    base_url: str
    api_key: str


class ReplaceModelsRequest(BaseModel):
    models: list[ModelInput]


class ModelResponse(BaseModel):
    id: int
    model_id: str
    display_name: str
    endpoint: str
    is_default: bool
    is_enabled: bool
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None
    supported_durations: list[int] | None = None
    resolution: str | None = None
    # 系统判定值（四字段全量），video endpoint 才有；非 video 或 endpoint 声明异常时为 None。
    system_capabilities: dict[str, object] | None = None
    # 用户覆盖（稀疏字典），与 system_capabilities 平凡合并即为生效值。
    capability_overrides: dict[str, object] | None = None
    # 正在引用该模型的全局 system_settings 键名（如 default_video_backend_i2v）；未被引用为
    # None。只查 DB 全局配置，不扫描项目文件（`docs/adr/0054`）；前端据此渲染非阻塞提示。
    global_bucket_refs: list[str] | None = None


class ProviderResponse(BaseModel):
    id: int
    display_name: str
    discovery_format: str
    base_url: str
    api_key_masked: str
    models: list[ModelResponse]
    created_at: str | None = None
    image_max_workers: int | None = None
    video_max_workers: int | None = None
    audio_max_workers: int | None = None


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class DiscoverResponse(BaseModel):
    models: list[dict]


class DiscoverAnthropicRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class CredentialsResponse(BaseModel):
    base_url: str
    api_key: str


class EndpointDescriptor(BaseModel):
    """前端从 catalog API 拿到的单条 endpoint 描述（与 lib.custom_provider.endpoints.EndpointSpec 对齐，去掉闭包）。"""

    key: str
    media_type: str
    family: str
    display_name_key: str
    request_method: str
    request_path_template: str
    image_capabilities: list[str] | None = None  # image 类填能力字符串列表，其他为 None
    # 该 endpoint 的执行层是否真的下传尾帧约束；仅 video 类有意义。前端据此收窄 last_frame
    # 覆盖控件里「强制开」的可选范围——否则用户只能撞上写入侧的 422 才知道这条路不通。
    end_image_capable: bool = False


class EndpointCatalogResponse(BaseModel):
    endpoints: list[EndpointDescriptor]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _system_capabilities_for(endpoint: str, model_id: str) -> dict[str, object] | None:
    """读该 model 的系统判定能力（四字段全量）；非 video endpoint 返回 None。

    判定失败（endpoint 已下线、注册表声明异常）时降级为 None 而非 500：列表端点要能把
    其余模型正常呈现出来，单行判定不出来只是设置页少一段"判定值"提示。
    """
    try:
        if endpoint_to_media_type(endpoint) != "video":
            return None
        return asdict(system_video_capabilities(endpoint=endpoint, model_id=model_id))
    except ValueError:
        logger.warning("无法判定系统能力: endpoint=%r model_id=%r", endpoint, model_id)
        return None


def _effective_overrides_for_response(
    endpoint: str, model_id: str, overrides: object | None
) -> dict[str, object] | None:
    """回显前按写入侧同一判定过滤，剔除执行层不会采用的键值，并收窄至开放白名单。

    存量行 / 非 API 写入可能留下已不兼容的覆盖（如 endpoint 不再 end_image_capable 后的
    last_frame=True）：原样回显会让界面显示"覆盖已生效"，但执行层其实静默忽略；且客户端
    普通保存时把它原样回传，会被写入侧白名单拒为 422，堵住与该覆盖无关的编辑。

    再经 :func:`_narrow_to_allowlist` 收窄：DB 遗留的白名单外键（同样只能来自手工改库）若原样
    回显，与写入落库的键集合不一致，调用方需要额外知道"回显可能含脏键但写回会被剔除"。收窄与
    写入侧走同一个函数，两处集合同源。

    音频两维另经 :func:`strip_incoherent_audio_overrides`：单维合法而合起来无意义的组合
    （direct ⊕ 上限 0）过得了 :func:`filter_valid_overrides`，执行层却会把它降到 ``none``，
    回显原值同样落进本函数要消灭的"界面显示已生效、执行其实忽略"。
    """
    filtered = _narrow_to_allowlist(filter_valid_overrides(endpoint=endpoint, model_id=model_id, overrides=overrides))
    filtered = strip_incoherent_audio_overrides(filtered, endpoint=endpoint, model_id=model_id)
    return filtered or None


def _extract_global_bucket_refs(all_settings: dict[str, str], provider_id: int) -> dict[str, list[str]]:
    """从一次性拉取的全局 settings 中挑出引用该 provider 模型的键，按 model_id 分组。"""
    prefix = f"{make_provider_id(provider_id)}/"
    refs: dict[str, list[str]] = {}
    for key in _GLOBAL_BUCKET_REFERENCE_KEYS:
        val = all_settings.get(key, "")
        if val and val.startswith(prefix):
            refs.setdefault(val[len(prefix) :], []).append(key)
    return refs


async def _global_bucket_refs_for_provider(session: AsyncSession, provider_id: int) -> dict[str, list[str]]:
    from lib.config.service import ConfigService

    all_settings = await ConfigService(session).get_all_settings()
    return _extract_global_bucket_refs(all_settings, provider_id)


def _model_to_response(m, global_bucket_refs: list[str] | None = None) -> ModelResponse:
    durations = json.loads(m.supported_durations) if m.supported_durations else None
    return ModelResponse(
        system_capabilities=_system_capabilities_for(m.endpoint, m.model_id),
        capability_overrides=_effective_overrides_for_response(m.endpoint, m.model_id, m.capability_overrides),
        id=m.id,
        model_id=m.model_id,
        display_name=m.display_name,
        endpoint=m.endpoint,
        is_default=m.is_default,
        is_enabled=m.is_enabled,
        price_unit=m.price_unit,
        price_input=m.price_input,
        price_output=m.price_output,
        currency=m.currency,
        supported_durations=durations,
        resolution=m.resolution,
        global_bucket_refs=global_bucket_refs or None,
    )


def _provider_to_response(provider, models, global_bucket_refs: dict[str, list[str]] | None = None) -> ProviderResponse:
    refs = global_bucket_refs or {}
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
        discovery_format=provider.discovery_format,
        base_url=provider.base_url,
        api_key_masked=mask_secret(provider.api_key),
        models=[_model_to_response(m, refs.get(m.model_id)) for m in models],
        created_at=dt_to_iso(provider.created_at),
        image_max_workers=provider.image_max_workers,
        video_max_workers=provider.video_max_workers,
        audio_max_workers=provider.audio_max_workers,
    )


def _cleanup_project_refs(prefix: str, setting_keys: tuple[str, ...]) -> None:
    """删除 provider 后，清理所有项目 project.json 中的悬空引用。"""
    from lib.config.resolver import get_project_manager

    pm = get_project_manager()
    for proj_name in pm.list_projects():
        try:

            def _mutate(p: dict, _prefix=prefix, _keys=setting_keys) -> None:
                for key in _keys:
                    val = p.get(key, "")
                    if isinstance(val, str) and val.startswith(_prefix):
                        p.pop(key, None)

            pm.update_project(proj_name, _mutate)
        except Exception:
            pass  # 读取失败或项目不可写，跳过（非致命）


def _check_duplicate_model_ids(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """校验模型列表：无重复 model_id；启用模型有合法 model_id 和 endpoint；价格组合自洽。"""
    seen: set[str] = set()
    for m in models:
        if m.is_enabled and not m.model_id.strip():
            raise HTTPException(status_code=422, detail=_t("model_id_required"))
        if m.is_enabled and not m.endpoint:
            raise HTTPException(status_code=422, detail=_t("endpoint_required"))
        if m.price_output is not None and m.price_input is None:
            raise HTTPException(status_code=422, detail=_t("price_input_required"))
        if m.model_id in seen:
            raise HTTPException(status_code=422, detail=_t("duplicate_model_id", model_id=m.model_id))
        if m.model_id:
            seen.add(m.model_id)


def _check_capability_overrides(
    overrides: dict[str, object] | None,
    endpoint: str,
    model_id: str,
    _t: Callable[..., str],
) -> None:
    """写入侧校验：合成函数对脏值只降级不抛，合法性把关全在这里。

    None 与空字典都表示"全部跟随系统判定"，一律放行。键集合已由 ``ModelInput`` 收窄到开放
    白名单内，此处校验其余维度：非空覆盖要求 endpoint 为 video 类，值类型与该能力字段一致。
    """
    if not overrides:
        return
    if endpoint_to_media_type(endpoint) != "video":
        raise HTTPException(
            status_code=422,
            detail=_t("capability_overrides_video_only", model_id=model_id, endpoint=endpoint),
        )
    for key, value in overrides.items():
        expected = CAPABILITY_OVERRIDE_FIELDS[key]
        # 值类型判定复用合成层的同一函数：两边各写一份会漂移，届时写入侧放行的值被合成
        # 静默忽略，正是本能力覆盖链路要消灭的「界面允许、执行反悔」。
        if not capability_value_matches(value, expected):
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_invalid_value",
                    model_id=model_id,
                    capability=key,
                    expected=expected.__name__,
                ),
            )
        # last_frame 覆盖为 True 时，endpoint 的 delegate.generate() 必须真的会读取
        # end_image 下传尾帧约束——否则覆盖只是让合成层宣称支持，执行层仍静默生成无约束视频。
        if key == "last_frame" and value is True and not get_endpoint_spec(endpoint).end_image_capable:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_last_frame_unsupported",
                    model_id=model_id,
                    endpoint=endpoint,
                ),
            )
        # 同构：把音色模式覆盖成 direct 要求 endpoint 真的会下传 reference_audio_files。
        if (
            key == "reference_audio_mode"
            and value == ReferenceAudioMode.DIRECT.value
            and not get_endpoint_spec(endpoint).reference_audio_capable
        ):
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_reference_audio_unsupported",
                    model_id=model_id,
                    endpoint=endpoint,
                ),
            )

    # 合并后不变式：两维各自合法、合起来无意义的组合（支持音色输入却限 0 段）在这里拒，
    # 而不是留给执行期静默降级——降级后用户会拿到「最多支持 0 段」这种无从遵循的提示。
    # 稀疏覆盖只写其中一维也能凑出该组合，故按系统判定补齐未覆盖的那一维再判。
    if any(key in overrides for key in AUDIO_OVERRIDE_KEYS):
        mode, count = resolve_audio_pair(overrides, endpoint=endpoint, model_id=model_id)
        if not audio_capability_pair_is_coherent(mode=mode, count=count):
            raise HTTPException(
                status_code=422,
                detail=_t("capability_override_audio_pair_incoherent", model_id=model_id),
            )


def _check_model_capability_overrides(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """对整批模型逐个跑覆盖校验（保存模型列表的写入路径，设置页表单的覆盖编辑也走这里）。

    每行都按提交上来的 ``(endpoint, 覆盖值)`` 校验：覆盖是否合法随 endpoint 变化
    （last_frame=True 要求 endpoint 的 end_image_capable，非 video endpoint 直接拒绝非空覆盖），
    故覆盖字典原样不动、只切 endpoint 的整表 PUT 同样要按新 endpoint 重新判定。开放白名单外的
    键不会走到这里——``ModelInput`` 已在解析期把它们剔除。
    """
    for m in models:
        _check_capability_overrides(m.capability_overrides, m.endpoint, m.model_id, _t)


def _check_unique_defaults(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """校验默认模型互斥。

    - 非 image endpoint（text / video / audio）：同一 media_type 至多 1 个 is_default=True。
    - image endpoint：image capability 集合两两不相交（即同一 capability 至多 1 个默认）。
    """
    text_video_defaults: dict[str, list[str]] = {}
    image_defaults: list[tuple[str, frozenset[ImageCapability]]] = []
    for m in models:
        if not m.is_default:
            continue
        try:
            mt = endpoint_to_media_type(m.endpoint)
        except ValueError:
            continue  # endpoint 已在 ModelInput validator 校验，此处跳过未知值
        if mt != "image":
            text_video_defaults.setdefault(mt, []).append(m.model_id)
            continue
        try:
            caps = endpoint_to_image_capabilities(m.endpoint)
        except ValueError:
            continue
        image_defaults.append((m.model_id, caps))

    duplicates: dict[str, list[str]] = {}
    for mt, ids in text_video_defaults.items():
        if len(ids) > 1:
            duplicates[mt] = ids

    # image：按 capability 反向索引，任一槽位有 >1 个默认即视为冲突（O(n) 替代 O(n²) 两两 caps 求交）
    cap_to_ids: dict[ImageCapability, list[str]] = {}
    for mid, caps in image_defaults:
        for c in caps:
            cap_to_ids.setdefault(c, []).append(mid)
    conflict_ids = [mid for ids in cap_to_ids.values() if len(ids) > 1 for mid in ids]
    if conflict_ids:
        duplicates["image"] = list(dict.fromkeys(conflict_ids))

    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=_t("default_model_conflict", conflict="; ".join(parts)),
        )


async def _invalidate_caches(request: Request) -> None:
    """清空 backend 实例缓存 + 刷新 worker 限流配置。"""
    from server.services.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_providers(
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """列出所有自定义供应商（含模型列表）。"""
    repo = CustomProviderRepository(session)
    pairs = await repo.list_providers_with_models()
    from lib.config.service import ConfigService

    all_settings = await ConfigService(session).get_all_settings()
    return {
        "providers": [
            _provider_to_response(p, models, _extract_global_bucket_refs(all_settings, p.id)) for p, models in pairs
        ]
    }


# /endpoints 必须先于 /{provider_id} 注册，否则 FastAPI 会把字符串 "endpoints" 当作 provider_id。
@router.get("/endpoints", response_model=EndpointCatalogResponse)
async def list_endpoint_catalog(_user: CurrentUser) -> EndpointCatalogResponse:
    """暴露 ENDPOINT_REGISTRY 作为前端单一真相源：渲染下拉、显示路径与分组都派生自此返回值。"""
    return EndpointCatalogResponse(
        endpoints=[EndpointDescriptor(**endpoint_spec_to_dict(spec)) for spec in ENDPOINT_REGISTRY.values()],
    )


@router.post("", status_code=201)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """创建自定义供应商，可同时创建模型列表。"""
    if body.models:
        _check_duplicate_model_ids(body.models, _t)
        _check_unique_defaults(body.models, _t)
        _check_model_capability_overrides(body.models, _t)
    repo = CustomProviderRepository(session)
    model_dicts = [m.to_db_dict() for m in body.models] if body.models else None
    provider = await repo.create_provider(
        display_name=body.display_name,
        discovery_format=body.discovery_format,
        base_url=body.base_url,
        api_key=body.api_key,
        models=model_dicts,
        image_max_workers=body.image_max_workers,
        video_max_workers=body.video_max_workers,
        audio_max_workers=body.audio_max_workers,
    )
    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider.id)
    return _provider_to_response(provider, models, await _global_bucket_refs_for_provider(session, provider.id))


@router.get("/{provider_id}")
async def get_provider(
    provider_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """获取单个自定义供应商详情。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models, await _global_bucket_refs_for_provider(session, provider_id))


@router.get("/{provider_id}/credentials", response_model=CredentialsResponse)
async def get_provider_credentials(
    provider_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """返回明文 base_url + api_key，供智能体配置导入复用。

    仅 CurrentUser 鉴权,与现有 PATCH 接口对齐;日志不打印 body。
    多用户场景需重新评估细粒度授权。
    """
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return CredentialsResponse(
        base_url=provider.base_url or "",
        api_key=provider.api_key or "",
    )


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    body: UpdateProviderRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """更新自定义供应商配置。"""
    repo = CustomProviderRepository(session)
    kwargs = {}
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.base_url is not None:
        kwargs["base_url"] = body.base_url
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key

    if not kwargs:
        raise HTTPException(status_code=400, detail=_t("at_least_one_field_required"))

    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))

    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models, await _global_bucket_refs_for_provider(session, provider_id))


@router.put("/{provider_id}")
async def full_update_provider(
    provider_id: int,
    body: FullUpdateProviderRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """原子更新供应商元数据 + 模型列表（单一事务）。"""
    _check_duplicate_model_ids(body.models, _t)
    _check_unique_defaults(body.models, _t)
    _check_model_capability_overrides(body.models, _t)
    repo = CustomProviderRepository(session)
    kwargs: dict = {
        "display_name": body.display_name,
        "base_url": body.base_url,
        # PUT 为并发上限的权威来源：始终写入（含 None 清除），不做"仅非空更新"
        "image_max_workers": body.image_max_workers,
        "video_max_workers": body.video_max_workers,
        "audio_max_workers": body.audio_max_workers,
    }
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    model_dicts = [m.to_db_dict() for m in body.models]
    await repo.replace_models(provider_id, model_dicts)
    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models, await _global_bucket_refs_for_provider(session, provider_id))


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """删除自定义供应商（级联删除模型，清理悬空默认配置）。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    prefix = f"{make_provider_id(provider_id)}/"
    await repo.delete_provider(provider_id)
    # 清理引用该 provider 的全局默认 backend 配置
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    for key in _BACKEND_SETTING_KEYS:
        val = await svc.get_setting(key, "")
        if val and val.startswith(prefix):
            await svc.set_setting(key, "")
    await session.commit()
    await _invalidate_caches(request)
    # 清理引用该 provider 的项目级配置（同步文件 I/O，放到线程池避免阻塞事件循环）
    await asyncio.to_thread(_cleanup_project_refs, prefix, _PROJECT_BACKEND_KEYS)


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


@router.put("/{provider_id}/models")
async def replace_models(
    provider_id: int,
    body: ReplaceModelsRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """替换供应商的整个模型列表。"""
    _check_duplicate_model_ids(body.models, _t)
    _check_unique_defaults(body.models, _t)
    _check_model_capability_overrides(body.models, _t)
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    # 记录旧模型 ID，用于清理悬空引用
    old_model_ids = {m.model_id for m in await repo.list_models(provider_id)}
    new_model_ids = {m.model_id for m in body.models}
    deleted_model_ids = old_model_ids - new_model_ids

    model_dicts = [m.to_db_dict() for m in body.models]
    new_models = await repo.replace_models(provider_id, model_dicts)

    # 清理引用已删除模型的全局配置
    if deleted_model_ids:
        from lib.config.service import ConfigService

        svc = ConfigService(session)
        prefix = f"{make_provider_id(provider_id)}/"
        for key in _BACKEND_SETTING_KEYS:
            val = await svc.get_setting(key, "")
            if val and val.startswith(prefix):
                _, model_part = val.split("/", 1)
                if model_part in deleted_model_ids:
                    await svc.set_setting(key, "")

    await session.commit()
    await _invalidate_caches(request)
    refs = await _global_bucket_refs_for_provider(session, provider_id)
    return [_model_to_response(m, refs.get(m.model_id)) for m in new_models]


# ---------------------------------------------------------------------------
# 无状态操作
# ---------------------------------------------------------------------------


@router.post("/discover")
async def discover_models_endpoint(
    body: ProviderConnectionRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """模型发现：根据 discovery_format + base_url + api_key 查询可用模型。"""
    return await _run_discover(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/discover-anthropic", response_model=DiscoverResponse)
async def discover_anthropic_models_endpoint(
    body: DiscoverAnthropicRequest,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Anthropic 协议模型发现：智能体配置专用。

    凭据缺失时 fallback 到 active credential（AgentCredentialRepository）。
    """
    body_key = (body.api_key or "").strip()
    needs_key = not body_key
    needs_url = body.base_url is None

    cred = None
    if needs_key or needs_url:
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        cred = await AgentCredentialRepository(session).get_active()

    api_key = body_key if not needs_key else (cred.api_key if cred else "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=_t("anthropic_discovery_no_key"))

    base_url = body.base_url if not needs_url else (cred.base_url if cred else None)

    return await _run_discover("anthropic", base_url, api_key, _t)


@router.post("/{provider_id}/discover")
async def discover_models_by_id(
    provider_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """使用已存储凭证发现指定供应商的可用模型。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_discover(provider.discovery_format, provider.base_url, provider.api_key, _t)


@router.post("/test")
async def test_connection(
    body: ProviderConnectionRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """连接测试：验证 discovery_format + base_url + api_key 的连通性。"""
    return await _run_connection_test(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/{provider_id}/test")
async def test_connection_by_id(
    provider_id: int, _user: CurrentUser, _t: Translator, session: AsyncSession = Depends(get_async_session)
):
    """使用已存储凭证测试指定供应商的连通性。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_connection_test(provider.discovery_format, provider.base_url, provider.api_key, _t)


async def _run_discover(
    discovery_format: str, base_url: str | None, api_key: str, _t: Callable[..., str]
) -> DiscoverResponse:
    """共用的模型发现逻辑（明文凭证 / 已存储凭证两条入口共用）。"""
    from lib.custom_provider.discovery import UnsupportedDiscoveryFormatError, discover_models

    try:
        models = await discover_models(
            discovery_format=discovery_format,
            base_url=base_url or None,
            api_key=api_key,
        )
        return DiscoverResponse(models=models)
    except UnsupportedDiscoveryFormatError as exc:
        raise BadRequestError("invalid_discovery_format", discovery_format=discovery_format) from exc
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("模型发现失败: %s", err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg))


async def _run_connection_test(
    discovery_format: str, base_url: str, api_key: str, _t: Callable[..., str]
) -> ConnectionTestResponse:
    """共用的连接测试逻辑。"""
    try:
        if discovery_format == "openai":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_openai, base_url, api_key, _t),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        elif discovery_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_google, base_url, api_key, _t),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        else:
            return ConnectionTestResponse(
                success=False,
                message=_t("unsupported_discovery_format", discovery_format=discovery_format),
            )
        return result
    except TimeoutError:
        return ConnectionTestResponse(
            success=False,
            message=_t("connection_timeout"),
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连接测试失败 [%s]: %s", discovery_format, err_msg)
        return ConnectionTestResponse(
            success=False,
            message=_t("connection_failed", err_msg=err_msg),
        )


def _test_openai(base_url: str, api_key: str, _t: Callable[..., str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 OpenAI 兼容 API。"""
    from openai import OpenAI

    from lib.config.url_utils import ensure_openai_base_url

    client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
    models = client.models.list()
    count = sum(1 for _ in models)
    return ConnectionTestResponse(
        success=True,
        message=_t("connection_success"),
        model_count=count,
    )


def _test_google(base_url: str, api_key: str, _t: Callable[..., str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 Google genai API。"""
    from google import genai

    from lib.config.url_utils import ensure_google_base_url

    effective_url = ensure_google_base_url(base_url)
    http_options = {"base_url": effective_url} if effective_url else None
    client = genai.Client(api_key=api_key, http_options=http_options)  # type: ignore[arg-type]
    pager = client.models.list()
    count = sum(1 for _ in pager)
    return ConnectionTestResponse(
        success=True,
        message=_t("connection_success"),
        model_count=count,
    )
