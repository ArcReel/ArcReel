"""app 级异常处理器：异常→状态码→detail 映射的单点。

路由函数体只保留 happy path，领域异常与 lib 层异常在此统一完成：

- 状态码映射（``ApiError`` 自带；lib 异常按类型固定）
- 按请求 ``Accept-Language`` 翻译（复用 ``get_translator``）
- 脱敏：除 i18n key 显式声明的 params 外，异常消息一律不回传客户端——
  ``FileNotFoundError`` / 未预期异常的 ``str(exc)`` 可能含服务器绝对路径，只进日志

渐进迁移：仍自行 try/except 的路由不受影响（异常不会传播到这里）；
迁移一个端点 = 删掉它的 except 阶梯，让异常自然传播。
"""

import logging
from collections.abc import Sequence

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lib.api_errors import ApiError
from lib.generation_queue_client import TaskSpecValidationError
from lib.i18n import get_translator
from lib.script_editor import ScriptEditError

logger = logging.getLogger(__name__)


def _cors_headers_for(
    request: Request,
    *,
    allow_origins: Sequence[str],
    allow_credentials: bool,
) -> dict[str, str]:
    """为兜底 500 响应手工补 CORS 头，逻辑对齐 Starlette ``CORSMiddleware.send()``。

    ``ServerErrorMiddleware`` 发送未预期异常的 500 响应时用的是最外层原始
    ``send``（闭包参数），绕过所有内层中间件的 send 包装——包括注册在更内层的
    ``CORSMiddleware``。跨域前端因此只会看到 network error，看不到真正的
    500 body。调整中间件注册顺序无效（``Exception``/500 恒定路由到
    ``ServerErrorMiddleware``），只能在 handler 内按与 app 一致的 CORS 配置
    手工计算并附加响应头。
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

    if allow_all_origins and allow_credentials:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    elif not allow_all_origins and origin in allow_origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"

    return headers


def register_error_handlers(
    app: FastAPI,
    *,
    cors_allow_origins: Sequence[str] = ("*",),
    cors_allow_credentials: bool = False,
) -> None:
    """注册全部 app 级异常处理器。测试中对 bare ``FastAPI()`` 同样适用。

    ``cors_allow_origins``/``cors_allow_credentials`` 须与 app 上实际注册的
    ``CORSMiddleware`` 配置一致（见 ``_handle_unexpected`` 内的说明与
    ``server/app.py`` 调用处），默认值对应「未配置 CORS」的保守场景。
    """

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        _t = get_translator(request)
        return JSONResponse(status_code=exc.status_code, content={"detail": _t(exc.key, **exc.params)})

    @app.exception_handler(TaskSpecValidationError)
    async def _handle_task_spec_error(request: Request, exc: TaskSpecValidationError) -> JSONResponse:
        _t = get_translator(request)
        return JSONResponse(status_code=400, content={"detail": _t(exc.code, **exc.params)})

    @app.exception_handler(ScriptEditError)
    async def _handle_script_edit_error(request: Request, exc: ScriptEditError) -> JSONResponse:
        # 脏脚本（分镜数组键损坏等）→ 4xx 客户端错误；reason 是结构性描述，不含服务器路径
        _t = get_translator(request)
        return JSONResponse(status_code=400, content={"detail": _t("script_data_corrupted", reason=str(exc))})

    @app.exception_handler(FileNotFoundError)
    async def _handle_file_not_found(request: Request, exc: FileNotFoundError) -> JSONResponse:
        # 不回传 str(exc)：load_script 等异常消息含服务器绝对路径，只进日志
        logger.warning("资源不存在: %s %s (%s)", request.method, request.url.path, exc)
        _t = get_translator(request)
        return JSONResponse(status_code=404, content={"detail": _t("resource_not_found")})

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 未预期异常的消息可能含服务器路径等内部细节，一律通用 500。
        # Starlette 发送本响应后会 re-raise，堆栈由 request_logging_middleware /
        # uvicorn 记录，此处不重复打印。
        #
        # ServerErrorMiddleware 恒定用最外层原始 send 发送本响应，绕过
        # CORSMiddleware（见 _cors_headers_for 文档）——手工补上 CORS 头，
        # 否则跨域前端会把这个 500 当成 network error。
        _t = get_translator(request)
        headers = _cors_headers_for(
            request,
            allow_origins=cors_allow_origins,
            allow_credentials=cors_allow_credentials,
        )
        return JSONResponse(status_code=500, content={"detail": _t("internal_server_error")}, headers=headers)
