"""Bundled declarative built-ins: validate ``builtin_endpoints/*.json`` definitions and derive endpoint metadata.

Built-ins use Python backends or bundled definitions. JSON lives under
``builtin_endpoints/``, **filenames are built-in keys**, loaded into registries at import by :mod:`lib.custom_provider.endpoints`.
Never persist definitions in DB; users cannot edit/delete them, upgrades replace effective files.

Fail fast on invalid JSON/shared-validation failures, ``ce-`` key prefixes,
or authors other than ArcReel: process startup fails instead of delayed generation failures. Always use
:func:`arcreel_market_core.endpoint_definition.validate_definition`, identical for bundled/user definitions.

Only files → definitions/metadata here, without constructing :class:`~lib.custom_provider.endpoints.EndpointSpec`,
so no ``endpoints`` dependency, which instead depends here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from arcreel_market_core.endpoint_definition import DefinitionIssue, validate_definition
from arcreel_market_core.video_backend_contract import VideoCapabilities
from lib.custom_provider import CUSTOM_ENDPOINT_KEY_PREFIX

#: Bundled directory; filenames without ``.json`` are built-in keys.
BUILTIN_DEFINITIONS_DIR = Path(__file__).parent / "builtin_endpoints"

#: Bundled author attribution, retained by copy-as-mine; users may change copy names/versions freely.
BUILTIN_DEFINITION_AUTHOR = "ArcReel"


class BuiltinDefinitionError(RuntimeError):
    """Invalid bundled definitions, raised at import and preventing startup."""


def load_builtin_definitions(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read/validate every ``<key>.json``, returning built-in keys → definitions.

    Sort by filenames so registry order never drifts with filesystem traversal.
    """
    base = BUILTIN_DEFINITIONS_DIR if directory is None else directory
    if not base.is_dir():
        raise BuiltinDefinitionError(f"随版定义目录不存在: {base}")
    return {path.stem: load_builtin_definition(path) for path in sorted(base.glob("*.json"))}


def load_builtin_definition(path: Path) -> dict[str, Any]:
    """Read/validate one bundled definition; invalid raises :class:`BuiltinDefinitionError`."""
    key = path.stem
    if key.startswith(CUSTOM_ENDPOINT_KEY_PREFIX):
        raise BuiltinDefinitionError(
            f"随版定义 {path.name} 的内置键占用了自定义端点前缀 {CUSTOM_ENDPOINT_KEY_PREFIX!r}"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuiltinDefinitionError(f"随版定义 {path.name} 不是合法 JSON: {exc}") from exc
    diagnostics = validate_definition(document)
    if not diagnostics.valid:
        raise BuiltinDefinitionError(f"随版定义 {path.name} 未通过校验: {_format_issues(diagnostics.errors)}")
    author = document["meta"]["author"]
    if author != BUILTIN_DEFINITION_AUTHOR:
        raise BuiltinDefinitionError(
            f"随版定义 {path.name} 的 meta.author 必须是 {BUILTIN_DEFINITION_AUTHOR!r}，实际 {author!r}"
        )
    return document


def _format_issues(issues: Sequence[DefinitionIssue]) -> str:
    """One readable import-time diagnostic line; without request locales, render codes/paths only."""
    return "; ".join(f"{issue.path}: {issue.code.value}" for issue in issues)


def declarative_family(key: str) -> str:
    """Built-in key first segments define families (``newapi-video`` → ``newapi``)."""
    return key.split("-", 1)[0]


def declarative_display_name(definition: Mapping[str, Any]) -> str:
    """Display ``meta.name``: endpoint names are vendor proper names, untranslated without external mappings."""
    name: str = definition["meta"]["name"]
    return name


def declarative_request_path(definition: Mapping[str, Any]) -> str:
    """Submission URL templates without ``{{ base_url }}`` prefixes, for catalog paths.

    Definition URLs are full templates, but catalog ``request_path_template`` shows paths only, matching Python built-ins.
    Hardcoded vendor hosts without base_url prefixes likewise lose scheme/authority.
    """
    url: str = definition["submit"]["url"]
    stripped = url.strip()
    for prefix in ("{{base_url}}", "{{ base_url }}"):
        if stripped.startswith(prefix):
            return stripped[len(prefix) :]
    parsed = urlsplit(stripped)
    if parsed.scheme and parsed.netloc:
        return parsed.path + (f"?{parsed.query}" if parsed.query else "") or "/"
    return stripped


def declarative_video_capabilities(definition: Mapping[str, Any]) -> VideoCapabilities:
    """Public compatibility name for declarative projections, implemented centrally in capability composition."""
    from lib.custom_provider.capabilities import video_capabilities_from_definition

    return video_capabilities_from_definition(definition)
