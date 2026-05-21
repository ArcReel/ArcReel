"""共享校验函数，供多个 router 复用。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException

from lib.config.registry import PROVIDER_REGISTRY
from lib.i18n import _ as _default_translate


def validate_backend_value(value: str, field_name: str, _t: Callable[..., str] = _default_translate) -> None:
    """校验 ``provider/model`` 格式的 backend 字段值。

    只接受规范 provider id（``PROVIDER_REGISTRY`` 的 key 或 ``custom-`` 前缀）。legacy provider 名
    （``gemini``/``aistudio``/``vertex``/``seedance``）一律拒绝——它们是待清除的历史数据，由一次性项目迁移
    转为规范 id 后即不再被接受（见 ``docs/adr/0001``）。

    Raises:
        HTTPException(400): 格式不合法、provider 不在注册表中、或为 legacy 名。
    """
    if "/" not in value:
        if value in PROVIDER_REGISTRY:
            return  # 裸 registry id（无 model），下游按全局默认补全
        detail = _t("invalid_backend_format", field_name=field_name)
        raise HTTPException(
            status_code=400,
            detail=detail,
        )
    provider_id = value.split("/", 1)[0]
    if provider_id not in PROVIDER_REGISTRY and not provider_id.startswith("custom-"):
        detail = _t("unknown_provider", provider_id=provider_id)
        raise HTTPException(
            status_code=400,
            detail=detail,
        )
