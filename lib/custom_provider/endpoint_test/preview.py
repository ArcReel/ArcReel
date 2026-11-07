"""Request previews: render would-be requests from definitions/parameters, transmitting no bytes.

Use runtime :func:`render_request`, matching actual sent shapes with three template-semantics-preserving substitutions:
masked credential context (api_key placeholders allowed only in auth, so masking naturally targets credential injection,
never unrelated matching host/prompt/body substrings), asset size summaries,
and unchanged ``task_id`` / ``result_id`` polling placeholders that do not exist before submission.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from arcreel_market_core.endpoint_definition import (
    RenderedRequest,
    TemplateRenderError,
    build_context,
    definition_media_type,
    render_request,
)
from lib.backends.video_frame_slots import resolve_first_frame_aspect_ratio
from lib.custom_provider.builtin_definitions import declarative_video_capabilities
from lib.custom_provider.declarative_backend import (
    OVERRIDE_BASE_URL,
    apply_path_overrides,
    normalize_declarative_base_url,
)
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides, raw_base_url

from .errors import EndpointTestDefinitionError
from .inputs import ASSET_SOURCES, EndpointTestAssets, EndpointTestCredentials, EndpointTestParameters

#: Keep placeholders unchanged without credentials: previews compare provider-doc fields, so missing credentials must not collapse request shapes.
UNRESOLVED_API_KEY = "{{ api_key }}"
UNRESOLVED_BASE_URL = "{{ base_url }}"

#: Postsubmission polling/secondary-retrieval values always remain preview placeholders.
UNRESOLVED_TASK_ID = "{{ task_id }}"
UNRESOLVED_RESULT_ID = "{{ result_id }}"

_MASK_TAIL = 4
_MASK = "****"
#: auth.query percent-encodes masking into ``%2A%2A%2A%2A``; restore conventional preview URL forms.
_ENCODED_MASK = quote(_MASK, safe="")


@dataclass(frozen=True)
class PreviewedRequest:
    """One rendered/redacted request section."""

    method: str
    url: str
    headers: dict[str, str]
    body: object | None


@dataclass(frozen=True)
class RequestPreview:
    """Complete previews: submission/polling plus secondary retrieval if declared.

    ``conversions`` explains derivations with kind-dependent shapes passed unchanged: declarative
    templates directly substitute without explainable conversions, so ``None``; ComfyUI computes dimensions/frames/seeds/workflow
    changes, with keys in :class:`~.comfyui.ComfyuiConversions`.
    """

    submit: PreviewedRequest
    poll: PreviewedRequest
    result: PreviewedRequest | None
    conversions: Mapping[str, Any] | None = None


def preview_request(
    definition: Mapping[str, Any],
    parameters: EndpointTestParameters,
    *,
    credentials: EndpointTestCredentials | None = None,
    assets: EndpointTestAssets | None = None,
    placeholder_missing_assets: bool = True,
    path_overrides: PathOverrides = NO_PATH_OVERRIDES,
) -> RequestPreview:
    """Render submit/poll/result sections from shared-validator-approved definitions.

    ``path_overrides`` are model-row overrides (``docs/adr/0101``), relative to unchanged base_url like execution.

    ``placeholder_missing_assets`` is True for standalone previews only, generating declared placeholder summaries for missing uploads,
    so users compare fields with docs. Connection-test responses pass False: record actual sent shapes, where runtime
    removes entire fields for absent assets.
    """
    api_key = masked_api_key(credentials.api_key) if credentials else UNRESOLVED_API_KEY
    base_url = _preview_base_url(definition, credentials)
    definition = apply_path_overrides(definition, path_overrides)
    inputs = asset_summaries(definition.get("inputs") or {}, assets, placeholder_missing=placeholder_missing_assets)
    media_type = definition_media_type(definition)
    generation = (
        _video_variables(definition, parameters, inputs)
        if media_type == "video"
        else {
            "prompt": parameters.prompt,
            "aspect_ratio": parameters.aspect_ratio,
            "resolution": parameters.resolution,
            "seed": None,
        }
    )
    context = build_context(
        {
            "api_key": api_key,
            "base_url": base_url,
            OVERRIDE_BASE_URL: raw_base_url(credentials.base_url) if credentials else base_url,
            "model": parameters.model,
            **generation,
            "task_id": UNRESOLVED_TASK_ID,
            "result_id": UNRESOLVED_RESULT_ID,
        },
        inputs,
        definition.get("defaults"),
        media_type=media_type,
    )
    return RequestPreview(
        submit=_preview_section(definition, "submit", context),
        poll=_preview_section(definition, "poll", context),
        result=_preview_section(definition, "result", context) if "result" in definition else None,
    )


def _video_variables(
    definition: Mapping[str, Any], parameters: EndpointTestParameters, inputs: Mapping[str, object]
) -> dict[str, object]:
    """Generation-parameter variables for video definitions."""
    # Preview shapes must match actual sends: first_frame_ratio_adaptive_only endpoints accept only adaptive with first frames;
    # apply the same override according to first-frame presence actually rendered.
    aspect_ratio = resolve_first_frame_aspect_ratio(
        caps=declarative_video_capabilities(definition),
        aspect_ratio=parameters.aspect_ratio,
        has_first_frame=any(
            inputs.get(name) is not None
            for name, declaration in (definition.get("inputs") or {}).items()
            if declaration.get("source") == "start_image"
        ),
    )
    return {
        "prompt": parameters.prompt,
        "duration": parameters.duration_seconds,
        "duration_seconds": parameters.duration_seconds,
        "aspect_ratio": aspect_ratio,
        "resolution": parameters.resolution,
        "generate_audio": parameters.generate_audio,
        "seed": None,
    }


def asset_summaries(
    declarations: Mapping[str, Mapping[str, str]],
    assets: EndpointTestAssets | None,
    *,
    placeholder_missing: bool = True,
) -> dict[str, object]:
    """Replace assets with summaries such as ``<data:image/png;base64, 1234 bytes>``.

    True ``placeholder_missing`` in standalone previews creates declared missing-upload summaries instead of empty values:
    empty values let ``$when`` guards/whole placeholders delete entire fields, hiding request sections despite actual
    users supplying assets. False in connection responses preserves absent ``None``, matching runtime
    ``encode_inputs`` and actual field deletion.
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


def masked_api_key(api_key: str) -> str:
    """Masked credentials: ``****`` plus final 4 characters; empty strings unchanged, with nothing to mask."""
    if not api_key:
        return api_key
    return f"{_MASK}{api_key[-_MASK_TAIL:]}" if len(api_key) > _MASK_TAIL else _MASK


def restore_mask_in_url(url: str) -> str:
    """Restore URL-percent-encoded masking markers to ``****``.

    Replace encoded forms only, never credential substrings: real addresses never contain encoded ``****``.
    """
    return url.replace(_ENCODED_MASK, _MASK)


def _preview_base_url(definition: Mapping[str, Any], credentials: EndpointTestCredentials | None) -> str:
    # Share runtime normalization: strip configured trailing versions for explicit-version definitions, keeping preview/execution URLs identical.
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
        # Restore percent-encoded masking only to ****, never replace actual credential substrings.
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
