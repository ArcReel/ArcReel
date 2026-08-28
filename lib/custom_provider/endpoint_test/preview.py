"""预览请求：按定义与参数渲染出将要发出的请求，一个字节都不外发。

渲染走的是运行时那一份 :func:`render_request`，因此预览出来的形状与真发出去的完全一致；差别只
在三处替换，且都发生在渲染**之后**，不改模板语义：凭证打码、素材换成体积摘要、轮询节的
``task_id`` / ``result_id`` 保持占位符原样（提交之前它们本就还不存在）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from lib.custom_provider.endpoint_definition import (
    RenderedRequest,
    TemplateRenderError,
    build_context,
    render_request,
)

from .errors import EndpointTestDefinitionError
from .inputs import ASSET_SOURCES, EndpointTestAssets, EndpointTestCredentials, EndpointTestParameters

#: 无凭证预览时保持原样的占位符：预览的用途是对照供应商文档核字段，凭证缺席不该让请求形状塌掉。
UNRESOLVED_API_KEY = "{{ api_key }}"
UNRESOLVED_BASE_URL = "{{ base_url }}"

#: 轮询与二次取件节里提交后才有的值：预览恒保持占位符。
UNRESOLVED_TASK_ID = "{{ task_id }}"
UNRESOLVED_RESULT_ID = "{{ result_id }}"

_MASK_TAIL = 4


@dataclass(frozen=True)
class PreviewedRequest:
    """渲染并脱敏后的一节请求。"""

    method: str
    url: str
    headers: dict[str, str]
    body: object | None


@dataclass(frozen=True)
class RequestPreview:
    """一次预览的全部产出：提交、轮询，以及定义声明了二次取件节时的取件请求。"""

    submit: PreviewedRequest
    poll: PreviewedRequest
    result: PreviewedRequest | None


def preview_request(
    definition: Mapping[str, Any],
    parameters: EndpointTestParameters,
    *,
    credentials: EndpointTestCredentials | None = None,
    assets: EndpointTestAssets | None = None,
) -> RequestPreview:
    """渲染 submit / poll / result 三节请求。定义须已过共享校验器。"""
    api_key = credentials.api_key if credentials else UNRESOLVED_API_KEY
    base_url = _preview_base_url(definition, credentials)
    context = build_context(
        {
            "api_key": api_key,
            "base_url": base_url,
            "model": parameters.model,
            "prompt": parameters.prompt,
            "duration": parameters.duration_seconds,
            "duration_seconds": parameters.duration_seconds,
            "aspect_ratio": parameters.aspect_ratio,
            "resolution": parameters.resolution,
            "generate_audio": parameters.generate_audio,
            "seed": None,
            "task_id": UNRESOLVED_TASK_ID,
            "result_id": UNRESOLVED_RESULT_ID,
        },
        asset_summaries(definition.get("inputs") or {}, assets),
    )
    secret = api_key if credentials else None
    return RequestPreview(
        submit=_preview_section(definition, "submit", context, secret),
        poll=_preview_section(definition, "poll", context, secret),
        result=_preview_section(definition, "result", context, secret) if "result" in definition else None,
    )


def asset_summaries(
    declarations: Mapping[str, Mapping[str, str]],
    assets: EndpointTestAssets | None,
) -> dict[str, object]:
    """把素材换成 ``<data:image/png;base64, 1234 bytes>`` 这样的摘要。

    未上传的来源按声明生成占位摘要而不是留空：留空会让 ``$when`` 守卫与整串占位符把整个字段
    删掉，预览出来的请求就少了一节，而真发时用户是会带上素材的。
    """
    summaries: dict[str, object] = {}
    for name, declaration in declarations.items():
        source = declaration["source"]
        encoding = declaration["encoding"]
        if ASSET_SOURCES.get(source, False):
            items = assets.items(source) if assets else []
            summaries[name] = (
                [_summary(encoding, item.mime_type, len(item.content)) for item in items]
                if items
                else [_placeholder_summary(encoding, source)]
            )
            continue
        raw = assets.single(source) if assets else None
        summaries[name] = (
            _summary(encoding, raw.mime_type, len(raw.content))
            if raw is not None
            else _placeholder_summary(encoding, source)
        )
    return summaries


def mask_secret_in(value: object, secret: str | None) -> object:
    """把结构里出现的凭证换成 ``****`` 加尾 4 位；URL 上百分号编码过的那份一并换掉。"""
    if not secret:
        return value
    masked = f"****{secret[-_MASK_TAIL:]}" if len(secret) > _MASK_TAIL else "****"
    encoded = quote(secret, safe="")
    return _replace(value, {secret: masked, encoded: masked})


def _preview_base_url(definition: Mapping[str, Any], credentials: EndpointTestCredentials | None) -> str:
    if credentials:
        return credentials.base_url.rstrip("/")
    meta = definition.get("meta")
    hints = meta.get("hints") if isinstance(meta, Mapping) else None
    hinted = hints.get("base_url") if isinstance(hints, Mapping) else None
    return str(hinted).rstrip("/") if isinstance(hinted, str) and hinted else UNRESOLVED_BASE_URL


def _preview_section(
    definition: Mapping[str, Any],
    section: str,
    context: Mapping[str, object],
    secret: str | None,
) -> PreviewedRequest:
    rendered = _render(definition, section, context)
    return PreviewedRequest(
        method=rendered.method,
        url=str(mask_secret_in(rendered.url, secret)),
        headers={name: str(mask_secret_in(value, secret)) for name, value in rendered.headers.items()},
        body=mask_secret_in(rendered.body, secret),
    )


def _render(definition: Mapping[str, Any], section: str, context: Mapping[str, object]) -> RenderedRequest:
    try:
        return render_request(
            definition[section],
            context,
            enum_maps=definition.get("enum_maps"),
            auth=definition.get("auth"),
        )
    except (KeyError, TypeError, ValueError, TemplateRenderError) as exc:
        raise EndpointTestDefinitionError.from_render_failure(section, str(exc)) from exc


def _summary(encoding: str, mime_type: str, size: int) -> str:
    return f"<{_encoding_label(encoding, mime_type)}, {size} bytes>"


def _placeholder_summary(encoding: str, source: str) -> str:
    return f"<{_encoding_label(encoding, _PLACEHOLDER_MIME_TYPES[source])}, {source} not uploaded>"


def _encoding_label(encoding: str, mime_type: str) -> str:
    return f"data:{mime_type};base64" if encoding == "data_uri" else "base64"


_PLACEHOLDER_MIME_TYPES = {
    "start_image": "image/png",
    "end_image": "image/png",
    "reference_images": "image/png",
    "reference_audio_files": "audio/mpeg",
}


def _replace(value: object, table: Mapping[str, str]) -> object:
    if isinstance(value, str):
        for needle, replacement in table.items():
            value = value.replace(needle, replacement)
        return value
    if isinstance(value, list):
        return [_replace(item, table) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, table) for key, item in value.items()}
    return value
