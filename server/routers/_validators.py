"""共享校验函数，供多个 router 复用。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException

from lib.config.registry import PROVIDER_REGISTRY
from lib.i18n import _ as _default_translate

# backend 字段名 → 期望 media_type，驱动 validate_backend_value 的档位/模型能力匹配校验。
# 未登记的字段名视为不做该项校验（新增字段忘记登记时静默放行，而非误报）。
_FIELD_MEDIA_TYPES: dict[str, str] = {
    "video_backend": "video",
    "default_video_backend": "video",
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


def validate_backend_value(value: str, field_name: str, _t: Callable[..., str] = _default_translate) -> None:
    """校验 ``provider/model`` 格式的 backend 字段值。

    只接受规范 provider id（``PROVIDER_REGISTRY`` 的 key 或 ``custom-`` 前缀）。legacy provider 名
    （``gemini``/``aistudio``/``vertex``/``seedance``）一律拒绝——它们是待清除的历史数据，由一次性项目迁移
    转为规范 id 后即不再被接受（见 ``docs/adr/0001``）。

    额外校验 registry 内登记模型的 ``media_type`` 与 ``field_name``（经 ``_FIELD_MEDIA_TYPES``）期望的
    一致（如 text 档位字段不接受 video 模型）；只对 ``PROVIDER_REGISTRY`` 内登记模型判定，registry 外
    （自定义供应商等）无逐模型能力事实，放行交由供应商 API 把关，不做猜测。

    Raises:
        HTTPException(400): 格式不合法、provider 不在注册表中、为 legacy 名、或 media_type 不匹配。
    """
    if "/" not in value:
        if value in PROVIDER_REGISTRY:
            return  # 裸 registry id（无 model），下游按全局默认补全
        detail = _t("invalid_backend_format", field_name=field_name)
        raise HTTPException(
            status_code=400,
            detail=detail,
        )
    provider_id, model_id = value.split("/", 1)
    provider_meta = PROVIDER_REGISTRY.get(provider_id)
    if provider_meta is None and not provider_id.startswith("custom-"):
        detail = _t("unknown_provider", provider_id=provider_id)
        raise HTTPException(
            status_code=400,
            detail=detail,
        )
    expected_media_type = _FIELD_MEDIA_TYPES.get(field_name)
    if expected_media_type is not None and provider_meta is not None:
        model_info = provider_meta.models.get(model_id)
        if model_info is not None and model_info.media_type != expected_media_type:
            detail = _t(
                "backend_media_type_mismatch",
                field_name=field_name,
                provider=provider_id,
                model=model_id,
                expected=expected_media_type,
                actual=model_info.media_type,
            )
            raise HTTPException(
                status_code=400,
                detail=detail,
            )
