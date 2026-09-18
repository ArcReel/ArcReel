"""
自定义供应商管理 API。

提供自定义供应商 CRUD、模型管理、模型发现和连通性检查端点。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from lib.api_errors import BadRequestError
from lib.config.repository import mask_secret
from lib.custom_provider import is_custom_endpoint, make_provider_id
from lib.custom_provider.capabilities import (
    AUDIO_OVERRIDE_KEYS,
    CAPABILITY_OVERRIDE_FIELDS,
    capability_type_name,
    capability_value_matches,
    filter_valid_overrides,
    resolve_audio_pair,
    strip_incoherent_audio_overrides,
    system_video_capabilities,
)
from lib.custom_provider.discovery_formats import endpoint_attachment_holds, is_comfyui_protocol
from lib.custom_provider.endpoint_definition import COMFYUI_KIND
from lib.custom_provider.endpoint_resolution import endpoint_spec_from_row, resolve_endpoint_spec
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    EndpointSpec,
    endpoint_spec_to_dict,
    get_endpoint_spec,
    static_media_type,
)
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.http_status_errors import raise_for_status_redacted
from lib.httpx_shared import get_http_client
from lib.i18n import Translator
from lib.image_backends.base import ImageCapability
from lib.video_backends.base import ReferenceAudioMode, audio_capability_pair_is_coherent


def _validate_endpoint(value: str) -> str:
    """Pydantic 层校验内置键或合法的自定义端点键形状；存在性由写入事务异步确认。"""
    if value not in ENDPOINT_REGISTRY and not is_custom_endpoint(value):
        raise ValueError(f"unknown endpoint: {value!r}")
    return value


# 写入路径上的 endpoint 字段统一走运行时校验，键集合自动跟随 ENDPOINT_REGISTRY；
# 响应路径不需校验，直接 str。
EndpointType = Annotated[str, AfterValidator(_validate_endpoint)]
DiscoveryFormatLiteral = Literal["openai", "google", "comfyui"]

# 并发上限定型字段：可空正整数（≥1）；None = 未设置 → 容量装载回退全局默认。
MaxWorkers = Annotated[int | None, Field(default=None, ge=1)]

# 开放给用户覆盖的能力维度。DB 列与合成函数对 VideoCapabilities 全字段通用，写入侧在此收窄：
# 未列入的维度即便是合法字段名也不落库，扩容只需往这里加键名，无需 DB 迁移或改合成语义。
#
# 音轨形态（audio_track / reference_route_audio_track）刻意不开放（``docs/adr/0054``）：自定义
# 供应商的音轨按「无信号不收紧」处理，设置界面拿不到自定义模型的逐模型音轨目录，用户设的这一位
# 没有执行侧对应物——协议 backend 要么下发音轨开关要么不下发，声明改不了它——开放覆盖只会请回
# 一份「界面宣称、执行期反悔」的手写声明。
CAPABILITY_OVERRIDE_ALLOWLIST = frozenset({"last_frame", "reference_audio_mode", "max_reference_audio_count"})

#: 音轨形态两维：既不开放覆盖，也不回显系统判定（理由同上）。
_AUDIO_TRACK_FIELDS = frozenset({"audio_track", "reference_route_audio_track"})

# 两份键集合都必须是 VideoCapabilities 字段名的子集：值类型校验直接按字段名取期望类型，键名写错
# 要在导入期炸掉，而不是等到一次真实写入才 KeyError 成 500；音轨两维的键名写错则会静默停止过滤，
# 把 backend 侧声明摆回自定义供应商的设置页——那正是本口径要避免的分裂。
for _name, _keys in (
    ("能力覆盖白名单", CAPABILITY_OVERRIDE_ALLOWLIST),
    ("音轨形态字段", _AUDIO_TRACK_FIELDS),
):
    if not _keys <= CAPABILITY_OVERRIDE_FIELDS.keys():
        raise RuntimeError(f"{_name}含非 VideoCapabilities 字段: {sorted(_keys - set(CAPABILITY_OVERRIDE_FIELDS))}")


def _narrow_to_allowlist(overrides: dict[str, object]) -> dict[str, object]:
    """收窄至开放白名单。写入侧与回显侧共用，两处键集合同源而非各自推导后靠注释约定一致。"""
    return {k: v for k, v in overrides.items() if k in CAPABILITY_OVERRIDE_ALLOWLIST}


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["Custom Providers"])

_CONNECTIVITY_CHECK_TIMEOUT = 15  # 秒

#: ComfyUI 的连通性探针路径。零鉴权的本体上它也可匿名访问，故它同时是「地址对不对」的判据。
_COMFYUI_SYSTEM_STATS_PATH = "/system_stats"

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

    def to_db_dict(self, endpoint_spec: EndpointSpec) -> dict:
        """返回适合写入数据库的字典（supported_durations 序列化为 JSON 字符串）。

        视频类 endpoint：supported_durations 缺省（None）或显式传 []（空列表，下游视为非法）时，
        统一归一为缺省并由 duration_presets 启发式填补。
        非视频类 endpoint 保持 None。

        ComfyUI 端点不走那套启发式，也不把空集当缺省：时长这一维由节点绑定决定（``docs/adr/0082``）。
        ``frames`` 未绑定或读不到帧率来源时这份 workflow 的时长根本不由 ArcReel 驱动，服务端把该
        模型行的档位钉死为空集——用户在界面上改不动它，改了也无处生效；两者齐备时默认只含 workflow
        的原生时长，用户可在模型行里增删。模型名在这条通道上与 workflow 能出多长毫无关系。

        媒体类型读调用方已解析好的 spec：``ce-`` 端点的媒体类型写在它那份定义里，键前缀推不出来。
        """
        from lib.custom_provider.comfyui.capabilities import default_supported_durations
        from lib.custom_provider.duration_presets import infer_supported_durations

        d = self.model_dump()
        durations = self.supported_durations
        is_video = endpoint_spec.media_type == "video"
        definition = endpoint_spec.definition
        if is_video and definition is not None and endpoint_spec.kind == COMFYUI_KIND:
            # 默认集为空 = 这份 workflow 的时长不由 ArcReel 驱动，用户传什么都钉回空集。
            default = default_supported_durations(definition)
            durations = (durations or default) if default else default
        else:
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
    """PUT 全量更新：provider 元数据与模型列表。"""

    display_name: str
    base_url: str
    api_key: str | None = None  # None = 不修改
    models: list[ModelInput]
    # 并发上限随 PUT 全量提交（空输入 → None → 全局默认）；None 即清除，非"不修改"。
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class ConnectivityCheckRequest(BaseModel):
    # 连通性检查故意接受任意字符串，由 _run_connectivity_check 软失败返回 200 + success=False。
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


class ConnectivityCheckResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class DiscoverResponse(BaseModel):
    models: list[dict] = []
    # 该协议没有可枚举的模型列表。ComfyUI 的「模型」是 workflow 里的节点选择，服务端给不出
    # 与模型行对应的清单——回一个空列表会被读成「一个都没发现」，前端据此显示「请检查凭证」。
    # 前端据本位用一段说明替代「发现模型」按钮。
    not_applicable: bool = False
    reason: str | None = None


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
    # 实现形态："python"（backend 代码）| "declarative"（声明式定义）| "comfyui"（workflow
    # 与节点绑定）。前端据此决定「复制为我的 / 查看定义」是否可见（只对声明式端点成立），并
    # 按供应商协议过滤端点选择器的选项。
    kind: str
    # 端点来源：内置（随版发布，不可编辑删除）或用户自定义（落 custom_endpoint 表）。
    # 前端据此分组，并只对 custom 开放编辑与删除。
    source: Literal["builtin", "custom"] = "builtin"
    display_name_key: str
    # 声明式端点的显示名（定义里的 meta.name，专有名词不翻译）；Python 内置为 None，
    # 由前端按 display_name_key 取 i18n 文案。两者恰有其一，前端取名时先看本字段。
    display_name: str | None = None
    request_method: str
    request_path_template: str
    image_capabilities: list[str] | None = None  # image 类填能力字符串列表，其他为 None
    # 该 endpoint 的执行层是否真的下传尾帧约束；仅 video 类有意义。前端据此收窄 last_frame
    # 覆盖控件里「强制开」的可选范围——否则用户只能撞上写入侧的 422 才知道这条路不通。
    end_image_capable: bool = False
    # 参数约束四项，只有 ComfyUI 端点会取非默认值（``docs/adr/0082``）：尺寸 / 时长这两维由节点
    # 绑定决定 ArcReel 驱不驱动得了，驱动不了时对应的选择器禁用并明示；native_resolution 是不选
    # 档位时这份 workflow 实际会出的那一档，用作分辨率选择器的空值占位。
    size_fixed: bool = False
    duration_fixed: bool = False
    # 档位根本给不出来（frames 未绑定，或绑了却没有帧率来源）：时长这一维不由 ArcReel 驱动，
    # 模型行的档位编辑区只读、项目页的时长控件不渲染。duration_fixed 是它的子集，只决定文案说
    # 「天生固定」还是「缺帧率来源、补一处即可」。
    duration_tier_empty: bool = False
    native_resolution: str | None = None


class EndpointCatalogResponse(BaseModel):
    endpoints: list[EndpointDescriptor]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _system_capabilities_for(
    endpoint: str, model_id: str, endpoint_spec: EndpointSpec | None = None
) -> dict[str, object] | None:
    """读该 model 的系统判定能力；非 video endpoint 返回 None。

    判定失败（endpoint 已下线、注册表声明异常）时降级为 None 而非 500：列表端点要能把
    其余模型正常呈现出来，单行判定不出来只是设置页少一段"判定值"提示。

    音轨形态两维不回显：自定义供应商的音轨按「无信号不收紧」处理、不参与派生（见
    ``CAPABILITY_OVERRIDE_ALLOWLIST`` 的说明），把 backend 侧的静态声明摆到设置页会让用户
    以为它对自定义模型生效——那正是本口径要避免的分裂。
    """
    try:
        media_type = endpoint_spec.media_type if endpoint_spec is not None else static_media_type(endpoint)
        if media_type != "video":
            return None
        caps = asdict(system_video_capabilities(endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec))
        return {k: v for k, v in caps.items() if k not in _AUDIO_TRACK_FIELDS}
    except ValueError:
        logger.warning("无法判定系统能力: endpoint=%r model_id=%r", endpoint, model_id)
        return None


def _effective_overrides_for_response(
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
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
    filtered = _narrow_to_allowlist(
        filter_valid_overrides(
            endpoint=endpoint,
            model_id=model_id,
            overrides=overrides,
            endpoint_spec=endpoint_spec,
        )
    )
    filtered = strip_incoherent_audio_overrides(
        filtered,
        endpoint=endpoint,
        model_id=model_id,
        endpoint_spec=endpoint_spec,
    )
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


async def _read_endpoint_specs(
    session: AsyncSession,
    models,
    *,
    on_unknown: Callable[[str], None] | None = None,
) -> dict[str, EndpointSpec]:
    """逐个不同的 endpoint 解析一次 spec，供整批模型共用。

    ``on_unknown`` 决定解析不出来时的去向：回显侧只记日志、把该行按存量脏配置降级渲染；
    写入侧传入抛 422 的回调，不让一行引用不存在的端点入库。
    """
    repo = CustomEndpointRepository(session)
    specs: dict[str, EndpointSpec] = {}
    for endpoint in {model.endpoint for model in models}:
        try:
            specs[endpoint] = await resolve_endpoint_spec(endpoint, repo.get)
        except ValueError:
            if on_unknown is not None:
                on_unknown(endpoint)
            logger.warning("无法解析模型 endpoint，按存量脏配置回显: endpoint=%r", endpoint)
    return specs


def _model_to_response(
    m,
    global_bucket_refs: list[str] | None = None,
    endpoint_spec: EndpointSpec | None = None,
) -> ModelResponse:
    durations = json.loads(m.supported_durations) if m.supported_durations else None
    return ModelResponse(
        system_capabilities=_system_capabilities_for(m.endpoint, m.model_id, endpoint_spec),
        capability_overrides=_effective_overrides_for_response(
            m.endpoint, m.model_id, m.capability_overrides, endpoint_spec
        ),
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


def _provider_to_response(
    provider,
    models,
    global_bucket_refs: dict[str, list[str]] | None = None,
    endpoint_specs: Mapping[str, EndpointSpec] | None = None,
) -> ProviderResponse:
    refs = global_bucket_refs or {}
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
        discovery_format=provider.discovery_format,
        base_url=provider.base_url,
        api_key_masked=mask_secret(provider.api_key),
        models=[
            _model_to_response(
                m,
                refs.get(m.model_id),
                None if endpoint_specs is None else endpoint_specs.get(m.endpoint),
            )
            for m in models
        ],
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
    spec: EndpointSpec | None = None,
) -> None:
    """写入侧校验：合成函数对脏值只降级不抛，合法性把关全在这里。

    None 与空字典都表示"全部跟随系统判定"，一律放行。键集合已由 ``ModelInput`` 收窄到开放
    白名单内，此处校验其余维度：非空覆盖要求 endpoint 为 video 类，值类型与该能力字段一致。
    """
    if not overrides:
        return
    resolved_spec = spec or get_endpoint_spec(endpoint)
    if resolved_spec.media_type != "video":
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
                    expected=capability_type_name(expected),
                ),
            )
        # last_frame 覆盖为 True 时，endpoint 的 delegate.generate() 必须真的会读取
        # end_image 下传尾帧约束——否则覆盖只是让合成层宣称支持，执行层仍静默生成无约束视频。
        if key == "last_frame" and value is True and not resolved_spec.end_image_capable:
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
            and not resolved_spec.reference_audio_capable
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
        mode, count = resolve_audio_pair(overrides, endpoint=endpoint, model_id=model_id, endpoint_spec=resolved_spec)
        if not audio_capability_pair_is_coherent(mode=mode, count=count):
            raise HTTPException(
                status_code=422,
                detail=_t("capability_override_audio_pair_incoherent", model_id=model_id),
            )


def _check_model_capability_overrides(
    models: list[ModelInput],
    _t: Callable[..., str],
    specs: Mapping[str, EndpointSpec] | None = None,
) -> None:
    """对整批模型逐个跑覆盖校验（保存模型列表的写入路径，设置页表单的覆盖编辑也走这里）。

    每行都按提交上来的 ``(endpoint, 覆盖值)`` 校验：覆盖是否合法随 endpoint 变化
    （last_frame=True 要求 endpoint 的 end_image_capable，非 video endpoint 直接拒绝非空覆盖），
    故覆盖字典原样不动、只切 endpoint 的整表 PUT 同样要按新 endpoint 重新判定。开放白名单外的
    键不会走到这里——``ModelInput`` 已在解析期把它们剔除。
    """
    for m in models:
        _check_capability_overrides(
            m.capability_overrides,
            m.endpoint,
            m.model_id,
            _t,
            None if specs is None else specs[m.endpoint],
        )


def _check_protocol_constraints(
    models: list[ModelInput],
    discovery_format: str,
    specs: Mapping[str, EndpointSpec],
    _t: Callable[..., str],
) -> None:
    """ComfyUI 协议特有的两条写入约束：端点挂接双向配对，能力覆盖关闭。

    挂接配对是双向的（``docs/adr/0081``）：ComfyUI 端点的运行时只对 ComfyUI 服务有意义，声明式
    端点对 ComfyUI 服务同样无意义。错挂在保存期毫无征兆，要等发起生成才在传输层露出来，故两个
    方向都在写入侧拒。判定本身在 :func:`endpoint_attachment_holds`，此处只负责挑出违规的那个方向。

    本判定须排在 :func:`_check_model_capability_overrides` 之前：ComfyUI 端点的能力位全空，
    它的覆盖同样过不了通用校验，先判协议才给得出「该协议不支持覆盖」这条专用文案，而不是
    「endpoint 不支持尾帧」。

    能力覆盖对该协议关闭：ComfyUI 端点的能力由节点绑定推导，覆盖值在执行层没有对应物。回显侧
    的对应处置是忽略存量值（见 :func:`lib.custom_provider.capabilities.filter_valid_overrides`）。

    只对 comfyui 协议生效，其余协议的自由挂接与覆盖编辑一概不变。
    """
    comfyui_provider = is_comfyui_protocol(discovery_format)
    for m in models:
        spec = specs[m.endpoint]
        if not endpoint_attachment_holds(endpoint_kind=spec.kind, discovery_format=discovery_format):
            key = (
                "comfyui_provider_requires_comfyui_endpoint"
                if comfyui_provider
                else "comfyui_endpoint_requires_comfyui_provider"
            )
            raise HTTPException(status_code=422, detail=_t(key, model_id=m.model_id, endpoint=m.endpoint))
        if comfyui_provider and m.capability_overrides:
            raise HTTPException(
                status_code=422,
                detail=_t("capability_overrides_not_supported_for_comfyui", model_id=m.model_id),
            )


async def _resolve_model_endpoint_specs(
    session: AsyncSession,
    models: list[ModelInput],
    _t: Callable[..., str],
) -> dict[str, EndpointSpec]:
    """写入侧解析：任一模型行引用的端点不存在即 422，不落库。"""

    def reject(endpoint: str) -> None:
        raise HTTPException(status_code=422, detail=_t("unknown_endpoint", endpoint=endpoint))

    return await _read_endpoint_specs(session, models, on_unknown=reject)


def _check_unique_defaults(models: list[ModelInput], specs: dict[str, EndpointSpec], _t: Callable[..., str]) -> None:
    """校验默认模型互斥。

    - 非 image endpoint（text / video / audio）：同一 media_type 至多 1 个 is_default=True。
    - image endpoint：image capability 集合两两不相交（即同一 capability 至多 1 个默认）。

    媒体类型与能力位都读 ``specs`` 里已解析好的 spec：``ce-`` 端点的这两项写在定义里，键前缀推
    不出来，内置查表对它一律抛错。``specs`` 由 :func:`_resolve_model_endpoint_specs` 现解析，
    解析不出的行已在那里被 422 拦下。
    """
    text_video_defaults: dict[str, list[str]] = {}
    image_defaults: list[tuple[str, frozenset[ImageCapability]]] = []
    for m in models:
        if not m.is_default:
            continue
        spec = specs[m.endpoint]
        if spec.media_type != "image":
            text_video_defaults.setdefault(spec.media_type, []).append(m.model_id)
            continue
        image_defaults.append((m.model_id, spec.image_capabilities or frozenset()))

    duplicates: dict[str, list[str]] = {mt: ids for mt, ids in text_video_defaults.items() if len(ids) > 1}

    # image：按 capability 反向索引，任一槽位有 >1 个默认即视为冲突（O(n) 替代 O(n²) 两两 caps 求交）
    cap_to_ids: dict[ImageCapability, list[str]] = {}
    for mid, caps in image_defaults:
        for c in caps:
            cap_to_ids.setdefault(c, []).append(mid)
    conflict_ids = [mid for ids in cap_to_ids.values() if len(ids) > 1 for mid in ids]
    # 能力集为空即「这个端点没说自己能做什么」：它与任何一个 image 默认都分不开，只要还有别的
    # image 默认就算冲突。放两个进去保存期看着没事，取默认模型时会一次查出两行、在生成期炸掉。
    if len(image_defaults) > 1 and any(not caps for _mid, caps in image_defaults):
        conflict_ids = [mid for mid, _caps in image_defaults]
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
    session: AsyncSession = Depends(get_async_session),
):
    """列出所有自定义供应商（含模型列表）。"""
    repo = CustomProviderRepository(session)
    pairs = await repo.list_providers_with_models()
    from lib.config.service import ConfigService

    all_settings = await ConfigService(session).get_all_settings()
    endpoint_specs = await _read_endpoint_specs(session, [model for _, models in pairs for model in models])
    return {
        "providers": [
            _provider_to_response(
                p,
                models,
                _extract_global_bucket_refs(all_settings, p.id),
                endpoint_specs,
            )
            for p, models in pairs
        ]
    }


# /endpoints 必须先于 /{provider_id} 注册，否则 FastAPI 会把字符串 "endpoints" 当作 provider_id。
@router.get("/endpoints", response_model=EndpointCatalogResponse)
async def list_endpoint_catalog(
    session: AsyncSession = Depends(get_async_session),
) -> EndpointCatalogResponse:
    """暴露两个命名空间的 endpoint 作为前端单一真相源：渲染下拉、显示路径与分组都派生自此返回值。

    内置取自 ENDPOINT_REGISTRY，自定义由 custom_endpoint 表的定义现构造——不做启动时全量装载，
    定义原地改完、目录下次拉取即是新的。单行定义构造不出 spec（只可能来自手工改库）时跳过并
    告警，而不是让整份目录失败：一条坏定义不该把端点下拉整个打空。
    """
    specs = list(ENDPOINT_REGISTRY.values())
    for row in await CustomEndpointRepository(session).list_all():
        try:
            specs.append(endpoint_spec_from_row(row))
        except (KeyError, TypeError, ValueError):
            logger.warning("自定义调用端点定义无法构造 spec，已跳过: id=%s", row.id, exc_info=True)
    return EndpointCatalogResponse(
        endpoints=[EndpointDescriptor(**endpoint_spec_to_dict(spec)) for spec in specs],
    )


@router.get("/endpoints/{endpoint_key}/definition")
async def get_endpoint_definition(endpoint_key: str, _t: Translator) -> dict[str, Any]:
    """取内置声明式端点的定义 JSON，供「复制为我的」原样 POST 成 ce-<id> 副本。

    Python 实现的内置端点没有定义可取，与未知键一并回 404。返回体是定义原样（零封套），
    与导入导出的文件格式同一份东西。
    """
    spec = ENDPOINT_REGISTRY.get(endpoint_key)
    if spec is None or spec.definition is None:
        raise HTTPException(status_code=404, detail=_t("endpoint_definition_not_found", endpoint=endpoint_key))
    return dict(spec.definition)


@router.post("", status_code=201)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """创建自定义供应商，可同时创建模型列表。"""
    specs: dict[str, EndpointSpec] = {}
    if body.models:
        _check_duplicate_model_ids(body.models, _t)
        specs = await _resolve_model_endpoint_specs(session, body.models, _t)
        _check_unique_defaults(body.models, specs, _t)
        _check_protocol_constraints(body.models, body.discovery_format, specs, _t)
        _check_model_capability_overrides(body.models, _t, specs)
    repo = CustomProviderRepository(session)
    model_dicts = [m.to_db_dict(specs[m.endpoint]) for m in body.models] if body.models else None
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
    return _provider_to_response(
        provider,
        models,
        await _global_bucket_refs_for_provider(session, provider.id),
        await _read_endpoint_specs(session, models),
    )


@router.get("/{provider_id}")
async def get_provider(
    provider_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """获取单个自定义供应商详情。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        models,
        await _global_bucket_refs_for_provider(session, provider_id),
        await _read_endpoint_specs(session, models),
    )


