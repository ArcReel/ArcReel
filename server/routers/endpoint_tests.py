"""端点测试 API：预览请求、验证响应、测试连接。

三个独立端点，不用 ``mode`` 字段——三者的入参、代价与返回体各不相同，合成一个端点只会让「哪些
字段这次有意义」变成一段说明文字。定义一律内联（测试连接另可给 ``model_ref``），服务端不留草稿。

请求体收 JSON；带素材时改用 ``multipart/form-data``，同一份 JSON 放在 ``payload`` 字段，素材按
``inputs`` 的 ``source`` 名走文件字段（``start_image`` / ``end_image`` / ``reference_images`` /
``reference_audio_files``，后两者可重复）。素材用完即丢，不建临时上传端点、不引用项目资产。

定义不合法与渲染期错误统一 422 + ``diagnostic.errors[{path, code, message}]``，判定来自保存接口
那一个共享校验器：这里放行的定义，保存接口必然也放行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

# UploadFile 取 starlette 基类而非 fastapi 子类：这里对 Request.form() 解析结果做 isinstance
# 判定，解析器产出的是 starlette.datastructures.UploadFile，用子类判定会全部落空。
from starlette.datastructures import FormData, UploadFile

from arcreel_market_core.endpoint_definition import AssetData, DefinitionDiagnostics, validate_definition
from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.backends.image_errors import ImageCapabilityError
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.registry import PROVIDER_REGISTRY, ProviderMeta
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoint_test import (
    ASSET_SOURCES,
    EndpointTestAssets,
    EndpointTestCredentials,
    EndpointTestDefinitionError,
    EndpointTestMode,
    EndpointTestParameters,
    KindTestSupport,
    TrialRun,
    TrialRunBusyError,
    TrialRunManager,
    TrialRunTarget,
    artifact_media_type,
    check_response,
    parse_response_body,
    stage_report_payload,
    support_for_kind,
    supports_test_mode,
    trial_run_manager,
)
from lib.custom_provider.model_facts import model_entry
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides
from lib.db import async_session_factory, get_async_session
from lib.db.models.custom_provider import CustomProvider
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_result import problem_from_task_failure
from lib.generation.task_failure import encode_failure, parse_failure, render_failure
from lib.i18n.display_names import DisplayNames
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
from lib.video_runtime.assembly import VideoAssemblySpec, assemble_video
from lib.video_runtime.builtin import builtin_declarative_credentials
from lib.video_runtime.image_assembly import assemble_image
from lib.video_runtime.image_resolution import custom_image_spec
from lib.video_runtime.resolution import builtin_video_spec, custom_video_spec
from server.i18n import DisplayNamesCatalog, Translator

logger = logging.getLogger(__name__)

MAX_ASSET_BYTES = 15 * 1024 * 1024

MAX_ASSET_FILES = 16

_TESTABLE_MEDIA_TYPES = frozenset({"video", "image"})

router = APIRouter(tags=["Custom Endpoints"])


def get_trial_run_manager() -> TrialRunManager:
    """测试连接登记处的注入口。进程内单例，测试按依赖覆盖换成隔离的一台。"""
    return trial_run_manager()


def get_config_resolver() -> ConfigResolver:
    """``model_ref`` 装配 backend 用的配置解析器——它自开 session，与请求那条不共享事务。"""
    return ConfigResolver(async_session_factory)


class TestParametersInput(BaseModel):
    """调用参数，与 ``VideoGenerationRequest`` 同名。刻意没有 seed（产品界面无处设置）。"""

    model: str
    prompt: str = ""
    duration_seconds: int = 5
    aspect_ratio: str = "9:16"
    resolution: str | None = None
    generate_audio: bool = True

    def to_parameters(self) -> EndpointTestParameters:
        return EndpointTestParameters(
            model=self.model,
            prompt=self.prompt,
            duration_seconds=self.duration_seconds,
            aspect_ratio=self.aspect_ratio,
            resolution=self.resolution,
            generate_audio=self.generate_audio,
        )


class TestCredentialsInput(BaseModel):
    """凭证两版：``provider_id`` 读库，或内联 ``base_url`` + ``api_key``。"""

    provider_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ModelRefInput(BaseModel):
    """模型行引用：``provider_id`` 取规范形（内置如 ``kling``，自定义如 ``custom-3``）。"""

    provider_id: str
    model_id: str


class PreviewRequestInput(BaseModel):
    definition: Any
    parameters: TestParametersInput
    credentials: TestCredentialsInput | None = None


class CheckResponseInput(BaseModel):
    definition: Any
    stage: Literal["submit", "poll", "result"]
    response_body: Any = None


class TrialRunInput(BaseModel):
    definition: Any = None
    model_ref: ModelRefInput | None = None
    parameters: TestParametersInput
    credentials: TestCredentialsInput | None = None


class PreviewedRequestResponse(BaseModel):
    method: str
    url: str
    headers: dict[str, str]
    body: Any = None


class PreviewResponse(BaseModel):
    """渲染后的各节请求，外加「这份请求是怎么算出来的」。

    ``conversions`` 的形状由定义的 ``kind`` 决定：声明式端点的请求全部来自模板直填、没有可说明
    的换算，为 null；ComfyUI 端点给出尺寸 / 帧数 / 种子的换算与这次改图删掉的节点。
    """

    submit: PreviewedRequestResponse
    poll: PreviewedRequestResponse
    result: PreviewedRequestResponse | None = None
    conversions: dict[str, Any] | None = None


class TrialRunResponse(BaseModel):
    id: str
    status: str
    provider: str
    model: str
    media_type: str = "video"
    created_at: float
    finished_at: float | None = None
    api_call_id: int | None = None
    provider_job_id: str | None = None
    stages: dict[str, str] = {}
    request: dict[str, Any] | None = None
    submit_response: Any = None
    poll_responses: list[Any] = []
    result_response: Any = None
    extractions: dict[str, Any] = {}
    video_url: str | None = None
    duration_seconds: int | None = None
    error: str | None = None
    error_code: str | None = None
    error_action: str | None = None
    has_artifact: bool = False


async def _parse_body[T: BaseModel](request: Request, model: type[T]) -> tuple[T, EndpointTestAssets]:
    """收 JSON 或 multipart。multipart 时 ``payload`` 字段是同一份 JSON，其余字段是素材。"""
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        return _validated(model, await _json_body(request)), EndpointTestAssets()
    form = await request.form()
    raw = form.get("payload")
    if not isinstance(raw, str):
        raise BadRequestError("endpoint_test_payload_required")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise BadRequestError("endpoint_test_payload_invalid") from exc
    assets = await _read_assets(form)
    return _validated(model, payload), assets


async def _json_body(request: Request) -> object:
    try:
        return await request.json()
    except ValueError as exc:
        raise BadRequestError("endpoint_test_payload_invalid") from exc


def _validated[T: BaseModel](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload)
    except ValueError as exc:
        raise BadRequestError("endpoint_test_payload_invalid").with_diagnostic(str(exc)) from exc


async def _read_assets(form: FormData) -> EndpointTestAssets:
    if sum(1 for _name, item in form.multi_items() if isinstance(item, UploadFile)) > MAX_ASSET_FILES:
        raise BadRequestError("endpoint_test_too_many_assets", limit=MAX_ASSET_FILES)
    by_source: dict[str, AssetData | Sequence[AssetData] | None] = {}
    filenames: dict[str, list[str]] = {}
    for source, is_list in ASSET_SOURCES.items():
        uploads = [item for item in form.getlist(source) if isinstance(item, UploadFile)]
        loaded = [await _read_upload(upload) for upload in uploads]
        by_source[source] = loaded if is_list else (loaded[0] if loaded else None)
        filenames[source] = [PureWindowsPath(upload.filename or "").name for upload in uploads]
    return EndpointTestAssets(by_source=by_source, filenames=filenames)


async def _read_upload(upload: UploadFile) -> AssetData:
    content = await upload.read(MAX_ASSET_BYTES + 1)
    if len(content) > MAX_ASSET_BYTES:
        raise BadRequestError(
            "endpoint_test_asset_too_large",
            name=upload.filename or "asset",
            limit_mb=MAX_ASSET_BYTES // (1024 * 1024),
        )
    return AssetData(upload.content_type or "application/octet-stream", content)


def _accepted_definition(definition: object, _t: Translator, *, mode: EndpointTestMode) -> dict[str, Any]:
    """过共享校验器，再按定义的 ``kind`` 判这种模式支不支持。

    校验与保存接口同一份判定、同一套错误码，端点测试不另设门槛。「这种 kind 没有这种模式」不是
    定义有错，故不混进定义诊断，按一条 400 单独拒绝。
    """
    diagnostics = validate_definition(definition)
    if diagnostics.errors or not isinstance(definition, dict):
        raise _invalid(diagnostics, _t)
    kind = str(definition["kind"])
    if not supports_test_mode(kind, mode):
        raise BadRequestError("endpoint_test_mode_unsupported_for_kind", kind=kind)
    return definition


def _support(definition: Mapping[str, Any]) -> KindTestSupport:
    """这份定义所属 kind 的端点测试实现。定义已过 :func:`_accepted_definition` 的模式闸。"""
    return support_for_kind(str(definition["kind"]))


def _invalid(diagnostics: DefinitionDiagnostics, _t: Translator) -> UnprocessableError:
    return UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(diagnostics.to_payload(_t))


async def _resolve_credentials(
    body_credentials: TestCredentialsInput | None,
    session: AsyncSession,
    *,
    require_base_url: bool = True,
    require_api_key: bool = True,
) -> EndpointTestCredentials | None:
    """凭证两版归一。``provider_id`` 只接受自定义供应商——内置供应商的凭证不按端点定义调用。

    ``require_base_url`` 为 False 时（定义三节 URL 都写死绝对地址）内联凭证不必带接口地址，
    ``require_api_key`` 为 False 时（``auth`` 为空）不必带 api_key：那种定义没有一处会用到
    对应凭证，逼调用方编一个假值填进来没有意义。
    """
    if body_credentials is None:
        return None
    if body_credentials.provider_id:
        if body_credentials.base_url or body_credentials.api_key:
            raise BadRequestError("endpoint_test_credentials_ambiguous")
        provider = await _custom_provider(body_credentials.provider_id, session)
        if (require_base_url and not provider.base_url) or (require_api_key and not provider.api_key):
            return None
        return EndpointTestCredentials(base_url=provider.base_url or "", api_key=provider.api_key or "")
    if (body_credentials.base_url or not require_base_url) and (
        body_credentials.api_key is not None or not require_api_key
    ):
        return EndpointTestCredentials(base_url=body_credentials.base_url or "", api_key=body_credentials.api_key or "")
    return None


async def _required_credentials(
    body_credentials: TestCredentialsInput | None,
    session: AsyncSession,
    *,
    require_base_url: bool = True,
    require_api_key: bool = True,
) -> EndpointTestCredentials:
    """测试连接必须有定义用得上的凭证：没有它们的请求本就发不出去，让它排到后台任务里再失败
    没有意义。定义两种凭证都用不上（``auth`` 为空且三节 URL 全写死绝对地址）时按空凭证放行。"""
    credentials = await _resolve_credentials(
        body_credentials, session, require_base_url=require_base_url, require_api_key=require_api_key
    )
    if credentials is None:
        if not require_base_url and not require_api_key:
            return EndpointTestCredentials(base_url="", api_key="")
        raise BadRequestError("endpoint_test_credentials_required")
    return credentials


async def _custom_provider(provider_id: str, session: AsyncSession) -> CustomProvider:
    if not is_custom_provider(provider_id):
        raise BadRequestError("endpoint_test_provider_not_custom", provider_id=provider_id)
    try:
        row_id = parse_provider_id(provider_id)
    except ValueError as exc:
        raise BadRequestError("endpoint_test_provider_not_custom", provider_id=provider_id) from exc
    provider = await CustomProviderRepository(session).get_provider(row_id)
    if provider is None:
        raise NotFoundError("provider_not_found")
    return provider


@router.post("/preview-request")
async def preview_endpoint_request(
    request: Request,
    _t: Translator,
    names: DisplayNamesCatalog,
    session: AsyncSession = Depends(get_async_session),
) -> PreviewResponse:
    """渲染将要发出的请求，不外发。凭证打码、素材换成体积摘要、提交后才有的 id 保持占位符。"""
    body, assets = await _parse_body(request, PreviewRequestInput)
    definition = _accepted_definition(body.definition, _t, mode=EndpointTestMode.PREVIEW_REQUEST)
    support = _support(definition)
    require_base_url, require_api_key = support.credential_needs(definition)
    credentials = await _resolve_credentials(
        body.credentials, session, require_base_url=require_base_url, require_api_key=require_api_key
    )
    try:
        preview = support.preview(definition, body.parameters.to_parameters(), credentials=credentials, assets=assets)
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    except (ComfyuiError, ImageCapabilityError, VideoCapabilityError) as exc:
        raise BadRequestError(
            "endpoint_test_preview_failed", detail=render_failure(encode_failure(exc.code, **exc.params), _t, names)
        ) from exc
    return PreviewResponse(
        submit=_previewed(preview.submit),
        poll=_previewed(preview.poll),
        result=_previewed(preview.result) if preview.result else None,
        conversions=dict(preview.conversions) if preview.conversions is not None else None,
    )


@router.post("/check-response")
async def check_endpoint_response(
    body: CheckResponseInput,
    _t: Translator,
) -> dict[str, Any]:
    """用一份供应商真实响应验证取值路径与状态映射，不外发。零费用。"""
    definition = _accepted_definition(body.definition, _t, mode=EndpointTestMode.CHECK_RESPONSE)
    try:
        report = check_response(definition, body.stage, parse_response_body(body.response_body))
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    return stage_report_payload(report)


@router.post("/trial-runs", status_code=201)
async def start_trial_run(
    request: Request,
    _t: Translator,
    names: DisplayNamesCatalog,
    session: AsyncSession = Depends(get_async_session),
    manager: TrialRunManager = Depends(get_trial_run_manager),
    resolver: ConfigResolver = Depends(get_config_resolver),
) -> TrialRunResponse:
    """真实提交一次生成并轮询到终态，**会产生费用**。接口不设确认参数——明示计费归调用方。

    定义内联或 ``model_ref`` 二选一：后者按模型行解析端点与凭证，内置与自定义走同一资源、同一
    结果体、同一记账。每用户同时只允许一个。
    """
    body, assets = await _parse_body(request, TrialRunInput)
    parameters = body.parameters.to_parameters()
    if body.model_ref is not None and body.definition is not None:
        raise BadRequestError("endpoint_test_definition_and_model_ref_exclusive")
    if body.model_ref is not None:
        target, credentials, definition, path_overrides = await _model_ref_target(body.model_ref, session, resolver, _t)
        parameters = replace(parameters, model=body.model_ref.model_id)
    elif body.definition is not None:
        path_overrides = NO_PATH_OVERRIDES
        definition = _accepted_definition(body.definition, _t, mode=EndpointTestMode.TRIAL_RUN)
        support = _support(definition)
        require_base_url, require_api_key = support.credential_needs(definition)
        credentials = await _required_credentials(
            body.credentials, session, require_base_url=require_base_url, require_api_key=require_api_key
        )
        target = support.trial_target(definition, credentials, parameters)
    else:
        raise BadRequestError("endpoint_test_definition_or_model_ref_required")

    preview_payload = _preview_payload(definition, parameters, credentials, assets, _t, path_overrides)
    staged: list[Path] = []
    try:
        persisted, sources = await run_sync_transaction(_stage_assets, assets, staged)
        run = await manager.start(
            target,
            parameters,
            assets=persisted,
            asset_sources=sources,
            staging=staged[0],
            request_preview=preview_payload,
        )
    except TrialRunBusyError as exc:
        await run_sync_transaction(_remove_staging, staged)
        raise ConflictError("trial_run_already_running") from exc
    except BaseException:
        await run_sync_transaction(_remove_staging, staged)
        raise
    return _run_response(run, _t, names)


@router.get("/trial-runs/{run_id}")
async def get_trial_run(
    run_id: str,
    _t: Translator,
    names: DisplayNamesCatalog,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> TrialRunResponse:
    """读一次测试连接。运行中读内存、终态读盘；取消或被重启打断的 run 不留结果。"""
    run = await asyncio.to_thread(manager.get, run_id)
    if run is None:
        raise NotFoundError("trial_run_not_found")
    return _run_response(run, _t, names)


@router.post("/trial-runs/{run_id}/cancel", status_code=204)
async def cancel_trial_run(
    run_id: str,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> Response:
    """停本地轮询，记账按失败结算。远端由 backend 的取消路径自己叫停，停不下来时照跑、钱照花。"""
    if not await manager.cancel(run_id):
        raise NotFoundError("trial_run_not_found")
    return Response(status_code=204)


@router.get("/trial-runs/{run_id}/artifact")
async def get_trial_run_artifact(
    run_id: str,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> FileResponse:
    """播放该测试连接生成的产物。

    MIME 按落盘字节的文件头给，不按文件名：产物名是本层起的（视频 ``artifact.mp4``、图像
    ``artifact.png``），而 ComfyUI 那一侧导出的容器可以是同族里的另一个，按名字声明会让浏览器
    按一个错误的类型解。
    """
    located = await asyncio.to_thread(_locate_artifact, manager, run_id)
    if located is None:
        raise NotFoundError("trial_run_artifact_not_found")
    path, media_type = located
    return FileResponse(path, media_type=media_type)


def _previewed(section: Any) -> PreviewedRequestResponse:
    return PreviewedRequestResponse(method=section.method, url=section.url, headers=section.headers, body=section.body)


def _run_response(run: TrialRun, translate: Translator, names: DisplayNames) -> TrialRunResponse:
    """结果体本地化：失败原因渲染成当前语言，失败码与它的后续动作另给一份原文。

    码与动作一并给出，而不是让读侧从文案里认：文案随语言变，而「这是哪一类失败、该去做什么」
    按码分流。动作取自 :func:`problem_from_task_failure`——同一条失败码在项目页与这里该给出
    同一个后续动作，两处各写一份必然分叉（ComfyUI 的八条码里就有三条指向重试而非配置供应商）。
    编不出码的裸异常文本两者皆无。
    """
    payload = run.to_payload()
    parsed = parse_failure(run.error)
    payload["error"] = render_failure(run.error, translate, names)
    payload["error_code"] = parsed[0] if parsed else None
    payload["error_action"] = problem_from_task_failure(run.error).action.value if parsed else None
    return TrialRunResponse(**payload)


def _has_usable_credentials(meta: ProviderMeta, config: Mapping[str, str | None]) -> bool:
    """凭证够不够装配用。分组语义与凭证表单一致：声明了 credential_groups 按二选一，否则
    required ∩ secret 全部必填；两者都给不出非空分组（如走文件上传的 vertex）时不在此设闸。"""
    groups = [
        group
        for group in (meta.credential_groups or [[key for key in meta.required_keys if key in meta.secret_keys]])
        if group
    ]
    return not groups or any(all(config.get(key) for key in group) for group in groups)


async def _model_ref_target(
    model_ref: ModelRefInput,
    session: AsyncSession,
    resolver: ConfigResolver,
    _t: Translator,
) -> tuple[TrialRunTarget, EndpointTestCredentials | None, dict[str, Any] | None, PathOverrides]:
    """按模型行解析目标。解析出声明式定义时连凭证一起取出，好让结果体有请求与提取两段。

    自定义供应商的模型行还带出它生效的路径覆盖（``docs/adr/0101``），渲染请求段与真发一致。
    """

    def video_target(spec: VideoAssemblySpec, definition, entry):
        async def build():
            catalog.reject_discontinued_entry(model_ref.provider_id, model_ref.model_id, "video", entry)
            return await assemble_video(spec)

        return TrialRunTarget(model_ref.provider_id, model_ref.model_id, build, definition, snapshot=spec.snapshot)

    if not is_custom_provider(model_ref.provider_id):
        identity = ProviderModel(model_ref.provider_id, model_ref.model_id)
        try:
            builtin = await builtin_video_spec(resolver, identity, submitted=True)
        except ModelNotInCatalogError as exc:
            if catalog.get_model(identity.provider_id, identity.model_id) is None:
                raise NotFoundError("model_not_found") from exc
            raise BadRequestError("endpoint_test_model_unavailable") from exc
        config = builtin.config.credentials
        if not _has_usable_credentials(PROVIDER_REGISTRY[model_ref.provider_id], config):
            raise BadRequestError("missing_credentials")
        definition = None
        credentials = None
        if builtin.definition is not None:
            definition = _accepted_definition(dict(builtin.definition), _t, mode=EndpointTestMode.TRIAL_RUN)
            decl_base_url, decl_api_key = builtin_declarative_credentials(builtin.config)
            credentials = EndpointTestCredentials(base_url=decl_base_url, api_key=decl_api_key)
        return video_target(builtin, definition, builtin.entry), credentials, definition, NO_PATH_OVERRIDES
    provider = await _custom_provider(model_ref.provider_id, session)
    model = await CustomProviderRepository(session).get_model_by_ids(provider.id, model_ref.model_id)
    if model is None:
        raise NotFoundError("model_not_found")
    try:
        endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        endpoint_spec = None
    if not model.is_enabled or endpoint_spec is None or endpoint_spec.media_type not in _TESTABLE_MEDIA_TYPES:
        raise BadRequestError("endpoint_test_model_unavailable")
    if endpoint_spec.definition is not None:
        require_base_url, require_api_key = _support(endpoint_spec.definition).credential_needs(
            endpoint_spec.definition
        )
        if require_base_url and not provider.base_url:
            raise BadRequestError("endpoint_test_provider_base_url_required")
        if require_api_key and not provider.api_key:
            raise BadRequestError("missing_credentials")
    definition = None
    if endpoint_spec.definition is not None:
        definition = _accepted_definition(dict(endpoint_spec.definition), _t, mode=EndpointTestMode.TRIAL_RUN)
    credentials = EndpointTestCredentials(base_url=provider.base_url, api_key=provider.api_key)
    if endpoint_spec.media_type == "video":
        entry = None if endpoint_spec.kind == "comfyui" else model_entry(model.model_id, "video", model.entry_binding)
        spec = custom_video_spec(provider, model, endpoint_spec, entry)
        return video_target(spec, definition, spec.entry), credentials, definition, spec.paths

    entry = None if endpoint_spec.kind == "comfyui" else model_entry(model.model_id, "image", model.entry_binding)
    image_spec = custom_image_spec(provider, model, endpoint_spec, entry)

    async def build_image():
        catalog.reject_discontinued_entry(model_ref.provider_id, model_ref.model_id, "image", entry)
        return await assemble_image(image_spec)

    target = TrialRunTarget(
        model_ref.provider_id,
        model_ref.model_id,
        build_image,
        definition,
        media_type="image",
        snapshot=image_spec.snapshot,
    )
    return target, credentials, definition, image_spec.paths


def _preview_payload(
    definition: Mapping[str, Any] | None,
    parameters: EndpointTestParameters,
    credentials: EndpointTestCredentials | None,
    assets: EndpointTestAssets,
    _t: Translator,
    path_overrides: PathOverrides = NO_PATH_OVERRIDES,
) -> dict[str, Any] | None:
    """结果体里的「渲染请求」段。

    顺带充当提交前的渲染闸：模板在真发之前就渲一次，占位符缺值这类错误当场回 422，而不是等
    后台任务跑起来才失败——那时用户已经在等一个注定失败的 run。没有定义可渲（Python 实现的
    端点）、或这种 kind 不给这一段（ComfyUI，见 ``KindTestSupport``）时留空。
    """
    if definition is None or credentials is None:
        return None
    support = _support(definition)
    if not support.trial_run_request_preview:
        return None
    try:
        preview = support.preview(
            definition,
            parameters,
            credentials=credentials,
            assets=assets,
            placeholder_missing_assets=False,
            path_overrides=path_overrides,
        )
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    return {
        "method": preview.submit.method,
        "url": preview.submit.url,
        "headers": preview.submit.headers,
        "body": preview.submit.body,
    }


def _locate_artifact(manager: TrialRunManager, run_id: str) -> tuple[Path, str] | None:
    path = manager.artifact_path(run_id)
    return None if path is None else (path, artifact_media_type(path))


def _stage_assets(
    assets: EndpointTestAssets, staged: list[Path]
) -> tuple[dict[str, Path | list[Path] | None], dict[Path, Path]]:
    staging = Path(tempfile.mkdtemp(prefix="arcreel-trial-run-"))
    staged.append(staging)
    persisted = _persist_assets(assets, staging)
    sources = {}
    for source in ("start_image", "end_image", "reference_images"):
        value = persisted[source]
        paths = value if isinstance(value, list) else ([value] if value is not None else [])
        for path, name in zip(paths, assets.filenames.get(source, ()), strict=False):
            sources[path] = Path(name)
    return persisted, sources


def _remove_staging(staged: list[Path]) -> None:
    for staging in staged:
        shutil.rmtree(staging, ignore_errors=True)  # hard-delete: temp -- 试跑的 mkdtemp 暂存


def _persist_assets(assets: EndpointTestAssets, staging: Path) -> dict[str, Path | list[Path] | None]:
    """素材落到临时目录：backend 收的是文件路径，而上传内容此刻只在内存里。目录随 run 终态清掉。"""
    persisted: dict[str, Path | list[Path] | None] = {}
    for source, is_list in ASSET_SOURCES.items():
        if is_list:
            persisted[source] = [
                _write_asset(staging, f"{source}_{index}", item) for index, item in enumerate(assets.items(source))
            ]
            continue
        single = assets.single(source)
        persisted[source] = _write_asset(staging, source, single) if single is not None else None
    return persisted


def _write_asset(staging: Path, name: str, asset: AssetData) -> Path:
    suffix = mimetypes.guess_extension(asset.mime_type) or ".bin"
    path = staging / f"{uuid.uuid4().hex}_{name}{suffix}"
    path.write_bytes(asset.content)
    return path
