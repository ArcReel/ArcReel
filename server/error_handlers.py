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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lib.api_errors import ApiError
from lib.generation_queue_client import TaskSpecValidationError
from lib.i18n import get_translator
from lib.script_editor import ScriptEditError

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    """注册全部 app 级异常处理器。测试中对 bare ``FastAPI()`` 同样适用。"""

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
        _t = get_translator(request)
        return JSONResponse(status_code=500, content={"detail": _t("internal_server_error")})
