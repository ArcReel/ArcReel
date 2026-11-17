"""目录查询相关的错误。"""

from __future__ import annotations


class ModelNotInCatalogError(ValueError):
    """内置供应商的模型引用不在该供应商的模型清单中（已改名或从未收录；已下线的见 :class:`ModelDiscontinuedError`）。

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


class ModelNotSelectedError(ModelNotInCatalogError):
    """项目的模型字段只写了内置供应商、没有选定模型（v19→v20 迁移补全不了的旧值，docs/adr/0099）。

    不按推荐补模型，也不回退到别的设置去生成和计费；用户在项目设置里重新选择模型后即可继续。
    """

    def __init__(self, *, provider_id: str, media_type: str):
        super().__init__(provider_id=provider_id, model_id="", media_type=media_type)
        self.code = "model_not_selected"
        self.params = {"provider": provider_id}
        self.args = (f"project {media_type} model names only the provider {provider_id}; re-select a model",)


class ModelDiscontinuedError(ModelNotInCatalogError):
    """引用的模型已下线（docs/adr/0103）：报错带上下线日期与官方后继，引用同样原样保留、由用户重新选择。

    是 :class:`ModelNotInCatalogError` 的子类，凡是按「请重新选择模型」处理清单外引用的地方都照旧接得住。
    ``shutdown_date`` 是 ISO 日期，``successor`` 是「模型厂商/官方模型名」；没有登记时不出现在 ``params`` 里，
    渲染时按 :func:`lib.generation.task_failure.failure_message_key` 选用省去这一句的文案。
    """

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        media_type: str,
        shutdown_date: str | None = None,
        successor: str | None = None,
    ):
        super().__init__(provider_id=provider_id, model_id=model_id, media_type=media_type)
        self.shutdown_date = shutdown_date
        self.successor = successor
        self.code = "model_discontinued"
        self.params = {"provider": provider_id, "model": model_id}
        message = f"{media_type} model {provider_id}/{model_id} has been discontinued"
        if shutdown_date is not None:
            self.params["shutdown_date"] = shutdown_date
            message += f" since {shutdown_date}"
        if successor is not None:
            self.params["successor"] = successor
            message += f"; the official successor is {successor}"
        self.args = (f"{message}; re-select a model",)
