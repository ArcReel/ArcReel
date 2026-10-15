"""目录查询相关的错误。"""

from __future__ import annotations


class ModelNotInCatalogError(ValueError):
    """内置供应商的模型引用不在该供应商的模型清单中（已下架、已改名或从未收录）。

    引用原样保留在设置与项目里，不静默回落到别的模型；用户重新选择模型后即可继续。
    ``code`` 是稳定失败码、``params`` 是其渲染参数，经 ``lib.generation.task_failure.FAILURE_CODE_KEYS``
    对应到 errors 目录 key；worker 落库经 ``encode_failure`` 结构化编码。``str(exc)`` 是英文技术消息。

    ``model_id`` 为空表示引用只写了供应商、而该供应商在模型清单里没有这一媒体类型的模型，此时报
    ``provider_unsupported_media``（该供应商给不出这一类生成），不渲染残缺的「供应商/」引用。
    """

    code: str
    params: dict[str, str]

    def __init__(self, *, provider_id: str, model_id: str, media_type: str):
        self.provider_id = provider_id
        self.model_id = model_id
        self.media_type = media_type
        if model_id:
            self.code = "model_not_in_provider_catalog"
            self.params = {"provider": provider_id, "model": model_id}
            message = f"{media_type} model {provider_id}/{model_id} is not in the provider's model list"
        else:
            self.code = "provider_unsupported_media"
            self.params = {"provider_id": provider_id, "media_type": media_type}
            message = f"provider {provider_id} has no {media_type} model in its model list"
        super().__init__(f"{message}; re-select a model")
