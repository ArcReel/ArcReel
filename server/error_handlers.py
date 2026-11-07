"""Application-level exception handlers: the single exception-to-status-to-detail mapping point.

Route bodies retain only happy paths; domain and library exceptions are handled consistently here:

- Status mapping (carried by ``ApiError``; fixed by type for library exceptions)
- Translation by request ``Accept-Language`` (reuse ``get_translator``), replacing provider/model IDs with display names
- Redaction: never return exception messages to clients beyond params explicitly declared by i18n keys;
  ``FileNotFoundError`` / unexpected-exception ``str(exc)`` may contain absolute server paths and goes only to logs

Incremental migration: routes retaining their own try/except are unaffected (exceptions never propagate here);
migrating an endpoint means removing its except ladder and letting exceptions propagate.
"""

import logging
from collections.abc import Callable, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lib.catalog.errors import ModelNotInCatalogError
from lib.generation.generation_queue import ActiveTaskRequestConflict
from lib.generation.generation_queue_client import TaskSpecValidationError
from lib.generation.task_failure import failure_message_key
from lib.i18n import render_generation_input_error, render_message
from lib.infra.api_errors import ApiError
from lib.script.script_editor import ScriptEditError
from server.i18n import get_translator, request_display_names
from server.services.project.episode_display import present_request_diagnostics

logger = logging.getLogger(__name__)


def script_edit_detail(exc: ScriptEditError, _t: Callable[..., str]) -> str:
    """Single mapping from ``ScriptEditError`` to user-visible detail.

    Translate reason via ``exc.key``/``exc.params`` in the request language, without embedding ``str(exc)``: the latter is fixed
    Chinese and would mix Chinese into en/vi responses. Routes retaining ``except ScriptEditError`` and this module's
    global handler share this function to prevent scattered copies of the same expression evolving separately.
    """
    return _t("script_data_corrupted", reason=_t(exc.key, **exc.params))


def _cors_headers_for(
    request: Request,
    *,
    allow_origins: Sequence[str],
    allow_credentials: bool,
) -> dict[str, str]:
    """Manually attach CORS headers to fallback 500 responses, matching Starlette ``CORSMiddleware.send()``.

    ``ServerErrorMiddleware`` sends unexpected-exception 500 responses using the outermost original
    ``send`` (closure argument), bypassing every inner middleware send wrapper, including the inner
    ``CORSMiddleware``. Cross-origin frontends therefore see only a network error rather than the actual
    500 body. Changing middleware registration order has no effect (``Exception``/500 always routes to
    ``ServerErrorMiddleware``); the handler must use the app's CORS configuration to
    calculate and attach headers manually.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return {}

    allow_all_origins = "*" in allow_origins
    headers: dict[str, str] = {}
    if allow_all_origins:
        headers["Access-Control-Allow-Origin"] = "*"
    if allow_credentials:
        headers["Access-Control-Allow-Credentials"] = "true"

    if (allow_all_origins and allow_credentials) or (not allow_all_origins and origin in allow_origins):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"

    return headers


def register_error_handlers(
    app: FastAPI,
    *,
    cors_allow_origins: Sequence[str] = ("*",),
    cors_allow_credentials: bool = False,
) -> None:
    """Register all application-level exception handlers. Also works with bare ``FastAPI()`` in tests.

    ``cors_allow_origins``/``cors_allow_credentials`` must match the app's registered
    ``CORSMiddleware`` configuration (see ``_handle_unexpected`` explanations and the
    ``server/app.py`` call site); defaults represent the conservative no-CORS-configuration case.
    """

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        _t = get_translator(request)
        names = await request_display_names(request)
        content: dict[str, object] = {"detail": render_generation_input_error(exc.key, exc.params, _t, names)}
        if exc.diagnostic is not None:
            content["diagnostic"] = exc.diagnostic
        content = await present_request_diagnostics(content, request, _t)
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(HTTPException)
    async def _handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = await present_request_diagnostics(exc.detail, request, get_translator(request))
        return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        _t = get_translator(request)
        error_types = {error["type"] for error in exc.errors()}
        if "assistant_image_too_large" in error_types:
            return JSONResponse(status_code=422, content={"detail": _t("assistant_image_too_large")})
        if "assistant_images_total_too_large" in error_types:
            return JSONResponse(status_code=422, content={"detail": _t("assistant_images_total_too_large")})
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.exception_handler(TaskSpecValidationError)
    async def _handle_task_spec_error(request: Request, exc: TaskSpecValidationError) -> JSONResponse:
        _t = get_translator(request)
        names = await request_display_names(request)
        detail = await present_request_diagnostics(render_message(exc.code, exc.params, _t, names), request, _t)
        return JSONResponse(status_code=400, content={"detail": detail})

    @app.exception_handler(ModelNotInCatalogError)
    async def _handle_model_not_in_catalog(request: Request, exc: ModelNotInCatalogError) -> JSONResponse:
        # A built-in model referenced by settings or projects is retired or absent from the provider catalog: ask users to reselect, not a server error
        _t = get_translator(request)
        names = await request_display_names(request)
        message = render_message(failure_message_key(exc.code, exc.params), exc.params, _t, names)
        detail = await present_request_diagnostics(message, request, _t)
        return JSONResponse(status_code=400, content={"detail": detail})

    @app.exception_handler(ActiveTaskRequestConflict)
    async def _handle_active_task_request_conflict(
        request: Request,
        exc: ActiveTaskRequestConflict,
    ) -> JSONResponse:
        _t = get_translator(request)
        detail = await present_request_diagnostics(
            _t("video_request_conflicts_with_active_task", resource_id=exc.resource_id), request, _t
        )
        return JSONResponse(
            status_code=409,
            content={"detail": detail},
        )

    @app.exception_handler(ScriptEditError)
    async def _handle_script_edit_error(request: Request, exc: ScriptEditError) -> JSONResponse:
        # Invalid scripts (such as damaged storyboard-array keys) map to 4xx client errors
        _t = get_translator(request)
        detail = await present_request_diagnostics(script_edit_detail(exc, _t), request, _t)
        return JSONResponse(status_code=400, content={"detail": detail})

    @app.exception_handler(FileNotFoundError)
    async def _handle_file_not_found(request: Request, exc: FileNotFoundError) -> JSONResponse:
        # Do not return str(exc): load_script and similar exceptions contain absolute server paths, logged only
        logger.warning("资源不存在: %s %s (%s)", request.method, request.url.path, exc)
        _t = get_translator(request)
        return JSONResponse(status_code=404, content={"detail": _t("resource_not_found")})

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Unexpected-exception messages may contain server paths or other internal details; always use generic 500.
        # Starlette re-raises after sending this response; request_logging_middleware /
        # uvicorn logs the stack, so do not duplicate it here.
        #
        # ServerErrorMiddleware always sends this response through the outermost original send, bypassing
        # CORSMiddleware (see _cors_headers_for docs); attach CORS headers manually,
        # or cross-origin frontends interpret this 500 as a network error.
        _t = get_translator(request)
        headers = _cors_headers_for(
            request,
            allow_origins=cors_allow_origins,
            allow_credentials=cors_allow_credentials,
        )
        return JSONResponse(status_code=500, content={"detail": _t("internal_server_error")}, headers=headers)
