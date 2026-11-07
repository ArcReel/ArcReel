"""Catalog query errors."""

from __future__ import annotations


class ModelNotInCatalogError(ValueError):
    """Built-in model references absent from provider manifests, renamed or never listed; discontinued models use :class:`ModelDiscontinuedError`.

    Keep references unchanged in settings/projects, never silently substitute other models; users resume after choosing again.
    Stable failure ``code`` and rendering ``params`` map through ``lib.generation.task_failure.FAILURE_CODE_KEYS``
    to errors directory keys; workers persist structured ``encode_failure``. ``str(exc)`` is English technical text.

    Empty ``model_id`` means provider-only references lacking that media type in manifests: report
    ``provider_unsupported_media`` (provider cannot generate this type), never render incomplete provider/ references.
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


class ModelDiscontinuedError(ModelNotInCatalogError):
    """Referenced models discontinued (docs/adr/0103): errors carry lifecycle dates/official successors; references remain unchanged for user reselection.

    Subclass :class:`ModelNotInCatalogError` so existing reselection handling for unlisted references still applies.
    ``shutdown_date`` is ISO dates; ``successor`` is model vendor/official name. Unregistered values are absent from ``params``;
    :func:`lib.generation.task_failure.failure_message_key` selects wording omitting the unavailable sentence.
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
