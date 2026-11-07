"""Shared endpoint-definition validator: the sole validation implementation for saving, the validate interface, endpoint tests, and import time.

The :func:`validate_definition` entry point first checks the container (object, present ``kind``, validator for ``kind``),
then dispatches by ``kind``. Unknown kinds are rejected structurally at the container layer instead of entering one
kind's rules and producing secondary errors unrelated to the actual problem.

Declarative kinds must pass both gates: ``schema.json`` governs structure (field sets, types, enums); this module
handles semantics: placeholders reference only declared variables, credentials enter only through ``auth``, capability
claims and actually referenced assets agree in both directions, and every extraction path fits the restricted JSONPath
subset. Both layers produce :class:`DefinitionIssue`, so consumers always receive the same code vocabulary.

Pure logic: no database access, requests, or environment reads; input is an already-parsed JSON value.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, fields
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from jsonschema import Draft202012Validator

from arcreel_market_core.auth_section import (
    check_auth_section,
    duplicate_header_issues,
    malformed_placeholders,
    placeholder_names,
)
from arcreel_market_core.comfyui.validator import (
    CURRENT_SCHEMA_VERSION as COMFYUI_SCHEMA_VERSION,
)
from arcreel_market_core.comfyui.validator import (
    validate_comfyui_definition,
)
from arcreel_market_core.definition_diagnostics import (
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    join_path,
)
from arcreel_market_core.definition_schema_errors import most_specific, translate_schema_error
from arcreel_market_core.video_backend_contract import (
    ProviderJobStatus,
    ReferenceAudioMode,
    VideoCapabilities,
    audio_capability_pair_is_coherent,
)

from .jsonpath_subset import JsonPathSubsetError, parse_json_path
from .kinds import COMFYUI_KIND, DECLARATIVE_KIND
from .media_type import definition_media_type
from .template_engine import enum_map_key

SCHEMA_PATH = Path(__file__).parent / "schema.json"

#: Definition-format version itself; writes do not rewrite file ``schema_version``, and validators do not migrate definitions.
CURRENT_SCHEMA_VERSION = "1.2.0"

#: Reserved variables always available to video request templates. ``width`` / ``height`` derive from ratio and resolution, not parameters.
BASE_VARIABLES = frozenset(
    {
        "base_url",
        "model",
        "prompt",
        "duration",
        "aspect_ratio",
        "resolution",
        "generate_audio",
        "seed",
        "width",
        "height",
    }
)

#: Variables allowed for enum mapping in video definitions: providers rename these tier parameters; renaming prompt and similar values is meaningless.
ENUM_MAP_VARIABLES = frozenset({"duration", "aspect_ratio", "resolution", "generate_audio"})

#: Variables permitting defaults in video definitions and their host types: tier parameters callers may omit. base_url /
#: model / prompt are supplied on every call; width / height derive from ratio and resolution. Declaring defaults for them only hides
#: actual missing parameters. Types are ArcReel-side parameter types: defaults enter context unchanged before rendering; wrong-type aspect_ratio /
#: resolution prevent dimension derivation from finding tiers (definitions referencing {{ width }} fail whenever parameters are omitted),
#: while wrong-type duration / generate_audio distort whole-value placeholders' native-type preservation.
DEFAULT_VALUE_TYPES: dict[str, type] = {
    "duration": int,
    "aspect_ratio": str,
    "resolution": str,
    "generate_audio": bool,
    "seed": int,
}

#: Video-definition capability fields, with the same names and meanings as ``VideoCapabilities``.
VIDEO_CAPABILITY_FIELDS = frozenset(field.name for field in fields(VideoCapabilities))


@dataclass(frozen=True)
class MediaTypeRules:
    """Fields available to definitions of one media type.

    ``schema.json`` accepts the union across media types; this table narrows by declared ``media_type``. Fields exclusive
    to another media type produce ``media_type_field_not_allowed`` rather than generic unknown-field or undeclared-variable
    errors, so authors can identify media type as the cause.
    """

    #: Reserved variables request templates may reference.
    variables: frozenset[str]
    #: Variables supporting enum mapping.
    enum_map_variables: frozenset[str]
    #: Variables allowing defaults and their host types.
    default_value_types: Mapping[str, type]
    #: Asset sources allowed for ``inputs.*.source``.
    input_sources: frozenset[str]
    #: Fields allowed in ``capabilities``.
    capabilities: frozenset[str]
    #: Artifact-extraction keys in runtime consumption order. The artifact section (``poll`` without ``result``, otherwise
    #: ``result``) must contain at least one in ``extract``.
    artifact_keys: tuple[str, ...]
    #: Whether ``extract`` may contain ``usage`` (billed duration).
    usage_extract: bool
    #: Capabilities with at least one required true declaration; empty means no requirement.
    required_capabilities: tuple[str, ...]


#: ``media_type`` to available fields for that media type.
MEDIA_TYPE_RULES: Mapping[str, MediaTypeRules] = {
    "video": MediaTypeRules(
        variables=BASE_VARIABLES,
        enum_map_variables=ENUM_MAP_VARIABLES,
        default_value_types=DEFAULT_VALUE_TYPES,
        input_sources=frozenset({"start_image", "end_image", "reference_images", "reference_audio_files"}),
        capabilities=VIDEO_CAPABILITY_FIELDS,
        artifact_keys=("video_url",),
        usage_extract=True,
        required_capabilities=(),
    ),
    "image": MediaTypeRules(
        variables=frozenset({"base_url", "model", "prompt", "aspect_ratio", "resolution", "seed", "width", "height"}),
        enum_map_variables=frozenset({"aspect_ratio", "resolution"}),
        default_value_types={"aspect_ratio": str, "resolution": str, "seed": int},
        input_sources=frozenset({"reference_images"}),
        capabilities=frozenset({"text_to_image", "image_to_image", "max_reference_images"}),
        artifact_keys=("image_url", "image_b64"),
        usage_extract=False,
        required_capabilities=("text_to_image", "image_to_image"),
    ),
}

#: Reserved variables available to any media type; only names outside this set are truly undeclared.
_ALL_VARIABLES = frozenset().union(*(rules.variables for rules in MEDIA_TYPE_RULES.values()))
_ALL_ENUM_MAP_VARIABLES = frozenset().union(*(rules.enum_map_variables for rules in MEDIA_TYPE_RULES.values()))
_ALL_DEFAULTABLE_VARIABLES = frozenset().union(*(rules.default_value_types for rules in MEDIA_TYPE_RULES.values()))
_ALL_ARTIFACT_KEYS = frozenset().union(*(rules.artifact_keys for rules in MEDIA_TYPE_RULES.values()))

#: List-valued asset sources: expand only through ``$each``; direct interpolation serializes the whole list into the request.
LIST_INPUT_SOURCES = frozenset({"reference_images", "reference_audio_files"})

#: Image-valued asset sources (all ``inputs.*.source`` enum members except reference audio). Required image inputs mean the
#: request shape requires images; derive ``capabilities.text_to_video`` from this set. Validation and capability composition in
#: :mod:`lib.custom_provider.capabilities` share it; do not keep independent copies, as set drift would let
#: save-time-accepted declarations produce opposite ``text_to_video`` during composition.
IMAGE_INPUT_SOURCES = frozenset({"start_image", "end_image", "reference_images"})


def requires_image_input(inputs: Mapping[str, Any] | None) -> bool:
    """Whether this definition's request shape requires images, the source for deriving ``capabilities.text_to_video``.

    Validator consistency checks, capability composition, and spec projection share this function: ``text_to_video``
    derives from request shape; its explicit definition declaration is only a redundant assertion (disagreement produces
    ``capability_incoherent`` at save time). Consumers reading directly from ``capabilities`` instead would reach a
    conclusion opposite to request shape for valid definitions that omit this flag.
    """
    return any(
        isinstance(declaration, Mapping)
        and declaration.get("required") is True
        and declaration.get("source") in IMAGE_INPUT_SOURCES
        for declaration in (inputs or {}).values()
    )


#: States declarative definitions may produce. They do not produce ``expired``; expiration maps to ``failed``.
CANONICAL_STATUSES = frozenset(
    {
        ProviderJobStatus.QUEUED.value,
        ProviderJobStatus.RUNNING.value,
        ProviderJobStatus.SUCCEEDED.value,
        ProviderJobStatus.FAILED.value,
    }
)

#: Capability/source pairs: run bidirectional consistency per pair, blocking false claims and omissions.
CAPABILITY_SOURCE_PAIRS: tuple[tuple[str, str], ...] = (
    ("first_frame", "start_image"),
    ("last_frame", "end_image"),
    ("max_reference_images", "reference_images"),
    ("reference_audio_mode", "reference_audio_files"),
)

#: Unsupported but easily written field names mapped to their destinations: a dedicated removed-field diagnostic is more helpful than generic unknown-field errors.
REMOVED_FIELD_REASONS: Mapping[str, str] = {
    "query": "val_ce_removed_reason_request_query",
    "success_status_codes": "val_ce_removed_reason_status_codes",
    "retry_status_codes": "val_ce_removed_reason_status_codes",
    "expired_status_codes": "val_ce_removed_reason_status_codes",
    "interval_seconds": "val_ce_removed_reason_polling_policy",
    "timeout_seconds": "val_ce_removed_reason_polling_policy",
    "source": "val_ce_removed_reason_extract_source",
    "duration_seconds": "val_ce_removed_reason_extract_usage_keys",
    "mime_types": "val_ce_removed_reason_mime_types",
}


@cache
def load_schema() -> dict[str, Any]:
    """Read and cache ``schema.json``. Public so documentation, agent skills, and frontend share one contract."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def _schema_validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_definition(document: object) -> DefinitionDiagnostics:
    """Validate definition JSON: check the container, then dispatch by ``kind`` to its validator.

    Container checks recognize only ``kind`` and ignore all other fields: definitions of different kinds have different
    shapes, so applying one kind's schema to another produces secondary errors unrelated to the actual problem.
    """
    container = tuple(_container_issues(document))
    if container or not isinstance(document, dict):
        return DefinitionDiagnostics(errors=container)
    return _KIND_VALIDATORS[str(document["kind"])](document)


