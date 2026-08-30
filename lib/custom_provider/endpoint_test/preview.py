"""预览请求：按定义与参数渲染出将要发出的请求，一个字节都不外发。

渲染走的是运行时那一份 :func:`render_request`，因此预览出来的形状与真发出去的完全一致；差别只
在三处替换，且都不改模板语义：凭证以打码值进渲染上下文（api_key 占位符只允许出现在 auth 节，
打码因此天然只落在凭证注入点上，不碰 host / prompt / 请求体里恰好相同的无关子串）、素材换成
体积摘要、轮询节的 ``task_id`` / ``result_id`` 保持占位符原样（提交之前它们本就还不存在）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from lib.custom_provider.builtin_definitions import declarative_video_capabilities
from lib.custom_provider.declarative_backend import normalize_declarative_base_url
from lib.custom_provider.endpoint_definition import (
    RenderedRequest,
    TemplateRenderError,
    build_context,
    render_request,
)
from lib.video_frame_slots import resolve_first_frame_aspect_ratio

from .errors import EndpointTestDefinitionError
from .inputs import ASSET_SOURCES, EndpointTestAssets, EndpointTestCredentials, EndpointTestParameters

#: 无凭证预览时保持原样的占位符：预览的用途是对照供应商文档核字段，凭证缺席不该让请求形状塌掉。
UNRESOLVED_API_KEY = "{{ api_key }}"
UNRESOLVED_BASE_URL = "{{ base_url }}"

#: 轮询与二次取件节里提交后才有的值：预览恒保持占位符。
UNRESOLVED_TASK_ID = "{{ task_id }}"
UNRESOLVED_RESULT_ID = "{{ result_id }}"

_MASK_TAIL = 4
_MASK = "****"
#: auth.query 追加项做百分号编码，打码记号会变成 ``%2A%2A%2A%2A``；预览 URL 把它还原成惯用形。
_ENCODED_MASK = quote(_MASK, safe="")


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
    placeholder_missing_assets: bool = True,
) -> RequestPreview:
    """渲染 submit / poll / result 三节请求。定义须已过共享校验器。

    ``placeholder_missing_assets`` 只在独立预览为 True：那里未上传的素材按声明生成占位摘要，
    好让用户对照文档核字段。测试连接的结果体传 False——记录的必须是真发出去的形状，运行时对
    缺席素材是整个字段删除。
    """
    api_key = _masked_api_key(credentials.api_key) if credentials else UNRESOLVED_API_KEY
    base_url = _preview_base_url(definition, credentials)
    inputs = asset_summaries(definition.get("inputs") or {}, assets, placeholder_missing=placeholder_missing_assets)
    # 渲染出的请求要与真发的一致：声明 first_frame_ratio_adaptive_only 的端点在带首帧的请求上
    # 只接受 adaptive，按实际渲进请求的首帧有无施加同一条覆盖。
    aspect_ratio = resolve_first_frame_aspect_ratio(
        caps=declarative_video_capabilities(definition),
        aspect_ratio=parameters.aspect_ratio,
        has_first_frame=any(
            inputs.get(name) is not None
            for name, declaration in (definition.get("inputs") or {}).items()
            if declaration.get("source") == "start_image"
        ),
    )
    context = build_context(
        {
            "api_key": api_key,
            "base_url": base_url,
            "model": parameters.model,
            "prompt": parameters.prompt,
            "duration": parameters.duration_seconds,
            "duration_seconds": parameters.duration_seconds,
            "aspect_ratio": aspect_ratio,
            "resolution": parameters.resolution,
            "generate_audio": parameters.generate_audio,
            "seed": None,
            "task_id": UNRESOLVED_TASK_ID,
            "result_id": UNRESOLVED_RESULT_ID,
        },
        inputs,
        definition.get("defaults"),
    )
    return RequestPreview(
        submit=_preview_section(definition, "submit", context),
        poll=_preview_section(definition, "poll", context),
        result=_preview_section(definition, "result", context) if "result" in definition else None,
    )


def asset_summaries(
    declarations: Mapping[str, Mapping[str, str]],
    assets: EndpointTestAssets | None,
    *,
    placeholder_missing: bool = True,
) -> dict[str, object]:
    """把素材换成 ``<data:image/png;base64, 1234 bytes>`` 这样的摘要。

    ``placeholder_missing`` 为 True 时（独立预览），未上传的来源按声明生成占位摘要而不是留空：
    留空会让 ``$when`` 守卫与整串占位符把整个字段删掉，预览出来的请求就少了一节，而真发时用户
    是会带上素材的。为 False 时（测试连接结果体）保持缺席为 ``None``——与运行时
    ``encode_inputs`` 同形，字段照真发那样被删除。
    """
    summaries: dict[str, object] = {}
    for name, declaration in declarations.items():
        source = declaration["source"]
        encoding = declaration["encoding"]
        if ASSET_SOURCES.get(source, False):
            items = assets.items(source) if assets else []
            if items:
                summaries[name] = [_summary(encoding, item.mime_type, len(item.content)) for item in items]
            else:
                summaries[name] = [_placeholder_summary(encoding, source)] if placeholder_missing else None
            continue
        raw = assets.single(source) if assets else None
        if raw is not None:
            summaries[name] = _summary(encoding, raw.mime_type, len(raw.content))
        else:
            summaries[name] = _placeholder_summary(encoding, source) if placeholder_missing else None
    return summaries


def _masked_api_key(api_key: str) -> str:
    """凭证的打码形：``****`` 加尾 4 位。空串原样返回——空凭证没有可打码的内容。"""
    if not api_key:
        return api_key
    return f"{_MASK}{api_key[-_MASK_TAIL:]}" if len(api_key) > _MASK_TAIL else _MASK


def _preview_base_url(definition: Mapping[str, Any], credentials: EndpointTestCredentials | None) -> str:
    # 归一化与运行时同一份：定义带显式版本段时剥掉配置末尾的版本段，预览与真发的 URL 必须一致。
    if credentials:
        return normalize_declarative_base_url(credentials.base_url, definition)
    meta = definition.get("meta")
    hints = meta.get("hints") if isinstance(meta, Mapping) else None
    hinted = hints.get("base_url") if isinstance(hints, Mapping) else None
    if isinstance(hinted, str) and hinted:
        return normalize_declarative_base_url(hinted, definition)
    return UNRESOLVED_BASE_URL


def _preview_section(
    definition: Mapping[str, Any],
    section: str,
    context: Mapping[str, object],
) -> PreviewedRequest:
    rendered = _render(definition, section, context)
    return PreviewedRequest(
        method=rendered.method,
        # 只把百分号编码后的打码记号还原成 ****，不对凭证本身做任何子串替换。
        url=rendered.url.replace(_ENCODED_MASK, _MASK),
        headers=dict(rendered.headers),
        body=rendered.body,
    )


def _render(definition: Mapping[str, Any], section: str, context: Mapping[str, object]) -> RenderedRequest:
    try:
        return render_request(
            definition[section],
            context,
            enum_maps=definition.get("enum_maps"),
            auth=definition.get("auth"),
        )
    except TemplateRenderError as exc:
        raise EndpointTestDefinitionError.from_render_failure(section, exc) from exc
    except (KeyError, TypeError, ValueError) as exc:
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
