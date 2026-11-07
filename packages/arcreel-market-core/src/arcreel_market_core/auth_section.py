"""Template primitives and semantic checks for the ``auth`` section, shared by both ``kind`` values.

Declarative and ComfyUI definitions share the same ``auth`` schema and rendering semantics: ``headers`` and ``query``
are two maps with template values; credentials appear only as ``api_key`` placeholders. The sole difference between kinds is which variables besides ``api_key``
are recognized (declarative auth also accepts base variables such as ``base_url``, while ComfyUI accepts only ``api_key``).
Callers therefore provide variable scopes through ``variable_issues``; the other three checks (malformed placeholders, literal credentials, duplicate header names)
are kind-independent and shared here.

This module lives at package root rather than inside ``endpoint_definition``: the comfyui subpackage's import contract prohibits depending on the
declarative runtime, so it cannot access that package's implementations; like ``definition_diagnostics`` and
``definition_schema_errors``, this is a leaf dependency of both kinds.

Rendering lives here for the same reason: the ComfyUI client must render this shared ``auth`` section into actual headers and query parameters, while the declarative
template engine is inaccessible from that side. Credential syntax and its validation therefore share one definition of a valid placeholder.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from arcreel_market_core.definition_diagnostics import DefinitionErrorCode, DefinitionIssue, join_path

#: The only credential-bearing variable in ``auth``.
API_KEY_VARIABLE = "api_key"

#: Valid placeholder: ``{{ variable_name }}``, allowing dot-separated names (``inputs.first_frame``).
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")

#: Every ``{{`` in a template. Any not starting a :data:`PLACEHOLDER` is malformed: only bare variables are recognized;
#: filters, indices, expressions, and unclosed opening braces are not placeholders and are sent unchanged when rendered.
_PLACEHOLDER_OPEN = re.compile(r"\{\{")

#: Credential-like shape: a token string of 20+ characters containing digits (common to API keys / JWT / base64). Short static values such as version numbers and fixed
#: fields do not match; false positives produce only a warning that does not block saving.
_CREDENTIAL_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{20,}")

#: The two maps in the ``auth`` section.
_AUTH_GROUPS = ("headers", "query")


def placeholder_names(text: str) -> list[str]:
    """Variable names of all valid placeholders, in order of appearance."""
    return PLACEHOLDER.findall(text)


def malformed_placeholders(text: str) -> list[str]:
    """All ``{{`` fragments that are not valid placeholders, through the nearest ``}}`` (or string end if absent)."""
    valid_starts = {match.start() for match in PLACEHOLDER.finditer(text)}
    fragments: list[str] = []
    for match in _PLACEHOLDER_OPEN.finditer(text):
        if match.start() in valid_starts:
            continue
        closing = text.find("}}", match.start())
        fragments.append(text[match.start() : closing + 2] if closing != -1 else text[match.start() :])
    return fragments


def looks_like_literal_credential(template: str) -> bool:
    """Whether an auth value's literal remainder after removing placeholders resembles a directly embedded credential.

    Export credential stripping depends on credentials using the api_key placeholder; literal credentials leak unchanged through export and Copy as Mine
    and can only be flagged by shape detection.
    """
    literal = PLACEHOLDER.sub(" ", template)
    return any(any(ch.isdigit() for ch in token) for token in _CREDENTIAL_TOKEN.findall(literal))


def auth_templates(auth: Mapping[str, Any]) -> Iterator[tuple[str, str, str, str]]:
    """Each template value in ``auth``: ``(map, key, location_path, template)``.

    Provide both key and location path: headers may contain dots, so deriving the key from the final path component would truncate ``X-Trace.Id`` to
    ``Id``.
    """
    for group in _AUTH_GROUPS:
        values: Mapping[str, Any] = auth.get(group) or {}
        for name, template in values.items():
            yield group, str(name), join_path(join_path("auth", group), str(name)), str(template)


def declares_credentials(auth: Mapping[str, Any]) -> bool:
    """Whether this section actually sends credentials.

    Determine this by entries in the two maps, not the ``auth`` object itself: ``{"headers": {}}`` sends no headers
    and is equivalent to an absent section. :func:`check_auth_section` and :func:`render_auth` use this same criterion,
    as must the decision whether the definition needs api_key.
    """
    return any(True for _ in auth_templates(auth))


def duplicate_header_issues(path: str, headers: Mapping[str, Any]) -> Iterator[DefinitionIssue]:
    """Same header key with different casing in one map: HTTP names are case-insensitive, so both would be sent."""
    seen: dict[str, str] = {}
    for name in headers:
        first = seen.setdefault(name.lower(), name)
        if first != name:
            yield DefinitionIssue(
                join_path(path, name),
                DefinitionErrorCode.HEADER_NAME_DUPLICATE,
                {"header": name, "first": first},
            )


@dataclass(frozen=True)
class AuthIssues:
    """One ``auth`` check result, split into two severity lists; literal credentials warn without blocking saving."""

    errors: tuple[DefinitionIssue, ...] = ()
    warnings: tuple[DefinitionIssue, ...] = ()


def check_auth_section(
    auth: Mapping[str, Any],
    *,
    variable_issues: Callable[[str, str], Iterable[DefinitionIssue]],
) -> AuthIssues:
    """Run all semantic rules for the ``auth`` section.

    ``variable_issues(path, name)`` checks variable scope for the caller's kind, invoked only for variables other than ``api_key``.
    ``api_key`` is always valid in auth, and its presence determines the final rule requiring nonempty auth to reference
    credentials; leaving this rule to each kind would let their interpretations of credential presence drift apart.

    Nonempty means entries in either map, not the ``auth`` object itself: ``{"headers": {}}``
    sends no headers and is equivalent to an absent section.
    """
    errors: list[DefinitionIssue] = []
    warnings: list[DefinitionIssue] = []
    references_api_key = False
    for _group, _name, path, template in auth_templates(auth):
        errors.extend(
            DefinitionIssue(path, DefinitionErrorCode.MALFORMED_PLACEHOLDER, {"fragment": fragment})
            for fragment in malformed_placeholders(template)
        )
        for name in placeholder_names(template):
            if name == API_KEY_VARIABLE:
                references_api_key = True
                continue
            errors.extend(variable_issues(path, name))
        if looks_like_literal_credential(template):
            warnings.append(DefinitionIssue(path, DefinitionErrorCode.AUTH_LITERAL_CREDENTIAL))
    errors.extend(duplicate_header_issues(join_path("auth", "headers"), auth.get("headers") or {}))
    if declares_credentials(auth) and not references_api_key:
        errors.append(DefinitionIssue("auth", DefinitionErrorCode.AUTH_WITHOUT_API_KEY))
    return AuthIssues(tuple(errors), tuple(warnings))


def render_auth(auth: Mapping[str, Any], *, api_key: str) -> tuple[dict[str, str], dict[str, str]]:
    """Render the ``auth`` section into the actual ``(headers, query)`` to send.

    Skip the whole section when ``api_key`` is empty: native ComfyUI has no authentication and credentials are needed only behind a reverse proxy;
    sending an empty credential such as ``Authorization: Bearer `` would make the proxy return 401 for a request that should pass.

    Variables other than ``api_key`` are rejected at save time by validation (ComfyUI auth accepts only this variable), so this function
    does not evaluate general templates; if a placeholder slips through, retaining it makes the mistake clearer than replacing it with an empty string.
    """
    if not api_key:
        return {}, {}
    rendered: dict[str, dict[str, str]] = {"headers": {}, "query": {}}
    for group, name, _path, template in auth_templates(auth):
        rendered[group][name] = PLACEHOLDER.sub(
            lambda match: api_key if match.group(1) == API_KEY_VARIABLE else match.group(0), template
        )
    return rendered["headers"], rendered["query"]