def _validate_declarative(document: Mapping[str, Any]) -> DefinitionDiagnostics:
    """The two declarative-definition gates.

    Do not run semantics after structural errors: placeholder and capability checks assume valid field shapes.
    Continuing on incomplete structures would produce misleading secondary errors.
    """
    structural = tuple(_structural_issues(document))
    if structural:
        return DefinitionDiagnostics(errors=structural)
    checker = _SemanticChecker(document)
    checker.run()
    return DefinitionDiagnostics(errors=tuple(checker.errors), warnings=tuple(checker.warnings))


#: ``kind`` to its validator. Keys are all kinds recognized by validation; derive the container enum from them to avoid drift.
_KIND_VALIDATORS: Mapping[str, Callable[[Mapping[str, Any]], DefinitionDiagnostics]] = {
    DECLARATIVE_KIND: _validate_declarative,
    COMFYUI_KIND: validate_comfyui_definition,
}

#: ``kind`` to its current definition-format version. The two kinds have independent version lines; comparing across them would report false version gaps on entirely
#: valid definitions.
_CURRENT_SCHEMA_VERSION_BY_KIND: Mapping[str, str] = {
    DECLARATIVE_KIND: CURRENT_SCHEMA_VERSION,
    COMFYUI_KIND: COMFYUI_SCHEMA_VERSION,
}