@router.get("/{provider_id}/credentials", response_model=CredentialsResponse)
async def get_provider_credentials(
    provider_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """返回明文 base_url + api_key，供 Agent 配置导入复用。

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
    return _provider_to_response(
        provider,
        models,
        await _global_bucket_refs_for_provider(session, provider_id),
        await _read_endpoint_specs(session, models),
    )


@router.put("/{provider_id}")
async def full_update_provider(
    provider_id: int,
    body: FullUpdateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """原子更新供应商元数据 + 模型列表（单一事务）。"""
    _check_duplicate_model_ids(body.models, _t)
    specs = await _resolve_model_endpoint_specs(session, body.models, _t)
    _check_unique_defaults(body.models, specs, _t)
    repo = CustomProviderRepository(session)
    # 协议是创建时定下、之后不可改的，故读库里这一行而非请求体。取行排在写入之前：协议约束要
    # 先于覆盖校验判定，comfyui 行才拿得到「该协议不支持覆盖」这条专用文案，而不是被通用的
    # 「endpoint 不支持尾帧」抢先拒掉。
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    _check_protocol_constraints(body.models, provider.discovery_format, specs, _t)
    _check_model_capability_overrides(body.models, _t, specs)
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
    model_dicts = [m.to_db_dict(specs[m.endpoint]) for m in body.models]
    await repo.replace_models(provider_id, model_dicts)
    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        models,
        await _global_bucket_refs_for_provider(session, provider_id),
        await _read_endpoint_specs(session, models),
    )


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    request: Request,
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
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """替换供应商的整个模型列表。"""
    _check_duplicate_model_ids(body.models, _t)
    specs = await _resolve_model_endpoint_specs(session, body.models, _t)
    _check_unique_defaults(body.models, specs, _t)
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    _check_protocol_constraints(body.models, provider.discovery_format, specs, _t)
    _check_model_capability_overrides(body.models, _t, specs)
    # 记录旧模型 ID，用于清理悬空引用
    old_model_ids = {m.model_id for m in await repo.list_models(provider_id)}
    new_model_ids = {m.model_id for m in body.models}
    deleted_model_ids = old_model_ids - new_model_ids

    model_dicts = [m.to_db_dict(specs[m.endpoint]) for m in body.models]
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
    endpoint_specs = await _read_endpoint_specs(session, new_models)
    return [_model_to_response(m, refs.get(m.model_id), endpoint_specs.get(m.endpoint)) for m in new_models]


# ---------------------------------------------------------------------------
# 无状态操作
# ---------------------------------------------------------------------------


@router.post("/discover")
async def discover_models_endpoint(
    body: ConnectivityCheckRequest,
    _t: Translator,
):
    """模型发现：根据 discovery_format + base_url + api_key 查询可用模型。"""
    return await _run_discover(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/discover-anthropic", response_model=DiscoverResponse)
async def discover_anthropic_models_endpoint(
    body: DiscoverAnthropicRequest,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Anthropic 协议模型发现：Agent 配置专用。

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

    base_url = body.base_url if not needs_url else (_credential_discovery_base(cred) if cred else None)
    if base_url:
        from lib.config.url_utils import InvalidAnthropicBaseUrlError, validate_anthropic_base_url

        try:
            base_url = validate_anthropic_base_url(base_url)
        except InvalidAnthropicBaseUrlError as exc:
            # 文案固定，不回显输入：地址里可能带着用户误填的 api_key
            raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc

    return await _run_discover("anthropic", base_url, api_key, _t)


@router.post("/{provider_id}/discover")
async def discover_models_by_id(
    provider_id: int,
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
async def check_connectivity(
    body: ConnectivityCheckRequest,
    _t: Translator,
):
    """连通性检查：验证 discovery_format + base_url + api_key 是否可达。免费探针，不证明任何模型可生成。"""
    return await _run_connectivity_check(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/{provider_id}/test")
async def check_connectivity_by_id(
    provider_id: int, _t: Translator, session: AsyncSession = Depends(get_async_session)
):
    """用已存储凭证对指定供应商做连通性检查。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_connectivity_check(provider.discovery_format, provider.base_url, provider.api_key, _t)


def _credential_discovery_base(cred: Any) -> str | None:
    """激活凭证用于模型发现的根地址。

    预设凭证未覆盖 base_url（存储值仍等于预设 ``messages_url``）时，模型列表按预设目录的
    ``discovery_url`` 取（DeepSeek 的列表不在 messages 根之下）；自定义或已覆盖的凭证按存储值。
    与前端凭证表单「预填值不算覆盖」同一规则。
    """
    from lib.agent_provider_catalog import get_preset

    preset = get_preset(cred.preset_id) if cred.preset_id else None
    if preset is not None and cred.base_url == preset.messages_url:
        return preset.discovery_url or preset.messages_url
    return cred.base_url


async def _run_discover(
    discovery_format: str,
    base_url: str | None,
    api_key: str,
    _t: Callable[..., str],
    *,
    discover_models_fn: Callable[..., Awaitable[list[dict]]] | None = None,
) -> DiscoverResponse:
    """共用的模型发现逻辑（明文凭证 / 已存储凭证两条入口共用）。"""
    from lib.config.url_utils import InvalidAnthropicBaseUrlError
    from lib.custom_provider.discovery import UnsupportedDiscoveryFormatError, discover_models

    if is_comfyui_protocol(discovery_format):
        # 结构化的「不适用」而非 422：这不是一次失败的发现，是该协议本就没有这一步。
        return DiscoverResponse(not_applicable=True, reason=_t("discovery_not_applicable_comfyui"))
    try:
        discover = discover_models_fn or discover_models
        models = await discover(
            discovery_format=discovery_format,
            base_url=base_url or None,
            api_key=api_key,
        )
        return DiscoverResponse(models=models)
    except UnsupportedDiscoveryFormatError as exc:
        raise BadRequestError("invalid_discovery_format", discovery_format=discovery_format) from exc
    except InvalidAnthropicBaseUrlError as exc:
        # 入口校验拒绝的地址不是上游故障：与 /discover-anthropic 同样 422，文案不回显输入。
        raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("模型发现失败: %s", err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg)) from exc


