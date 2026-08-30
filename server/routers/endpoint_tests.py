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

import json
import logging
import mimetypes
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData, UploadFile

from lib.api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.backend_assembly.specs import builtin_declarative_video_diagnostics
from lib.config.registry import PROVIDER_REGISTRY, ProviderMeta, model_info_for
from lib.config.resolver import ConfigResolver
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.custom_provider.endpoint_definition import AssetData, DefinitionDiagnostics, validate_definition
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoint_test import (
    ASSET_SOURCES,
    EndpointTestAssets,
    EndpointTestCredentials,
    EndpointTestDefinitionError,
    EndpointTestParameters,
    TrialRun,
    TrialRunBusyError,
    TrialRunManager,
    TrialRunTarget,
    check_response,
    declarative_target,
    model_ref_target,
    parse_response_body,
    preview_request,
    stage_report_payload,
    trial_run_manager,
)
from lib.custom_provider.endpoints import declarative_requires_api_key, declarative_requires_base_url
from lib.db import async_session_factory, get_async_session
from lib.db.models.custom_provider import CustomProvider
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.i18n import Translator
from lib.task_failure import render_failure

logger = logging.getLogger(__name__)

#: 单个素材文件的上限。测试用的首帧 / 参考图与真实生成同量级，与参考音频上限取同一档。
MAX_ASSET_BYTES = 15 * 1024 * 1024

#: 单次请求的素材文件总数上限：覆盖最重的合法组合（H3 的 9 参考图 + 首尾帧 + 3 段音频）仍有余量。
MAX_ASSET_FILES = 16

router = APIRouter(tags=["Custom Endpoints"])


def get_trial_run_manager() -> TrialRunManager:
    """测试连接登记处的注入口。进程内单例，测试按依赖覆盖换成隔离的一台。"""
    return trial_run_manager()


def get_config_resolver() -> ConfigResolver:
    """``model_ref`` 装配 backend 用的配置解析器——它自开 session，与请求那条不共享事务。"""
    return ConfigResolver(async_session_factory)


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------


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
    #: 供应商响应体原样。字符串按 JSON 试解析，解析不了就按原始字符串验证。
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
    submit: PreviewedRequestResponse
    poll: PreviewedRequestResponse
    result: PreviewedRequestResponse | None = None


class TrialRunResponse(BaseModel):
    id: str
    status: str
    provider: str
    model: str
    created_at: float
    finished_at: float | None = None
    api_call_id: int | None = None
    request: dict[str, Any] | None = None
    submit_response: Any = None
    poll_responses: list[Any] = []
    result_response: Any = None
    extractions: dict[str, Any] = {}
    video_url: str | None = None
    duration_seconds: int | None = None
    error: str | None = None
    has_artifact: bool = False


# ---------------------------------------------------------------------------
# 共用：请求解析与定义闸门
# ---------------------------------------------------------------------------


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
    # 件数上限按表单里的全部文件数判，不只数已登记的字段：换个字段名塞进来的文件同样已被
    # 解析、缓冲或落进临时盘，只数认得的字段等于给出一个换个 key 就绕开的上限。
    if sum(1 for _name, item in form.multi_items() if isinstance(item, UploadFile)) > MAX_ASSET_FILES:
        raise BadRequestError("endpoint_test_too_many_assets", limit=MAX_ASSET_FILES)
    by_source: dict[str, AssetData | Sequence[AssetData] | None] = {}
    for source, is_list in ASSET_SOURCES.items():
        uploads = [item for item in form.getlist(source) if isinstance(item, UploadFile)]
        loaded = [await _read_upload(upload) for upload in uploads]
        by_source[source] = loaded if is_list else (loaded[0] if loaded else None)
    return EndpointTestAssets(by_source=by_source)


async def _read_upload(upload: UploadFile) -> AssetData:
    # 只读到上限 +1 字节：超限件不整块载入内存就能判超。
    content = await upload.read(MAX_ASSET_BYTES + 1)
    if len(content) > MAX_ASSET_BYTES:
        raise BadRequestError(
            "endpoint_test_asset_too_large",
            name=upload.filename or "asset",
            limit_mb=MAX_ASSET_BYTES // (1024 * 1024),
        )
    return AssetData(upload.content_type or "application/octet-stream", content)


def _accepted_definition(definition: object, _t: Translator) -> dict[str, Any]:
    """过共享校验器。与保存接口同一份判定、同一套错误码，端点测试不另设门槛。"""
    diagnostics = validate_definition(definition)
    if diagnostics.errors or not isinstance(definition, dict):
        raise _invalid(diagnostics, _t)
    return definition


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
            # 混合两种来源时静默取其一，付费请求会打到调用方没明确选择的计费目标上。
            raise BadRequestError("endpoint_test_credentials_ambiguous")
        provider = await _custom_provider(body_credentials.provider_id, session)
        # 库里的行存了空值时与内联缺字段同判：缺的凭证要在请求线程上露头，而不是等后台任务
        # 渲染出一个空鉴权请求才失败。
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