def current_schema_version(document: object) -> str:
    """Current version of the definition's version line.

    Fall back to the declarative line when ``kind`` is unknown: version categories merely inform import confirmation;
    the validator is the actual gate and already rejects unknown kinds. Inventing a version here has no value.
    """
    kind = document.get("kind") if isinstance(document, Mapping) else None
    return _CURRENT_SCHEMA_VERSION_BY_KIND.get(str(kind), CURRENT_SCHEMA_VERSION)


# ---------------------------------------------------------------- Container layer


@cache
def _container_validator() -> Draft202012Validator:
    schema = {
        "type": "object",
        "required": ["kind"],
        "properties": {"kind": {"enum": sorted(_KIND_VALIDATORS)}},
    }
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _container_issues(document: Any) -> Iterator[DefinitionIssue]:
    for error in _container_validator().iter_errors(document):
        yield from translate_schema_error(error, removed_fields={})


# ---------------------------------------------------------------- Structure layer


def _structural_issues(document: Any) -> Iterator[DefinitionIssue]:
    for error in _schema_validator().iter_errors(document):
        yield from translate_schema_error(most_specific(error), removed_fields=REMOVED_FIELD_REASONS)


# ---------------------------------------------------------------- Semantic layer


class _Scope:
    """Placeholder scope: section determines reserved-variable availability; locals are loop variables introduced by ``$each``."""

    __slots__ = ("locals", "section")

    def __init__(self, *, section: str, locals_: frozenset[str] = frozenset()) -> None:
        self.section = section
        self.locals = locals_

    def with_locals(self, names: set[str]) -> _Scope:
        return _Scope(section=self.section, locals_=self.locals | frozenset(names))


