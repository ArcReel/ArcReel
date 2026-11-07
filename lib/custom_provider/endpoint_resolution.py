"""Endpoint-key spec resolution: built-ins use lookups, ``ce-`` loads DB definitions on demand.

``get_endpoint_spec`` remains sync pure lookup over import-time constants. Callers needing both namespaces
use :func:`resolve_endpoint_spec`. Prefixes alone determine routing, never custom-then-built-in
fallback: disjoint namespaces share domains, with built-ins forbidden from ``ce-`` prefixes
(``lib.custom_provider.endpoints``).

Definitions alone are authoritative; specs/mirror columns project through definition ``kind`` implementations.
Definitions determine media, never endpoint keys. Projection implementations live outside this module:
both kinds in ``endpoints`` with ``EndpointSpec``/invariants; declarative projections also serve bundled definitions,
because user/bundled definitions are identical concepts. Here only load rows → select projections by kind.
Async loading and pure construction stay separate
(``docs/adr/0039``). Never preload entire definitions into in-memory registries: in-place updates must immediately affect new tasks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from arcreel_market_core.endpoint_definition import COMFYUI_KIND, DECLARATIVE_KIND, definition_media_type
from lib.custom_provider import is_custom_endpoint, make_endpoint_key, parse_endpoint_key
from lib.custom_provider.endpoints import (
    EndpointSpec,
    comfyui_endpoint_spec,
    declarative_endpoint_spec,
    get_endpoint_spec,
)

if TYPE_CHECKING:
    from lib.db.models.custom_endpoint import CustomEndpoint


@dataclass(frozen=True)
class MirrorColumns:
    """Readonly write-derived mirror columns for DB filtering/display without JSON parsing."""

    kind: str
    schema_version: str
    media_type: str
    display_name: str


#: ``kind`` → definition-to-spec projection implementations.
_SPEC_BY_KIND: Mapping[str, Callable[[str, Mapping[str, Any]], EndpointSpec]] = {
    DECLARATIVE_KIND: lambda key, definition: declarative_endpoint_spec(key, definition, source="custom"),
    COMFYUI_KIND: comfyui_endpoint_spec,
}


def _dispatch[T](table: Mapping[str, T], definition: Mapping[str, Any]) -> T:
    kind = str(definition["kind"])
    implementation = table.get(kind)
    if implementation is None:
        raise ValueError(f"unsupported endpoint definition kind: {kind!r}")
    return implementation


def derive_mirror_columns(definition: Mapping[str, Any]) -> MirrorColumns:
    """Derive mirrors from **validated** definitions.

    Validation guarantees string ``kind`` / ``schema_version`` / ``meta.name``, so read directly;
    deriving unvalidated definitions would persist incomplete structures.
    """
    meta: Mapping[str, Any] = definition["meta"]
    return MirrorColumns(
        kind=str(definition["kind"]),
        schema_version=str(definition["schema_version"]),
        media_type=definition_media_type(definition),
        display_name=str(meta["name"]),
    )


def endpoint_spec_from_row(row: CustomEndpoint) -> EndpointSpec:
    """Construct specs from DB rows; derive keys from autoincrement ids, mark source ``custom``.

    Raises:
        ValueError: Definition ``kind`` lacks projection implementations here.
    """
    definition: Mapping[str, Any] = row.definition
    return _dispatch(_SPEC_BY_KIND, definition)(make_endpoint_key(row.id), definition)


async def resolve_endpoint_spec(
    endpoint: str,
    get_custom_endpoint: Callable[[int], Awaitable[CustomEndpoint | None]],
) -> EndpointSpec:
    """Resolve specs: ``ce-`` reads ``custom_endpoint``, all others delegate built-in lookups.

    Raises:
        ValueError: Missing keys, absent built-ins or deleted custom endpoints.
    """
    if not is_custom_endpoint(endpoint):
        return get_endpoint_spec(endpoint)

    try:
        endpoint_id = parse_endpoint_key(endpoint)
    except ValueError:
        raise ValueError(f"unknown endpoint: {endpoint!r}") from None
    row = await get_custom_endpoint(endpoint_id)
    if row is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return endpoint_spec_from_row(row)
