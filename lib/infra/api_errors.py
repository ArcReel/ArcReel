"""Domain exceptions carrying (status_code, i18n key, params).

Conventions:
- lib / service layers raise with i18n keys and params only, not finished copy.
- The app-level exception handler (``server/error_handlers.py``) owns status-code
  mapping, request Accept-Language translation and redaction; routes retain the happy path.
- ``str(exc)`` goes only to server logs, never to clients.
"""

from __future__ import annotations

from typing import Self


class ApiError(Exception):
    """Domain-error base, translated by the app handler into a ``{"detail": ...}`` response.

    ``detail`` is a product-language summary rendered from i18n key + params for the
    user. Developer-only information such as field names, schemas and tool IDs
    belongs in optional ``diagnostic`` instead; the handler adds that response field
    only when nonempty.

    ``diagnostic`` is deliberately absent from the constructor: ``params`` collects
    rendering parameters through ``**kwargs``. Sharing that keyword namespace would
    silently consume any rendering parameter named ``diagnostic``. Attach it with
    :meth:`with_diagnostic` instead.

    ``diagnostic`` is sent unchanged (explanatory text or structured diagnostics), so
    include only information reproducible from the request: field names, expected
    schemas and original exceptions caused by submitted content. Server absolute
    paths, credentials and internal stacks belong only in logs.
    """

    def __init__(self, key: str, *, status_code: int, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params
        self.diagnostic: object | None = None

    def with_diagnostic(self, diagnostic: object) -> Self:
        """Attach technical diagnostics and return self for ``raise XxxError(key).with_diagnostic(...)``."""
        self.diagnostic = diagnostic
        return self


class BadRequestError(ApiError):
    """Client request error (HTTP 400)."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=400, **params)


class UnprocessableError(ApiError):
    """Request format or content cannot be processed (HTTP 422)."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=422, **params)


class NotFoundError(ApiError):
    """Requested resource does not exist (HTTP 404)."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=404, **params)


class ConflictError(ApiError):
    """Conflict with the current resource state (HTTP 409)."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=409, **params)


class BadGatewayError(ApiError):
    """Upstream content is unusable or could not be retrieved (HTTP 502)."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=502, **params)


class ServiceUnavailableError(ApiError):
    """Service temporarily unavailable (HTTP 503)."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=503, **params)