class _SemanticChecker:
    """Run semantic rules on structurally valid definitions and record each violation as a diagnostic."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        self._document = document
        self._media_type = definition_media_type(document)
        self._rules = MEDIA_TYPE_RULES[self._media_type]
        self._inputs: Mapping[str, Any] = document.get("inputs") or {}
        self._auth: Mapping[str, Any] = document.get("auth") or {}
        self._list_inputs = {name for name, decl in self._inputs.items() if decl.get("source") in LIST_INPUT_SOURCES}
        self._referenced_inputs: set[str] = set()
        self.errors: list[DefinitionIssue] = []
        self.warnings: list[DefinitionIssue] = []

    def run(self) -> None:
        self._check_auth_section()
        for section in ("submit", "poll", "result"):
            if section in self._document:
                self._check_request(section)
        self._check_enum_maps()
        self._check_defaults()
        self._check_status_map()
        self._check_inputs_referenced()
        self._check_input_sources()
        self._check_artifact_extract()
        self._check_capabilities()

    # ---- Recording ----

    def _error(self, path: str, code: DefinitionErrorCode, **params: Any) -> None:
        self.errors.append(DefinitionIssue(path, code, params))

    def _media_type_mismatch(self, path: str, name: str) -> DefinitionIssue:
        return DefinitionIssue(
            path,
            DefinitionErrorCode.MEDIA_TYPE_FIELD_NOT_ALLOWED,
            {"name": name, "media_type": self._media_type},
        )

    def _warn(self, path: str, code: DefinitionErrorCode, **params: Any) -> None:
        self.warnings.append(DefinitionIssue(path, code, params))

    # ---- auth ----

    def _check_auth_section(self) -> None:
        """Share auth checks with ComfyUI definitions; only variable scope belongs to this kind."""
        issues = check_auth_section(self._auth, variable_issues=self._auth_variable_issues)
        self.errors.extend(issues.errors)
        self.warnings.extend(issues.warnings)

    def _auth_variable_issues(self, path: str, name: str) -> list[DefinitionIssue]:
        """Variables other than ``api_key`` in auth follow the same scope rules as other sections.

        Declarative auth accepts base variables such as ``base_url`` (credentials distributed by provider address),
        so do not reject everything except api_key; apply the full scope rules to the auth section.
        """
        issue = self._variable_issue(name, path, _Scope(section="auth"))
        return [] if issue is None else [issue]

    # ---- Request sections ----

    def _check_request(self, section: str) -> None:
        request: Mapping[str, Any] = self._document[section]
        scope = _Scope(section=section)
        url = request.get("url")
        self._scan_template(url, join_path(section, "url"), scope)
        for name, template in (request.get("headers") or {}).items():
            self._scan_template(template, join_path(join_path(section, "headers"), name), scope)
        self._check_header_names(join_path(section, "headers"), request.get("headers") or {})
        if "body" in request:
            self._scan_node(request["body"], join_path(section, "body"), scope)
        self._check_auth_collisions(section, request, url)
        self._check_extract(section, request.get("extract") or {})
        if section == "poll" and "task_id" not in placeholder_names(json.dumps(request, ensure_ascii=False)):
            self._warn("poll", DefinitionErrorCode.POLL_WITHOUT_TASK_ID)

    def _check_header_names(self, path: str, headers: Mapping[str, Any]) -> None:
        self.errors.extend(duplicate_header_issues(path, headers))

    def _check_auth_collisions(self, section: str, request: Mapping[str, Any], url: object) -> None:
        auth_headers = {name.lower() for name in (self._auth.get("headers") or {})}
        for name in request.get("headers") or {}:
            if name.lower() in auth_headers:
                self._error(
                    join_path(join_path(section, "headers"), name),
                    DefinitionErrorCode.AUTH_HEADER_CONFLICT,
                    header=name,
                )
        auth_query = set(self._auth.get("query") or {})
        for name in sorted(_url_query_names(url) & auth_query):
            self._error(join_path(section, "url"), DefinitionErrorCode.AUTH_QUERY_CONFLICT, param=name)

    def _check_extract(self, section: str, extract: Mapping[str, Any]) -> None:
        base = join_path(section, "extract")
        for key, spec in extract.items():
            if key in _ALL_ARTIFACT_KEYS and key not in self._rules.artifact_keys:
                self.errors.append(self._media_type_mismatch(join_path(base, key), key))
            if key == "usage":
                if not self._rules.usage_extract:
                    self.errors.append(self._media_type_mismatch(join_path(base, key), key))
                for usage_key, usage_spec in spec.items():
                    self._check_extract_spec(usage_spec, join_path(join_path(base, "usage"), usage_key))
                continue
            self._check_extract_spec(spec, join_path(base, key))

    def _check_extract_spec(self, spec: object, path: str) -> None:
        items = spec.get("paths", []) if isinstance(spec, dict) else spec
        if not isinstance(items, list):
            return
        for index, item in enumerate(items):
            item_path = join_path(path, index)
            if not isinstance(item, dict):
                self._check_json_path(item, item_path)
                continue
            self._check_json_path(item.get("path"), item_path)
            for then_index, then_path in enumerate(item.get("then") or []):
                self._check_json_path(then_path, join_path(join_path(item_path, "then"), then_index))

    def _check_json_path(self, source: object, path: str) -> None:
        try:
            parsed = parse_json_path(source)
        except JsonPathSubsetError as exc:
            self._error(path, exc.code, path_expression=exc.source, position=exc.position)
            return
        if parsed.has_wildcard:
            self._warn(path, DefinitionErrorCode.JSONPATH_WILDCARD_ORDER, path_expression=parsed.source)

    # ---- Template scanning ----

    def _scan_node(self, node: object, path: str, scope: _Scope, *, in_array: bool = False) -> None:
        if isinstance(node, str):
            self._scan_template(node, path, scope)
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                self._scan_node(item, join_path(path, index), scope, in_array=True)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == "$when":
                if value not in self._inputs:
                    self._error(join_path(path, key), DefinitionErrorCode.WHEN_UNKNOWN_INPUT, name=str(value))
                elif scope.section != "submit":
                    self._error(join_path(path, key), DefinitionErrorCode.INPUT_OUT_OF_SCOPE, name=str(value))
                continue
            if key == "$each":
                self._scan_each(value, join_path(path, key), scope, in_array=in_array)
                continue
            self._scan_node(value, join_path(path, key), scope)

    def _scan_each(self, directive: Mapping[str, Any], path: str, scope: _Scope, *, in_array: bool) -> None:
        # Expand elements in arrays and key/value pairs in objects: their output shapes differ, so reversed forms have no executable meaning.
        if in_array != ("item" in directive):
            self._error(path, DefinitionErrorCode.EACH_POSITION_MISMATCH)
        # Inside loops, `index` is always the ordinal; using it as the element alias makes the two values inseparable.
        if directive.get("as") == "index":
            self._error(join_path(path, "as"), DefinitionErrorCode.EACH_ALIAS_RESERVED, name="index")
        name = str(directive.get("in", "")).removeprefix("inputs.")
        if name not in self._list_inputs:
            self._error(join_path(path, "in"), DefinitionErrorCode.EACH_IN_NOT_LIST_INPUT, name=name)
        elif scope.section != "submit":
            self._error(join_path(path, "in"), DefinitionErrorCode.INPUT_OUT_OF_SCOPE, name=name)
        else:
            self._referenced_inputs.add(name)
        inner = scope.with_locals({str(directive.get("as")), "index"})
        for key in ("item", "key", "value"):
            if key in directive:
                self._scan_node(directive[key], join_path(path, key), inner)

    def _scan_template(self, template: object, path: str, scope: _Scope) -> None:
        if not isinstance(template, str):
            return
        for fragment in malformed_placeholders(template):
            self._error(path, DefinitionErrorCode.MALFORMED_PLACEHOLDER, fragment=fragment)
        for name in placeholder_names(template):
            self._check_variable(name, path, scope)

    def _check_variable(self, name: str, path: str, scope: _Scope) -> None:
        issue = self._variable_issue(name, path, scope)
        if issue is not None:
            self.errors.append(issue)

    def _variable_issue(self, name: str, path: str, scope: _Scope) -> DefinitionIssue | None:
        """Check whether one placeholder reference is out of scope and return its diagnostic if so.

        Return diagnostics rather than recording them in place: auth checks use the implementation shared by both kinds
        and need the diagnostic itself to categorize by severity.
        """
        if name == "api_key":
            return None if scope.section == "auth" else DefinitionIssue(path, DefinitionErrorCode.API_KEY_OUTSIDE_AUTH)
        if name == "task_id":
            if scope.section in {"poll", "result"}:
                return None
            return DefinitionIssue(path, DefinitionErrorCode.TASK_ID_OUT_OF_SCOPE)
        if name == "result_id":
            return self._result_id_issue(path, scope)
        if name in scope.locals or name in self._rules.variables:
            return None
        if name.startswith("inputs."):
            return self._input_reference_issue(name.removeprefix("inputs."), path, scope)
        if name in _ALL_VARIABLES:
            return self._media_type_mismatch(path, name)
        return DefinitionIssue(path, DefinitionErrorCode.UNDECLARED_VARIABLE, {"name": name})

    def _result_id_issue(self, path: str, scope: _Scope) -> DefinitionIssue | None:
        if scope.section != "result":
            return DefinitionIssue(path, DefinitionErrorCode.RESULT_ID_OUT_OF_SCOPE)
        poll_extract = (self._document.get("poll") or {}).get("extract") or {}
        if "result_id" not in poll_extract:
            return DefinitionIssue(path, DefinitionErrorCode.RESULT_ID_WITHOUT_EXTRACT)
        return None

    def _input_reference_issue(self, name: str, path: str, scope: _Scope) -> DefinitionIssue | None:
        if name not in self._inputs:
            return DefinitionIssue(path, DefinitionErrorCode.UNDECLARED_VARIABLE, {"name": f"inputs.{name}"})
        if scope.section != "submit":
            return DefinitionIssue(path, DefinitionErrorCode.INPUT_OUT_OF_SCOPE, {"name": name})
        if name in self._list_inputs:
            return DefinitionIssue(path, DefinitionErrorCode.LIST_INPUT_REQUIRES_EACH, {"name": name})
        self._referenced_inputs.add(name)
        return None

    # ---- Dictionaries and capabilities ----

    def _check_enum_maps(self) -> None:
        allowed = self._rules.enum_map_variables
        for name in self._document.get("enum_maps") or {}:
            if name in allowed:
                continue
            path = join_path("enum_maps", name)
            if name in _ALL_ENUM_MAP_VARIABLES:
                self.errors.append(self._media_type_mismatch(path, name))
                continue
            self._error(
                path,
                DefinitionErrorCode.ENUM_MAP_VARIABLE_NOT_ALLOWED,
                variable=name,
                allowed=" / ".join(sorted(allowed)),
            )

    def _check_defaults(self) -> None:
        enum_maps: Mapping[str, Mapping[str, Any]] = self._document.get("enum_maps") or {}
        value_types = self._rules.default_value_types
        for name, value in (self._document.get("defaults") or {}).items():
            if name not in value_types:
                path = join_path("defaults", name)
                if name in _ALL_DEFAULTABLE_VARIABLES:
                    self.errors.append(self._media_type_mismatch(path, name))
                    continue
                self._error(
                    path,
                    DefinitionErrorCode.DEFAULT_VARIABLE_NOT_ALLOWED,
                    variable=name,
                    allowed=" / ".join(sorted(value_types)),
                )
                continue
            expected = value_types[name]
            # bool subclasses int: True can masquerade as valid duration / seed; check it separately in reverse.
            if not isinstance(value, expected) or (isinstance(value, bool) and expected is not bool):
                self._error(
                    join_path("defaults", name),
                    DefinitionErrorCode.DEFAULT_VALUE_TYPE_INVALID,
                    variable=name,
                    expected=expected.__name__,
                )
                continue
            # Defaults are ArcReel-side values and still pass through enum_maps during rendering; lookup failure means every request omitting parameters
            # fails at rendering, so such definitions must not be savable.
            mapping = enum_maps.get(name)
            if mapping is not None and enum_map_key(value) not in mapping:
                self._error(
                    join_path("defaults", name),
                    DefinitionErrorCode.DEFAULT_VALUE_NOT_IN_ENUM_MAP,
                    variable=name,
                    value=str(value),
                    allowed=" / ".join(sorted(mapping)),
                )

    def _check_status_map(self) -> None:
        for raw, target in (self._document.get("status_map") or {}).items():
            if target in CANONICAL_STATUSES:
                continue
            self._error(
                join_path("status_map", raw),
                DefinitionErrorCode.STATUS_MAP_TARGET_INVALID,
                target=str(target),
                allowed=" / ".join(sorted(CANONICAL_STATUSES)),
            )

    def _check_inputs_referenced(self) -> None:
        for name in self._inputs:
            if name not in self._referenced_inputs:
                self._error(join_path("inputs", name), DefinitionErrorCode.INPUT_NOT_REFERENCED)

    def _check_input_sources(self) -> None:
        for name, declaration in self._inputs.items():
            source = str(declaration.get("source"))
            if source not in self._rules.input_sources:
                self.errors.append(self._media_type_mismatch(join_path(join_path("inputs", name), "source"), source))

    def _check_artifact_extract(self) -> None:
        """The artifact section must contain at least one artifact-extraction key for this media type.

        ``schema.json`` enforces this structurally for video definitions, so videos do not trigger this check here.
        """
        section = "result" if "result" in self._document else "poll"
        extract: Mapping[str, Any] = self._document[section].get("extract") or {}
        if not any(key in extract for key in self._rules.artifact_keys):
            self._error(
                join_path(section, "extract"),
                DefinitionErrorCode.ARTIFACT_EXTRACT_MISSING,
                keys=" / ".join(self._rules.artifact_keys),
            )

    def _check_capabilities(self) -> None:
        capabilities: Mapping[str, Any] = self._document.get("capabilities") or {}
        for name in capabilities:
            if name not in self._rules.capabilities:
                self.errors.append(self._media_type_mismatch(join_path("capabilities", name), name))
        required = self._rules.required_capabilities
        if required and not any(capabilities.get(name) is True for name in required):
            self._error(
                "capabilities",
                DefinitionErrorCode.CAPABILITY_NOT_DECLARED,
                allowed=" / ".join(required),
            )
        if self._media_type == "video":
            self._check_video_capabilities(capabilities)
        else:
            self._check_image_capabilities(capabilities)

    def _used_sources(self) -> set[object]:
        return {decl.get("source") for name, decl in self._inputs.items() if name in self._referenced_inputs}

    def _check_image_capabilities(self, capabilities: Mapping[str, Any]) -> None:
        """Declare image capabilities explicitly rather than derive them from inputs; declarations, request shape, and reference-image limits must agree."""
        image_to_image = capabilities.get("image_to_image") is True
        path = "capabilities.image_to_image"
        uses_reference_images = "reference_images" in self._used_sources()
        if image_to_image and not uses_reference_images:
            self._error(
                path,
                DefinitionErrorCode.CAPABILITY_DECLARED_WITHOUT_INPUT,
                capability="image_to_image",
                source="reference_images",
            )
        elif not image_to_image and uses_reference_images:
            self._error(
                path,
                DefinitionErrorCode.CAPABILITY_INPUT_WITHOUT_DECLARATION,
                capability="image_to_image",
                source="reference_images",
            )

        # The reference-image limit is part of i2i: orchestration trims references by it; without it, i2i image counts are unbounded.
        has_limit = _capability_is_on("max_reference_images", capabilities.get("max_reference_images"))
        if image_to_image and not has_limit:
            self._error(
                path,
                DefinitionErrorCode.CAPABILITY_INCOHERENT,
                capability="image_to_image",
                requirement="max_reference_images > 0",
            )
        elif has_limit and not image_to_image:
            self._error(
                "capabilities.max_reference_images",
                DefinitionErrorCode.CAPABILITY_INCOHERENT,
                capability="max_reference_images",
                requirement="image_to_image = true",
            )

        # Text-to-image requests carry no references; required reference-image inputs would fail every such request for missing assets before submission.
        if capabilities.get("text_to_image") is True and requires_image_input(self._inputs):
            self._error(
                "capabilities.text_to_image",
                DefinitionErrorCode.CAPABILITY_INCOHERENT,
                capability="text_to_image",
                requirement="inputs.*.required = false",
            )

    def _check_video_capabilities(self, capabilities: Mapping[str, Any]) -> None:
        used_sources = self._used_sources()
        for capability, source in CAPABILITY_SOURCE_PAIRS:
            declared = _capability_is_on(capability, capabilities.get(capability))
            path = join_path("capabilities", capability)
            if declared and source not in used_sources:
                self._error(
                    path,
                    DefinitionErrorCode.CAPABILITY_DECLARED_WITHOUT_INPUT,
                    capability=capability,
                    source=source,
                )
            elif not declared and source in used_sources:
                self._error(
                    path,
                    DefinitionErrorCode.CAPABILITY_INPUT_WITHOUT_DECLARATION,
                    capability=capability,
                    source=source,
                )

        mode = capabilities.get("reference_audio_mode", ReferenceAudioMode.NONE.value)
        count = capabilities.get("max_reference_audio_count", 0)
        if not audio_capability_pair_is_coherent(mode=mode, count=count):
            self._error(
                "capabilities.reference_audio_mode",
                DefinitionErrorCode.CAPABILITY_INCOHERENT,
                capability="reference_audio_mode",
                requirement="max_reference_audio_count > 0",
            )
        if capabilities.get("reference_audio_per_image") is True and mode != ReferenceAudioMode.DIRECT.value:
            self._error(
                "capabilities.reference_audio_per_image",
                DefinitionErrorCode.CAPABILITY_INCOHERENT,
                capability="reference_audio_per_image",
                requirement="reference_audio_mode = direct",
            )
        if capabilities.get("first_frame_ratio_adaptive_only") is True and capabilities.get("first_frame") is not True:
            self._error(
                "capabilities.first_frame_ratio_adaptive_only",
                DefinitionErrorCode.CAPABILITY_INCOHERENT,
                capability="first_frame_ratio_adaptive_only",
                requirement="first_frame = true",
            )

        declared_t2v = capabilities.get("text_to_video")
        if isinstance(declared_t2v, bool):
            requires_image = requires_image_input(self._inputs)
            if declared_t2v is requires_image:
                self._error(
                    "capabilities.text_to_video",
                    DefinitionErrorCode.CAPABILITY_INCOHERENT,
                    capability="text_to_video",
                    requirement=f"text_to_video = {str(not requires_image).lower()}",
                )


def _capability_is_on(capability: str, value: object) -> bool:
    if capability == "max_reference_images":
        # Match schema integer semantics: integer-valued floats such as 1.0 count as integers, but booleans do not.
        return isinstance(value, int | float) and not isinstance(value, bool) and value == int(value) and value > 0
    if capability == "reference_audio_mode":
        return value is not None and value != "none"
    return value is True


def _url_query_names(url: object) -> set[str]:
    if not isinstance(url, str) or "?" not in url:
        return set()
    query = url.split("?", 1)[1].split("#", 1)[0]
    # Compare server-parsed names: `%74oken` and `api+key` are the same parameters as `token` and `api key`, respectively;
    # raw comparison misses duplicate names. Decode with parse_qsl, matching conventional query parsing.
    return {name for name, _ in parse_qsl(query, keep_blank_values=True)}