# ---------------------------------------------------------------------------
# 预览请求 / 验证响应
# ---------------------------------------------------------------------------


@router.post("/preview-request")
async def preview_endpoint_request(
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> PreviewResponse:
    """渲染将要发出的请求，不外发。凭证打码、素材换成体积摘要、``task_id`` 保持占位符。"""
    body, assets = await _parse_body(request, PreviewRequestInput)
    definition = _accepted_definition(body.definition, _t)
    credentials = await _resolve_credentials(
        body.credentials,
        session,
        require_base_url=declarative_requires_base_url(definition),
        require_api_key=declarative_requires_api_key(definition),
    )
    try:
        preview = preview_request(definition, body.parameters.to_parameters(), credentials=credentials, assets=assets)
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    return PreviewResponse(
        submit=_previewed(preview.submit),
        poll=_previewed(preview.poll),
        result=_previewed(preview.result) if preview.result else None,
    )


@router.post("/check-response")
async def check_endpoint_response(
    body: CheckResponseInput,
    _t: Translator,
) -> dict[str, Any]:
    """用一份供应商真实响应验证取值路径与状态映射，不外发。零费用。"""
    definition = _accepted_definition(body.definition, _t)
    try:
        report = check_response(definition, body.stage, parse_response_body(body.response_body))
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    return stage_report_payload(report)


# ---------------------------------------------------------------------------
# 测试连接
# ---------------------------------------------------------------------------


@router.post("/trial-runs", status_code=201)
async def start_trial_run(
    request: Request,
    _t: Translator,
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
        # 两种目标形态并存时静默取其一会让付费请求打到调用方没选中的那个目标上。
        raise BadRequestError("endpoint_test_definition_and_model_ref_exclusive")
    if body.model_ref is not None:
        target, credentials, definition = await _model_ref_target(body.model_ref, session, resolver, _t)
        # 结果体记录的模型与实际执行、记账的必须是同一个：model_ref 路径以模型行为准，
        # 覆盖 parameters 里独立携带的 model。
        parameters = replace(parameters, model=body.model_ref.model_id)
    elif body.definition is not None:
        definition = _accepted_definition(body.definition, _t)
        credentials = await _required_credentials(
            body.credentials,
            session,
            require_base_url=declarative_requires_base_url(definition),
            require_api_key=declarative_requires_api_key(definition),
        )
        target = declarative_target(definition, credentials, parameters)
    else:
        raise BadRequestError("endpoint_test_definition_or_model_ref_required")

    preview_payload = _preview_payload(definition, parameters, credentials, assets, _t)
    staging = Path(tempfile.mkdtemp(prefix="arcreel-trial-run-"))
    try:
        run = await manager.start(
            target,
            parameters,
            assets=_persist_assets(assets, staging),
            staging=staging,
            request_preview=preview_payload,
        )
    except TrialRunBusyError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ConflictError("trial_run_already_running") from exc
    except Exception:
        # manager 接手前失败（落素材、建结果目录）时临时目录还没有主人，留在盘上没人会回来删。
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _run_response(run, _t)


@router.get("/trial-runs/{run_id}")
async def get_trial_run(
    run_id: str,
    _t: Translator,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> TrialRunResponse:
    """读一次测试连接。运行中读内存、终态读盘；取消或被重启打断的 run 不留结果。"""
    run = manager.get(run_id)
    if run is None:
        raise NotFoundError("trial_run_not_found")
    return _run_response(run, _t)


@router.post("/trial-runs/{run_id}/cancel", status_code=204)
async def cancel_trial_run(
    run_id: str,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> Response:
    """停本地轮询。不通知供应商，记账按失败结算——远端任务照跑，钱可能已经花了。"""
    if not await manager.cancel(run_id):
        raise NotFoundError("trial_run_not_found")
    return Response(status_code=204)


@router.get("/trial-runs/{run_id}/artifact")
async def get_trial_run_artifact(
    run_id: str,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> FileResponse:
    """播放该测试连接生成的产物。"""
    path = manager.artifact_path(run_id)
    if path is None:
        raise NotFoundError("trial_run_artifact_not_found")
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _previewed(section: Any) -> PreviewedRequestResponse:
    return PreviewedRequestResponse(method=section.method, url=section.url, headers=section.headers, body=section.body)


def _run_response(run: TrialRun, translate: Translator) -> TrialRunResponse:
    payload = run.to_payload()
    payload["error"] = render_failure(run.error, translate)
    return TrialRunResponse(**payload)


def _has_usable_credentials(meta: ProviderMeta, config: Mapping[str, str]) -> bool:
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
) -> tuple[TrialRunTarget, EndpointTestCredentials | None, dict[str, Any] | None]:
    """按模型行解析目标。解析出声明式定义时连凭证一起取出，好让结果体有请求与提取两段。"""
    if not is_custom_provider(model_ref.provider_id):
        # 内置供应商同样要先核模型行：未登记的 model 会让装配层落到该供应商的默认视频模型，
        # 已登记的非视频 model（如文本档）则会被派发到视频端点——两种都会付费打给另一个模型。
        info = model_info_for(model_ref.provider_id, model_ref.model_id)
        if info is None:
            raise NotFoundError("model_not_found")
        if info.media_type != "video":
            raise BadRequestError("endpoint_test_model_unavailable")
        # 凭证也在请求线程上核：装配层缺凭证时抛的是各 backend 自己写的中文句子，而它跑在
        # 脱离请求的后台任务里，会原样进结果体的 error 字段。
        config = await resolver.provider_config(model_ref.provider_id)
        if not _has_usable_credentials(PROVIDER_REGISTRY[model_ref.provider_id], config):
            raise BadRequestError("missing_credentials")
        # 内置模型行走声明式装配时，诊断两段（渲染请求、逐阶段提取）与提交前渲染闸照给：
        # 定义与凭证回落取装配同一份，Python backend 实现的行两段留空。
        definition = None
        credentials = None
        diagnostics = builtin_declarative_video_diagnostics(model_ref.provider_id, model_ref.model_id, config)
        if diagnostics is not None:
            builtin_definition, base_url, api_key = diagnostics
            definition = _accepted_definition(dict(builtin_definition), _t)
            credentials = EndpointTestCredentials(base_url=base_url, api_key=api_key)
        target = model_ref_target(model_ref.provider_id, model_ref.model_id, resolver=resolver, definition=definition)
        return target, credentials, definition
    provider = await _custom_provider(model_ref.provider_id, session)
    model = await CustomProviderRepository(session).get_model_by_ids(provider.id, model_ref.model_id)
    if model is None:
        raise NotFoundError("model_not_found")
    # 只查存在不够：已禁用或非视频端点的行会让装配层回退到该供应商的默认视频模型——
    # 付费测试打到并计费给另一个模型，而结果体仍标着请求的那一行。
    try:
        endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        endpoint_spec = None
    if not model.is_enabled or endpoint_spec is None or endpoint_spec.media_type != "video":
        raise BadRequestError("endpoint_test_model_unavailable")
    # 凭证配置错误在请求线程上就能判，按翻译过的 400 拒掉：缺 base_url 时装配层只会抛一句
    # 不可翻译的中文，且落在脱离请求的后台任务里、原样进结果体的 error 字段；缺 api_key 时
    # 请求会带着空鉴权头发出去，付费打一个注定被拒的调用。
    if endpoint_spec.definition is not None:
        if declarative_requires_base_url(endpoint_spec.definition) and not provider.base_url:
            raise BadRequestError("endpoint_test_provider_base_url_required")
        if declarative_requires_api_key(endpoint_spec.definition) and not provider.api_key:
            raise BadRequestError("missing_credentials")
    definition = None
    if endpoint_spec.definition is not None:
        # 内置声明式端点（newapi-video / v2 / minimax 系）与 ce-* 一样有定义：结果体的渲染请求、
        # 逐阶段提取与提交前渲染闸按同一条路给出，不因定义随版发布而缺一段诊断。
        definition = _accepted_definition(dict(endpoint_spec.definition), _t)
    credentials = EndpointTestCredentials(base_url=provider.base_url, api_key=provider.api_key)
    target = model_ref_target(model_ref.provider_id, model_ref.model_id, resolver=resolver, definition=definition)
    return target, credentials, definition


def _preview_payload(
    definition: Mapping[str, Any] | None,
    parameters: EndpointTestParameters,
    credentials: EndpointTestCredentials | None,
    assets: EndpointTestAssets,
    _t: Translator,
) -> dict[str, Any] | None:
    """结果体里的「渲染请求」段。

    顺带充当提交前的渲染闸：模板在真发之前就渲一次，占位符缺值这类错误当场回 422，而不是等
    后台任务跑起来才失败——那时用户已经在等一个注定失败的 run。没有定义可渲（Python 实现的
    端点）时留空。
    """
    if definition is None or credentials is None:
        return None
    try:
        preview = preview_request(
            definition, parameters, credentials=credentials, assets=assets, placeholder_missing_assets=False
        )
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    return {
        "method": preview.submit.method,
        "url": preview.submit.url,
        "headers": preview.submit.headers,
        "body": preview.submit.body,
    }


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
