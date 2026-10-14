"""目录查询相关的错误。"""

from __future__ import annotations


class ModelNotInCatalogError(ValueError):
    """内置供应商的模型引用不在该供应商的模型清单中（已下架、已改名或从未收录）。

    引用原样保留在设置与项目里，不静默回落到别的模型；用户重新选择模型后即可继续。
    ``code`` 是 errors 目录 key、``params`` 是其渲染参数，worker 落库经
    ``lib.generation.task_failure.encode_failure`` 结构化编码。``str(exc)`` 是英文技术消息。
    """

    code = "model_not_in_provider_catalog"

    def __init__(self, *, provider_id: str, model_id: str, media_type: str):
        self.provider_id = provider_id
        self.model_id = model_id
        self.media_type = media_type
        self.params: dict[str, str] = {"provider": provider_id, "model": model_id}
        super().__init__(
            f"{media_type} model {provider_id}/{model_id} is not in the provider's model list; re-select a model"
        )
