"""共享校验函数，供多个 router 复用。"""

from __future__ import annotations

from lib.api_errors import BadRequestError
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError, VideoCapability


async def require_video_bucket_capability(project: dict, capability: VideoCapability) -> None:
    """视频生成入口预检：按能力桶解析全局 + 项目配置并过解析闸（``docs/adr/0054``）。

    解析出的模型缺该桶所需能力、或配置引用已不可用（模型被删 / 能力被改 / 供应商被删）时抛
    ``BadRequestError``（app 级 handler 本地化为 400），让用户在提交入口即看到修复指引，而不是
    任务面板里的异步失败。worker 执行时仍经同一解析闸独立校验，预检失效不放行执行。其余解析
    失败（如未配置任何供应商）沿用既有行为，留给 worker 在任务面板暴露。
    """
    from lib.db import async_session_factory

    try:
        await ConfigResolver(async_session_factory).resolve_video_backend(project, None, capability=capability)
    except VideoBucketCapabilityError as exc:
        raise BadRequestError(exc.code, **exc.params) from exc
    except ValueError:
        # 未配置任何供应商等其余解析失败：放行入队，由 worker 在任务面板暴露（与图片 / 视频
        # 生成入口的既有行为一致，不把非能力类失败升级为提交期拒绝）
        return


async def require_audio_switch_supported(project: dict, capability: VideoCapability) -> None:
    """视频生成入口预检：成片恒有声的模型不接受「关闭音频」的配置。

    这类模型在该执行路径上没有音轨开关可下发（音轨形态 ``always_on``），关闭意图无法抵达供应商，
    却会让编排层按无声路径裁掉全部音色约束——用户拿到的是失去音色约束的有声成片。提交入口
    显式拒绝并说明修复路径，比让请求带着不可能实现的意图执行下去更可用。

    设置界面已按同一判据禁用开关，此处覆盖存量配置里已存「关闭」的项目。判据取自
    :func:`server.services.video_caps.resolve_audio_switch_conflict`，与 Agent 入队路径同源；
    解析失败一律放行（与 :func:`require_video_bucket_capability` 同口径），不把配置解析问题
    升级为提交期拒绝。
    """
    from server.services.video_caps import resolve_audio_switch_conflict

    conflict = await resolve_audio_switch_conflict(project, capability)
    if conflict is None:
        return
    provider_id, model_id = conflict
    raise BadRequestError("video_audio_switch_not_supported", provider=provider_id, model=model_id)


# backend 字段名 → 期望 media_type，驱动 validate_backend_value 的档位/模型能力匹配校验。
# 未登记的字段名视为不做该项校验（新增字段忘记登记时静默放行，而非误报）。
_FIELD_MEDIA_TYPES: dict[str, str] = {
    "video_backend": "video",
    "video_provider_i2v": "video",
    "video_provider_r2v": "video",
    "default_video_backend": "video",
    "default_video_backend_i2v": "video",
    "default_video_backend_r2v": "video",
    "image_provider_t2i": "image",
    "image_provider_i2i": "image",
    "default_image_backend": "image",
    "default_image_backend_t2i": "image",
    "default_image_backend_i2i": "image",
    "audio_backend": "audio",
    "default_audio_backend": "audio",
    "text_backend_simple": "text",
    "text_backend_complex": "text",
    "default_text_backend": "text",
}


def validate_backend_value(value: str, field_name: str) -> None:
    """校验 ``provider/model`` 格式的 backend 字段值。

    只接受规范 provider id（``PROVIDER_REGISTRY`` 的 key 或 ``custom-`` 前缀）。legacy provider 名
    （``gemini``/``aistudio``/``vertex``/``seedance``）一律拒绝——它们是待清除的历史数据，由一次性项目迁移
    转为规范 id 后即不再被接受（见 ``docs/adr/0001``）。

    额外校验 registry 内登记模型的 ``media_type`` 与 ``field_name``（经 ``_FIELD_MEDIA_TYPES``）期望的
    一致（如 text 档位字段不接受 video 模型）；只对 ``PROVIDER_REGISTRY`` 内登记模型判定，registry 外
    （自定义供应商等）无逐模型能力事实，放行交由供应商 API 把关，不做猜测。

    Raises:
        BadRequestError: 格式不合法、provider 不在注册表中、为 legacy 名、或 media_type 不匹配。
            字段名只进诊断信息，不进面向使用者的摘要。
    """
    if "/" not in value:
        if value in PROVIDER_REGISTRY:
            return  # 裸 registry id（无 model），下游按全局默认补全
        raise BadRequestError("invalid_backend_format").with_diagnostic(f"field: {field_name}")
    provider_id, model_id = value.split("/", 1)
    provider_meta = PROVIDER_REGISTRY.get(provider_id)
    if provider_meta is None and not provider_id.startswith("custom-"):
        raise BadRequestError("unknown_provider", provider_id=provider_id).with_diagnostic(f"field: {field_name}")
    expected_media_type = _FIELD_MEDIA_TYPES.get(field_name)
    if expected_media_type is not None and provider_meta is not None:
        model_info = provider_meta.models.get(model_id)
        if model_info is not None and model_info.media_type != expected_media_type:
            raise BadRequestError(
                "backend_media_type_mismatch",
                provider=provider_id,
                model=model_id,
                expected=expected_media_type,
                actual=model_info.media_type,
            ).with_diagnostic(f"field: {field_name}")
