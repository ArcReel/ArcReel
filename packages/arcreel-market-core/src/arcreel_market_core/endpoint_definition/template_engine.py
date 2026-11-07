"""Request-template rendering for declarative invocation endpoints."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from arcreel_market_core.aspect_size import (
    IMAGE_TIER_SHORT_EDGE,
    VIDEO_TIER_SHORT_EDGE,
    aspect_size,
    resolution_to_short_edge,
)
from arcreel_market_core.definition_diagnostics import DefinitionErrorCode, message_key
from arcreel_market_core.validation_messages import ValidationMessage

from .media_type import DEFAULT_DECLARATIVE_MEDIA_TYPE

_PLACEHOLDER = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*}}")
_WHOLE_PLACEHOLDER = re.compile(r"^\s*{{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*}}\s*$")
_DROP = object()

#: Media type to resolution-tier short-edge pixel tables; image and video each have their own tier names and pixel values.
_TIER_SHORT_EDGE_BY_MEDIA_TYPE: Mapping[str, dict[str, int]] = {
    "image": IMAGE_TIER_SHORT_EDGE,
    "video": VIDEO_TIER_SHORT_EDGE,
}


class TemplateRenderError(ValueError):
    """The template cannot render safely in the given context."""

    def __init__(self, code: DefinitionErrorCode, **params: object) -> None:
        self.code = code
        self.message = ValidationMessage(message_key(code), params)
        super().__init__(code.value)


@dataclass(frozen=True)
class AssetData:
    """Asset contents already read and MIME-identified by the caller."""

    mime_type: str
    content: bytes


@dataclass(frozen=True)
class RenderedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: object | None = None
    #: Credentials rendered as query parameters from auth, already appended to ``url``; retained separately because redirects replace
    #: the entire query with ``Location``, requiring reattachment on same-origin redirects.
    auth_query: dict[str, str] = field(default_factory=dict)


def encode_inputs(
    declarations: Mapping[str, Mapping[str, str]],
    assets: Mapping[str, AssetData | Sequence[AssetData] | None],
) -> dict[str, str | list[str] | None]:
    """Encode assets as data URIs or bare base64 according to inputs declarations."""
    encoded: dict[str, str | list[str] | None] = {}
    for name, declaration in declarations.items():
        raw = assets.get(declaration["source"])
        if raw is None:
            encoded[name] = None
        elif isinstance(raw, AssetData):
            encoded[name] = _encode_asset(raw, declaration["encoding"])
        else:
            encoded[name] = [_encode_asset(asset, declaration["encoding"]) for asset in raw]
    return encoded


def build_context(
    parameters: Mapping[str, object],
    inputs: Mapping[str, object] | None = None,
    defaults: Mapping[str, object] | None = None,
    *,
    media_type: str = DEFAULT_DECLARATIVE_MEDIA_TYPE,
) -> dict[str, object]:
    """Complete reserved template variables; derive dimensions only from ratio and resolution, converting resolution tiers by ``media_type``.

    Apply ``defaults`` here before whole-value-placeholder field omission and dimension derivation: parameters omitted by callers use the definition's
    declared defaults, the request field remains present, and derived dimensions follow that value.
    """
    context = dict(parameters)
    for name, value in (defaults or {}).items():
        if context.get(name) is None:
            context[name] = value
    context["inputs"] = dict(inputs or {})
    aspect_ratio = context.get("aspect_ratio")
    if isinstance(aspect_ratio, str):
        resolution = context.get("resolution")
        width, height = aspect_size(
            aspect_ratio,
            resolution_to_short_edge(
                resolution if isinstance(resolution, str) else None,
                tier_map=_TIER_SHORT_EDGE_BY_MEDIA_TYPE[media_type],
            ),
            round_to=8,
        )
        context["width"] = width
        context["height"] = height
    return context


def render_request(
    request: Mapping[str, Any],
    context: Mapping[str, object],
    *,
    enum_maps: Mapping[str, Mapping[str, object]] | None = None,
    auth: Mapping[str, Mapping[str, str]] | None = None,
) -> RenderedRequest:
    """Render one submit/poll/result request section; URL placeholders are not escaped."""
    maps = enum_maps or {}
    url = _render_string(request["url"], context, maps)
    if not isinstance(url, str):
        raise TemplateRenderError(DefinitionErrorCode.TEMPLATE_URL_NOT_STRING)

    auth = auth or {}
    auth_header_names = {name.lower() for name in auth.get("headers", {})}
    headers = {
        name: _stringify(rendered)
        for name, value in request.get("headers", {}).items()
        if name.lower() not in auth_header_names and (rendered := _render_string(value, context, maps)) is not _DROP
    }
    for name, value in auth.get("headers", {}).items():
        rendered = _render_string(value, context, maps)
        if rendered is not _DROP:
            headers[name] = _stringify(rendered)

    auth_query = _render_auth_query(auth.get("query", {}), context, maps)
    url = _append_auth_query(url, auth_query)
    body = _render_node(request.get("body", _DROP), context, maps)
    return RenderedRequest(
        method=request["method"],
        url=url,
        headers=headers,
        body=None if body is _DROP else body,
        auth_query=auth_query,
    )


def _encode_asset(asset: AssetData, encoding: str) -> str:
    payload = base64.b64encode(asset.content).decode("ascii")
    if encoding == "base64":
        return payload
    if encoding == "data_uri":
        return f"data:{asset.mime_type};base64,{payload}"
    raise TemplateRenderError(DefinitionErrorCode.UNKNOWN_ASSET_ENCODING, encoding=encoding)


def _lookup(context: Mapping[str, object], name: str) -> object:
    current: object = context
    for part in name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise TemplateRenderError(DefinitionErrorCode.UNDECLARED_VARIABLE, name=name)
        current = current[part]
    return current


def enum_map_key(value: object) -> str:
    """Convert values to ``enum_maps`` lookup keys. Validators use this to check default membership consistently with rendering."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def _resolve(
    name: str,
    context: Mapping[str, object],
    enum_maps: Mapping[str, Mapping[str, object]],
) -> object:
    value = _lookup(context, name)
    if name not in enum_maps or value is None:
        return value
    mapping = enum_maps[name]
    key = enum_map_key(value)
    if key not in mapping:
        raise TemplateRenderError(DefinitionErrorCode.ENUM_MAP_VALUE_MISSING, name=name, value=key)
    return mapping[key]


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _render_string(
    template: str,
    context: Mapping[str, object],
    enum_maps: Mapping[str, Mapping[str, object]],
) -> object:
    whole = _WHOLE_PLACEHOLDER.fullmatch(template)
    if whole:
        value = _resolve(whole.group(1), context, enum_maps)
        return _DROP if value is None else value

    def replace(match: re.Match[str]) -> str:
        value = _resolve(match.group(1), context, enum_maps)
        if value is None:
            raise TemplateRenderError(DefinitionErrorCode.TEMPLATE_TEXT_VARIABLE_NULL, name=match.group(1))
        return _stringify(value)

    return _PLACEHOLDER.sub(replace, template)


