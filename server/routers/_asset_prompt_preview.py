"""项目资产的只读草稿预览路由，随资产 router 继承注册处的认证。"""

import asyncio
from collections.abc import Callable

from fastapi import APIRouter
from pydantic import BaseModel

from lib.infra.api_errors import NotFoundError
from lib.project.asset_derivatives import resolve_derivative_target
from lib.project.asset_types import AssetSpec, resolve_asset_key
from lib.project.project_manager import ProjectManager
from server.i18n import Translator
from server.services.admission.asset_prompt_preview import render_asset_prompt
from server.services.admission.prompt_preview import UNAVAILABLE_MISSING, RenderedPrompt


class AssetPromptPreviewRequest(BaseModel):
    description: str


def register_asset_prompt_preview_routes(
    router: APIRouter,
    *,
    spec: AssetSpec,
    pm_getter: Callable[[], ProjectManager],
) -> None:
    async def preview(
        project_name: str,
        entry_name: str,
        req: AssetPromptPreviewRequest,
        translate: Translator,
        derivative_name: str | None = None,
    ):
        def render() -> RenderedPrompt:
            project = pm_getter().load_project(project_name)
            bucket = project.get(spec.bucket_key)
            name = resolve_asset_key(bucket, entry_name)
            if name is None:
                return RenderedPrompt(unavailable=UNAVAILABLE_MISSING, is_text_form=True)
            asset_type = spec.asset_type
            if derivative_name is not None:
                try:
                    resolve_derivative_target(project, entry_name, derivative_name)
                except (KeyError, NotFoundError):
                    return RenderedPrompt(unavailable=UNAVAILABLE_MISSING, is_text_form=True)
                asset_type = "character_derivative"
            return render_asset_prompt(
                asset_type, name, req.description, project.get("style", ""), project.get("style_description", "")
            )

        try:
            result = await asyncio.to_thread(render)
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        return {
            "text": result.text,
            "unavailable": (
                translate(
                    "asset_prompt_preview_missing" if result.unavailable == UNAVAILABLE_MISSING else result.unavailable
                )
                if result.unavailable
                else None
            ),
            "is_text_form": result.is_text_form,
            "warnings": [translate(w["key"], **w["params"]) for w in result.warnings],
        }

    base = f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}"

    # 以下处理器由 @router.* 就地注册，模块内无其它引用；basedpyright 把函数作用域内的符号
    # 一律判为私有，逐个标注的 reportUnusedFunction 均为工具误报。
    @router.post(f"{base}/prompt-preview")
    async def preview_asset(  # pyright: ignore[reportUnusedFunction]
        project_name: str, entry_name: str, req: AssetPromptPreviewRequest, _t: Translator
    ):
        return await preview(project_name, entry_name, req, _t)

    if spec.supports_derivatives:

        @router.post(f"{base}/derivatives/{{derivative_name}}/prompt-preview")
        async def preview_derivative(  # pyright: ignore[reportUnusedFunction]
            project_name: str,
            entry_name: str,
            derivative_name: str,
            req: AssetPromptPreviewRequest,
            _t: Translator,
        ):
            return await preview(project_name, entry_name, req, _t, derivative_name)
