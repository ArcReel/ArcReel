"""Sharing submissions: endpoint file packaging, local preflight, and restoration of official-service diagnostics into translatable messages.

Local preflight shares official-service/market-CI rules: place files under temporary market-source
``endpoints/<slug>/`` and generate an index; generation contains all validation rules
(``docs/adr/0078``). Report errors only. Extract definition-validator ``val_ce_*`` diagnostic keys,
sharing endpoint diagnostic-card copy. Additionally block suspected literal-credential warnings
to prevent publishing secrets with definitions. Publish request content unchanged (entire ComfyUI
workflow; declarative submit/poll/result sections except response extract). Block credential-like
keys or URL query parameter names at any depth if literal values remain after removing placeholders;
separately judge header values by literal-credential shape.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from arcreel_market_core.auth_section import PLACEHOLDER, looks_like_literal_credential
from arcreel_market_core.definition_diagnostics import DefinitionErrorCode, join_path
from arcreel_market_core.endpoint_definition import validate_definition
from arcreel_market_core.market import ENDPOINT_ENTRY_TYPE, GenerateError, MarketIssueCode, build_index
from arcreel_market_core.market.generate import DEFINITION_FILENAME, ENDPOINTS_DIR, ICON_STEM
from arcreel_market_core.market.icon import ICON_FORMATS
from arcreel_market_core.market.issues import message_key
from arcreel_market_core.validation_messages import MessageJoin, MessagePart, MessageRef, Translator, ValidationMessage

#: Initial scope accepts call endpoints only.
SUBMISSION_TYPE = ENDPOINT_ENTRY_TYPE
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: Split keys at delimiters/case boundaries and remove plural s; these fragments mark credentials. Only keys count, leaving long literal values such as model filenames unaffected;
#: keys such as ``tokenizer`` do not match. ``Proxy-Authorization`` / ``Set-Cookie`` match fragments.
CREDENTIAL_NAME_PARTS = frozenset(
    {"apikey", "token", "secret", "password", "passwd", "credential", "authorization", "cookie"}
)
#: Header-mapping keys: additionally judge values by literal-credential shapes, covering custom header names.
_HEADERS = "headers"
LITERAL_CREDENTIAL_KEY = "market_submission_literal_credential"
#: Definition sections whose request content publishes unchanged; definition validators check literal credentials in ``auth``.
_REQUEST_SECTIONS = {"comfyui": ("workflow",), "declarative": ("submit", "poll", "result")}
#: Response extracts inside declarative request sections: extraction-name keys with JSONPath values, outside requests and unscanned.
_RESPONSE_EXTRACT = "extract"
#: Allowed request icon filenames, matching market-repository directory conventions.
ICON_FILENAMES = frozenset(f"{ICON_STEM}.{extension}" for extension in ICON_FORMATS)


@dataclass(frozen=True)
class SubmissionDiagnostic:
    """One submission diagnostic: ``file`` relative to entry directory (empty for invalid slugs), ``path`` locates within the file."""

    file: str
    path: str
    message: ValidationMessage

    def to_payload(self, translate: Translator) -> dict[str, str]:
        return {
            "file": self.file,
            "path": self.path,
            "code": self.message.key,
            "message": self.message.render(translate),
        }


def submission_files(definition: Mapping[str, Any], icon: tuple[str, bytes] | None) -> dict[str, bytes]:
    """Entry-directory files: unchanged serialized definition plus optional icon."""
    files = {DEFINITION_FILENAME: (json.dumps(definition, ensure_ascii=False, indent=2) + "\n").encode()}
    if icon is not None:
        files[icon[0]] = icon[1]
    return files


def check_submission(slug: str, files: Mapping[str, bytes]) -> list[SubmissionDiagnostic]:
    if not SLUG_PATTERN.fullmatch(slug):
        return [
            SubmissionDiagnostic("", "$", ValidationMessage(message_key(MarketIssueCode.SLUG_INVALID), {"value": slug}))
        ]
    directory = f"{ENDPOINTS_DIR}/{slug}/"
    with tempfile.TemporaryDirectory(prefix="arcreel-submission-") as tmp:
        root = Path(tmp)
        entry = root / directory
        entry.mkdir(parents=True)
        for name, content in files.items():
            (entry / name).write_bytes(content)
        try:
            build_index(root, default_name="arcreel-market")
        except GenerateError as exc:
            issues = exc.issues
        else:
            definition = json.loads(files[DEFINITION_FILENAME])
            return [
                SubmissionDiagnostic(DEFINITION_FILENAME, issue.path, issue.message)
                for issue in validate_definition(definition).warnings
                if issue.code is DefinitionErrorCode.AUTH_LITERAL_CREDENTIAL
            ] + [
                SubmissionDiagnostic(DEFINITION_FILENAME, path, ValidationMessage(LITERAL_CREDENTIAL_KEY, {}))
                for path in _request_credentials(definition)
            ]
    diagnostics: list[SubmissionDiagnostic] = []
    for issue in issues:
        detail = issue.params.get("detail")
        message = (
            detail
            if issue.code is MarketIssueCode.DEFINITION_INVALID and isinstance(detail, ValidationMessage)
            else issue.message
        )
        diagnostics.append(SubmissionDiagnostic(issue.file.removeprefix(directory), issue.path, message))
    return diagnostics


def _request_credentials(definition: Mapping[str, Any]) -> Iterator[str]:
    """Request locations with credential-like keys/query parameter names and remaining literal values after placeholder removal."""
    for section in _REQUEST_SECTIONS.get(str(definition.get("kind")), ()):
        content = definition.get(section)
        if isinstance(content, Mapping):
            content = {key: value for key, value in content.items() if key != _RESPONSE_EXTRACT}
        yield from _literal_credentials(section, content, named_credential=False, in_headers=False)


def _literal_credentials(path: str, value: Any, *, named_credential: bool, in_headers: bool) -> Iterator[str]:
    """Recursively scan dicts/lists: string values beneath credential-like keys (including nested levels) and credential-like URL query names
    match when literal values remain; strings under header mappings also match literal-credential shapes."""
    if isinstance(value, str):
        if (
            (named_credential and _has_literal(value))
            or (in_headers and looks_like_literal_credential(value))
            or any(_names_credential(name) and _has_literal(param) for name, param in _query_params(value))
        ):
            yield path
    elif isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            yield from _literal_credentials(
                join_path(path, name),
                child,
                named_credential=named_credential or _names_credential(name),
                in_headers=in_headers or name == _HEADERS,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _literal_credentials(
                join_path(path, index), child, named_credential=named_credential, in_headers=in_headers
            )


def _has_literal(template: str) -> bool:
    return bool(PLACEHOLDER.sub("", template).strip())


def _query_params(text: str) -> list[tuple[str, str]]:
    if "?" not in text:
        return []
    try:
        return parse_qsl(urlsplit(text).query, keep_blank_values=True)
    except ValueError:
        return []


def _names_credential(name: str) -> bool:
    words = (
        re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower().replace("api_key", "apikey").replace("api-key", "apikey")
    )
    return any(word.removesuffix("s") in CREDENTIAL_NAME_PARTS for word in re.split(r"[^a-z0-9]+", words))


def remote_diagnostics(params: Mapping[str, Any]) -> list[SubmissionDiagnostic]:
    """Official-service ``submission_invalid`` ``params.diagnostics``; skip malformed entries."""
    raw = params.get("diagnostics")
    if not isinstance(raw, list):
        return []
    diagnostics: list[SubmissionDiagnostic] = []
    for item in raw:
        message = _message(item)
        if message is None or not isinstance(item.get("file"), str) or not isinstance(item.get("path"), str):
            continue
        diagnostics.append(SubmissionDiagnostic(item["file"], item["path"], message))
    return diagnostics


def _message(value: Any) -> ValidationMessage | None:
    if not isinstance(value, Mapping) or not isinstance(value.get("code"), str):
        return None
    params = value.get("params")
    if not isinstance(params, Mapping):
        return None
    return ValidationMessage(value["code"], {name: _param(item) for name, item in params.items()})


def _param(value: Any) -> Any:
    """Nested ``{code, params}`` parameters are messages: translate before outer interpolation; restore lists as fragment sequences."""
    if isinstance(value, list):
        return MessageJoin(tuple(_part(item) for item in value))
    nested = _message(value)
    return nested if nested is not None else value


def _part(value: Any) -> MessagePart:
    if isinstance(value, list):
        return MessageJoin(tuple(_part(item) for item in value))
    nested = _message(value)
    if nested is not None and not nested.params:
        return MessageRef(nested.key)
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