def _render_node(
    node: object,
    context: Mapping[str, object],
    enum_maps: Mapping[str, Mapping[str, object]],
) -> object:
    if isinstance(node, str):
        return _render_string(node, context, enum_maps)
    if isinstance(node, list):
        rendered: list[object] = []
        expanded_each = False
        for item in node:
            if isinstance(item, dict) and "$each" in item:
                expanded_each = True
                guard = item.get("$when")
                if guard is not None and not _input_present(context, guard):
                    continue
                directive = item["$each"]
                values = _each_values(directive, context)
                for index, value in enumerate(values):
                    result = _render_node(
                        directive["item"], {**context, directive["as"]: value, "index": index}, enum_maps
                    )
                    if result is not _DROP:
                        rendered.append(result)
            else:
                result = _render_node(item, context, enum_maps)
                if result is not _DROP:
                    rendered.append(result)
        return _DROP if expanded_each and not rendered else rendered
    if not isinstance(node, dict):
        return node

    guard = node.get("$when")
    if guard is not None and not _input_present(context, guard):
        return _DROP

    rendered_object: dict[str, object] = {}
    for key, value in node.items():
        if key == "$when":
            continue
        if key == "$each":
            values = _each_values(value, context)
            if not values:
                return _DROP
            for index, item in enumerate(values):
                scope = {**context, value["as"]: item, "index": index}
                rendered_key = _render_string(value["key"], scope, enum_maps)
                rendered_value = _render_node(value["value"], scope, enum_maps)
                if rendered_key is not _DROP and rendered_value is not _DROP:
                    _put_unique(rendered_object, _stringify(rendered_key), rendered_value)
            continue
        result = _render_node(value, context, enum_maps)
        if result is not _DROP:
            _put_unique(rendered_object, key, result)
    return rendered_object


def _put_unique(target: dict[str, object], key: str, value: object) -> None:
    """Write a key; raise on collision with an existing key rather than overwrite.

    Static JSON keys are already unique; only object-form ``$each`` expansion can collide. Overwriting silently drops earlier assets while
    the request still sends normally, so reject before submission.
    """
    if key in target:
        raise TemplateRenderError(DefinitionErrorCode.EACH_KEY_DUPLICATE, field=key)
    target[key] = value


def _each_values(directive: Mapping[str, Any], context: Mapping[str, object]) -> Sequence[object]:
    values = _lookup(context, directive["in"])
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TemplateRenderError(DefinitionErrorCode.EACH_VALUE_NOT_LIST, name=directive["in"])
    return values


def _input_present(context: Mapping[str, object], name: str) -> bool:
    inputs = context.get("inputs")
    return isinstance(inputs, Mapping) and name in inputs and inputs[name] is not None and inputs[name] != []


def _render_auth_query(
    query_templates: Mapping[str, str],
    context: Mapping[str, object],
    enum_maps: Mapping[str, Mapping[str, object]],
) -> dict[str, str]:
    """Render auth.query key/value pairs; continue omitting items whose whole-string placeholders resolve to null."""
    rendered_pairs: dict[str, str] = {}
    for name, template in query_templates.items():
        rendered = _render_string(template, context, enum_maps)
        if rendered is not _DROP:
            rendered_pairs[name] = _stringify(rendered)
    return rendered_pairs


def _append_auth_query(url: str, auth_query: Mapping[str, str]) -> str:
    """Append auth.query to the original query; preserve existing URL queries unchanged and percent-encode only appended items."""
    if not auth_query:
        return url
    parts = urlsplit(url)
    existing = {name for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
    overlap = existing & auth_query.keys()
    if overlap:
        raise TemplateRenderError(DefinitionErrorCode.AUTH_QUERY_CONFLICT, param=sorted(overlap)[0])
    appended = [f"{quote(name, safe='')}={quote(value, safe='')}" for name, value in auth_query.items()]
    query = "&".join([parts.query, *appended]) if parts.query else "&".join(appended)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
