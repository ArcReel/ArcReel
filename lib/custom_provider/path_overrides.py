"""Path overrides (``docs/adr/0101``): endpoint request declarations, path validation/effective values.

Each endpoint declares overridable requests in
:attr:`EndpointSpec.requests <lib.custom_provider.endpoints.EndpointSpec.requests>`; model ``path_overrides``
replace whole paths by request name, relative to unchanged configured base_url, bypassing endpoint normalization.
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit

import httpx

#: Request names → whole replacement paths.
PathOverrides = Mapping[str, str]
NO_PATH_OVERRIDES: PathOverrides = MappingProxyType({})

#: How endpoints normalize base_url before default paths without overrides; UI reconstructs final addresses from this.
#: ``as_is`` strips trailing slashes; ``strip_version`` removes final versions such as ``/v1``; ``append_v1``
#: appends absent versions as ``/v1``; ``ensure_<后缀>`` appends absent suffixes (Ark ``/api/v3``, Vidu ``/ent/v2``,
#: Kling/MiniMax ``/v1``). ``dashscope_api_v1`` strips known Bailian suffixes then appends ``/api/v1``; ``strip_messages`` removes
#: copied ``/v1/messages`` endings (``normalize_anthropic_base_url``).
#: Overrides always use raw bases, never this normalization.
BaseUrlRule = Literal[
    "as_is",
    "strip_version",
    "append_v1",
    "ensure_api_v3",
    "ensure_ent_v2",
    "ensure_v1",
    "dashscope_api_v1",
    "strip_messages",
]

#: Path placeholders match declarative templates: ``{{ model }}`` / ``{{ task_id }}``.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")
_MAX_PATH_LENGTH = 512

#: Resume-submitted base_url from task ``submitted_base_url``, identifying requests to submitted bases.
_replay_base_url: ContextVar[str | None] = ContextVar("path_override_replay_base_url", default=None)

PathProblem = Literal["path_override_invalid_path", "path_override_placeholder_not_allowed"]


@dataclass(frozen=True)
class EndpointRequest:
    """One endpoint request with overridable paths."""

    #: Request names used by overrides, such as declarative ``submit`` / ``poll`` / ``result``.
    name: str
    #: Frontend dashboard i18n keys, shown above individual addresses when endpoints have multiple requests.
    display_name_key: str
    #: Default paths after normalized base_url, potentially containing placeholders.
    default_path: str
    #: Allowed override placeholders limited to values already belonging to requests, such as API names/task ids.
    placeholders: tuple[str, ...]
    base_url_rule: BaseUrlRule = "as_is"


def placeholder_names(path: str) -> set[str]:
    return set(_PLACEHOLDER.findall(path))


def path_problem(request: EndpointRequest, path: str) -> PathProblem | None:
    """Invalid-path reasons; None when valid.

    Paths start with ``/``, without whitespace/braces/``#`` outside placeholders (fragments are never sent); only declared placeholders are allowed.
    """
    literal = _PLACEHOLDER.sub("", path)
    if not path.startswith("/") or len(path) > _MAX_PATH_LENGTH or any(c.isspace() or c in "{}#" for c in literal):
        return "path_override_invalid_path"
    if not placeholder_names(path) <= set(request.placeholders):
        return "path_override_placeholder_not_allowed"
    return None


def effective_path_overrides(stored: object, requests: Iterable[EndpointRequest]) -> dict[str, str]:
    """Still-effective stored overrides, ignoring request names removed from endpoint definitions."""
    if not isinstance(stored, Mapping):
        return {}
    names = {r.name for r in requests}
    return {k: v for k, v in stored.items() if k in names and isinstance(v, str)}


def raw_base_url(base_url: str) -> str:
    """Override-relative configured base_url, adding absent schemes/removing trailing slashes only."""
    stripped = base_url.strip().rstrip("/")
    return f"https://{stripped}" if stripped and "://" not in stripped else stripped


def endpoint_guard(endpoint: str, overrides: PathOverrides) -> str:
    """Resume protocol identities: endpoint keys plus canonical override JSON when present.

    Without overrides, exactly equal endpoint keys, preserving existing-checkpoint comparisons.
    """
    if not overrides:
        return endpoint
    return f"{endpoint} paths={json.dumps(dict(overrides), sort_keys=True, ensure_ascii=False, separators=(',', ':'))}"


@contextmanager
def replaying_base_url(base_url: str | None) -> Generator[None]:
    """Tell override layers to replay submission-time base_url during resume.

    Resume polling/retrieval uses submitted bases. If users changed only base paths, prefixes differ from current
    configuration; recognize submitted prefixes and compose overrides against submitted raw paths.
    """
    token = _replay_base_url.set(base_url)
    try:
        yield
    finally:
        _replay_base_url.reset(token)


def _path_pattern(prefix: str, default_path: str, placeholder: str) -> re.Pattern[str]:
    """Whole ``prefix + default_path`` matches, with placeholders matched through ``placeholder``."""
    parts = _PLACEHOLDER.split(default_path)  # Alternate literals/placeholder names.
    body = "".join(re.escape(part) if i % 2 == 0 else f"(?P<{part}>{placeholder})" for i, part in enumerate(parts))
    return re.compile(re.escape(prefix) + body)


class PathOverrideLayer:
    """Python endpoint overrides: identify request names by paths, replacing with raw base_url path + override path.

    Recognize whole normalized-base-path + default-path matches only, never response download URLs.
    Hosts remain unchanged, preserving resume-submission-host replay. Within :func:`replaying_base_url`, recognize submitted
    bases first.
    """

    def __init__(self, routes: list[tuple[str, str]], prefix: str, raw_path: str, model: str, placeholder: str) -> None:
        self._routes = routes
        self._prefix = prefix
        self._raw_path = raw_path
        self._model = model
        self._placeholder = placeholder

    def __call__(self, inner: httpx.AsyncBaseTransport | httpx.BaseTransport, /) -> _PathOverrideTransport:
        return _PathOverrideTransport(inner, self)

    def _replayed_raw_path(self, prefix: str) -> str:
        """Submitted raw paths restored from submitted normalized paths using current raw→normalized deltas.

        Persisted submission bases are normalized; changing path prefixes only preserves deltas and restores original raw paths.
        """
        shared = 0
        for normalized_char, raw_char in zip(self._prefix, self._raw_path, strict=False):
            if normalized_char != raw_char:
                break
            shared += 1
        normalized_suffix = self._prefix[shared:]
        raw_suffix = self._raw_path[shared:]
        if normalized_suffix:
            if not prefix.endswith(normalized_suffix):
                return prefix
            prefix = prefix[: -len(normalized_suffix)]
        return prefix + raw_suffix

    def _bases(self) -> Iterator[tuple[str, str]]:
        """Normalized-prefix/raw-path pairs to try: submitted bases first on resume, then current configuration."""
        replayed = _replay_base_url.get()
        if replayed:
            prefix = urlsplit(replayed).path.rstrip("/")
            if prefix != self._prefix:
                yield prefix, self._replayed_raw_path(prefix)
        yield self._prefix, self._raw_path

    def rewrite(self, request: httpx.Request) -> None:
        # Match undecoded paths: raw base_url prefixes would mismatch decoded %2F; captured placeholders replay unchanged too.
        raw_path = request.url.raw_path.decode("ascii").partition("?")[0]
        found = next(
            (
                (m, override, base_raw_path)
                for prefix, base_raw_path in self._bases()
                for default_path, override in self._routes
                if (m := _path_pattern(prefix, default_path, self._placeholder).fullmatch(raw_path))
            ),
            None,
        )
        if found is None:
            return
        match, override, base_raw_path = found
        values = {"model": self._model, **match.groupdict()}
        target = base_raw_path + _PLACEHOLDER.sub(lambda p: values.get(p.group(1), p.group(0)), override)
        # Override queries replace entire originals (docs/adr/0101).
        path, has_query, query = target.partition("?")
        request.url = (
            request.url.copy_with(path=path, query=query.encode()) if has_query else request.url.copy_with(path=path)
        )


class _PathOverrideTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | httpx.BaseTransport, layer: PathOverrideLayer) -> None:
        self._inner = inner
        self._layer = layer

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(self._inner, httpx.BaseTransport)
        self._layer.rewrite(request)
        return self._inner.handle_request(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(self._inner, httpx.AsyncBaseTransport)
        self._layer.rewrite(request)
        return await self._inner.handle_async_request(request)

    def close(self) -> None:
        assert isinstance(self._inner, httpx.BaseTransport)
        self._inner.close()

    async def aclose(self) -> None:
        assert isinstance(self._inner, httpx.AsyncBaseTransport)
        await self._inner.aclose()


def path_override_layer(
    requests: Iterable[EndpointRequest],
    overrides: PathOverrides,
    base_url: str | None,
    normalized_base_url: str | None,
    model: str,
    placeholder: str = "[^/]+",
) -> PathOverrideLayer | None:
    """Python channel-injected override layers; None without overrides, preserving normal construction.

    ``normalized_base_url`` is actual bases without overrides, against which default paths match. ``placeholder`` patterns
    default to single segments; families containing ``/``, such as Google operation names, supply their own path patterns.
    """
    routes = [(r.default_path, overrides[r.name]) for r in requests if r.name in overrides]
    if not routes:
        return None
    return PathOverrideLayer(
        routes,
        urlsplit(normalized_base_url or "").path.rstrip("/"),
        urlsplit(raw_base_url(base_url or "")).path,
        model,
        placeholder,
    )