async def _run_connectivity_check(
    discovery_format: str,
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    openai_probe: Callable[[str, str, Callable[..., str]], ConnectivityCheckResponse] | None = None,
    google_probe: Callable[[str, str, Callable[..., str]], ConnectivityCheckResponse] | None = None,
) -> ConnectivityCheckResponse:
    """共用的连通性检查逻辑：明文凭证与已存储凭证两条入口共用。"""
    try:
        if discovery_format == "openai":
            result = await asyncio.wait_for(
                asyncio.to_thread(openai_probe or _check_openai, base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        elif discovery_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(google_probe or _check_google, base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        elif is_comfyui_protocol(discovery_format):
            # 不进 to_thread：ComfyUI 探针走 httpx 直调，本就是协程，另外两条是同步 SDK 调用。
            result = await asyncio.wait_for(
                _check_comfyui(base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        else:
            return ConnectivityCheckResponse(
                success=False,
                message=_t("connectivity_check_unsupported_format", discovery_format=discovery_format),
            )
        return result
    except TimeoutError:
        return ConnectivityCheckResponse(
            success=False,
            message=_t("connectivity_check_timeout"),
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连通性检查失败 [%s]: %s", discovery_format, err_msg)
        return ConnectivityCheckResponse(
            success=False,
            message=_t("connectivity_check_failed", err_msg=err_msg),
        )


def _check_openai(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> ConnectivityCheckResponse:
    """通过 models.list() 验证 OpenAI 兼容 API。"""
    from openai import OpenAI

    from lib.config.url_utils import ensure_openai_base_url

    client = (client_factory or OpenAI)(api_key=api_key, base_url=ensure_openai_base_url(base_url))
    models = client.models.list()
    count = sum(1 for _ in models)
    return ConnectivityCheckResponse(
        success=True,
        message=_t("connectivity_check_ok"),
        model_count=count,
    )


def _check_google(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> ConnectivityCheckResponse:
    """通过 models.list() 验证 Google genai API。"""
    from google import genai

    from lib.config.url_utils import ensure_google_base_url

    effective_url = ensure_google_base_url(base_url)
    http_options = {"base_url": effective_url} if effective_url else None
    client = (client_factory or genai.Client)(api_key=api_key, http_options=http_options)  # type: ignore[arg-type]
    pager = client.models.list()
    count = sum(1 for _ in pager)
    return ConnectivityCheckResponse(
        success=True,
        message=_t("connectivity_check_ok"),
        model_count=count,
    )


def _comfyui_probe_headers(api_key: str) -> dict[str, str]:
    """ComfyUI 探针的请求头：``api_key`` 作 Bearer 裸打，留空则完全不带凭证。

    端点定义里的 ``auth`` 节在此不渲染（``docs/adr/0081``）：那是端点级的凭据模板，供应商级
    探针拿不到，也不该替某一个端点猜。用自定义请求头做反向代理鉴权的部署因此可能探不通，
    前端为此标注「以测试连接为准」。
    """
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


async def _check_comfyui(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
) -> ConnectivityCheckResponse:
    """通过 ``GET {base_url}/system_stats`` 验证 ComfyUI 可达，并回显 ``comfyui_version``。

    ``model_count`` 不填：ComfyUI 没有可枚举的模型列表，填 0 会被读成「一个模型都没有」。
    """
    url = base_url.strip().rstrip("/") + _COMFYUI_SYSTEM_STATS_PATH
    resp = await get_http_client().get(
        url,
        headers=_comfyui_probe_headers(api_key),
        timeout=_CONNECTIVITY_CHECK_TIMEOUT,
    )
    raise_for_status_redacted(resp)
    payload = resp.json()
    system = payload.get("system") if isinstance(payload, dict) else None
    version = system.get("comfyui_version") if isinstance(system, dict) else None
    # 版本缺失仍算可达：老版本与部分代理不回这一字段，据此判失败会把能用的部署拦在外面。
    message = (
        _t("connectivity_check_comfyui_ok", version=str(version))
        if version
        else _t("connectivity_check_comfyui_ok_unknown_version")
    )
    return ConnectivityCheckResponse(success=True, message=message)
