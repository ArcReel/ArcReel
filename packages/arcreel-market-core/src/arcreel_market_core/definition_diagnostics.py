"""Endpoint definition diagnostics: stable error code + location path + locale-neutral message.

Save, ``validate`` APIs, endpoint tests, and import share diagnostics; each consumer boundary renders its language, so producers
carry only ``ValidationMessage`` (key + params), not finished text. ``code`` is a stable cross-boundary contract: the frontend
uses it to highlight sections, tests assert it, and translation looks up copy from it (each code has a ``val_ce_<code>`` message key).

``path`` locates a position in definition JSON, rooted at ``$``, with subsequent components such as ``submit.extract.video_url[0]``
using dots and indices that correspond directly to UI fields.

This module lives at package root rather than within a specific ``kind`` subpackage: every kind's validator produces these
diagnostics, and placing them in one kind's package would make another kind depend on it in reverse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from arcreel_market_core.validation_messages import Translator, ValidationMessage

#: Common diagnostic message key prefix: ``code`` maps one-to-one to message keys; new codes require updating messages in all three languages.
MESSAGE_KEY_PREFIX = "val_ce_"

#: Root path within a definition.
ROOT_PATH = "$"


class DefinitionErrorCode(StrEnum):
    """All diagnostic codes that definition validation can produce.

    The first section covers structure (translated from JSON Schema), the middle covers declarative semantics (placeholder scopes, the single credential
    write entry point, bidirectional capability consistency, JSONPath subset), followed by ComfyUI semantics (cross-checks between node bindings and
    the workflow). Both kinds share structure and placeholder scope sections. Warning codes are listed separately at the end.
    """

    # ---- Structure ----
    MISSING_FIELD = "missing_field"
    UNKNOWN_FIELD = "unknown_field"
    REMOVED_FIELD = "removed_field"
    INVALID_TYPE = "invalid_type"
    INVALID_ENUM_VALUE = "invalid_enum_value"
    INVALID_VALUE = "invalid_value"
    SCHEMA_VIOLATION = "schema_violation"

    # ---- Placeholder scopes ----
    MALFORMED_PLACEHOLDER = "malformed_placeholder"
    UNDECLARED_VARIABLE = "undeclared_variable"
    API_KEY_OUTSIDE_AUTH = "api_key_outside_auth"
    AUTH_WITHOUT_API_KEY = "auth_without_api_key"
    AUTH_HEADER_CONFLICT = "auth_header_conflict"
    HEADER_NAME_DUPLICATE = "header_name_duplicate"
    AUTH_QUERY_CONFLICT = "auth_query_conflict"
    AUTH_QUERY_RESERVED = "auth_query_reserved"
    TASK_ID_OUT_OF_SCOPE = "task_id_out_of_scope"
    RESULT_ID_OUT_OF_SCOPE = "result_id_out_of_scope"
    RESULT_ID_WITHOUT_EXTRACT = "result_id_without_extract"

    # ---- Media and expansion constructs ----
    INPUT_OUT_OF_SCOPE = "input_out_of_scope"
    LIST_INPUT_REQUIRES_EACH = "list_input_requires_each"
    EACH_IN_NOT_LIST_INPUT = "each_in_not_list_input"
    EACH_SHAPE_INVALID = "each_shape_invalid"
    EACH_POSITION_MISMATCH = "each_position_mismatch"
    EACH_ALIAS_RESERVED = "each_alias_reserved"
    WHEN_UNKNOWN_INPUT = "when_unknown_input"
    INPUT_NOT_REFERENCED = "input_not_referenced"

    # ---- Dictionaries and capabilities ----
    ENUM_MAP_VARIABLE_NOT_ALLOWED = "enum_map_variable_not_allowed"
    DEFAULT_VARIABLE_NOT_ALLOWED = "default_variable_not_allowed"
    DEFAULT_VALUE_TYPE_INVALID = "default_value_type_invalid"
    DEFAULT_VALUE_NOT_IN_ENUM_MAP = "default_value_not_in_enum_map"
    STATUS_MAP_TARGET_INVALID = "status_map_target_invalid"
    CAPABILITY_DECLARED_WITHOUT_INPUT = "capability_declared_without_input"
    CAPABILITY_INPUT_WITHOUT_DECLARATION = "capability_input_without_declaration"
    CAPABILITY_INCOHERENT = "capability_incoherent"
    CAPABILITY_NOT_DECLARED = "capability_not_declared"

    # ---- Media types ----
    MEDIA_TYPE_FIELD_NOT_ALLOWED = "media_type_field_not_allowed"
    ARTIFACT_EXTRACT_MISSING = "artifact_extract_missing"

    # ---- JSONPath subset ----
    JSONPATH_NOT_A_STRING = "jsonpath_not_a_string"
    JSONPATH_SURROUNDING_WHITESPACE = "jsonpath_surrounding_whitespace"
    JSONPATH_MISSING_ROOT = "jsonpath_missing_root"
    JSONPATH_RECURSIVE_DESCENT = "jsonpath_recursive_descent"
    JSONPATH_UNION = "jsonpath_union"
    JSONPATH_SLICE_STEP = "jsonpath_slice_step"
    JSONPATH_FUNCTION_EXTENSION = "jsonpath_function_extension"
    JSONPATH_FILTER_ROOT_REFERENCE = "jsonpath_filter_root_reference"
    JSONPATH_FILTER_NON_SINGULAR = "jsonpath_filter_non_singular"
    JSONPATH_REGEX_OPERATOR = "jsonpath_regex_operator"
    JSONPATH_SYNTAX = "jsonpath_syntax"
    JSONPATH_EVALUATION_FAILED = "jsonpath_evaluation_failed"

    # ---- Rendering (endpoint tests return the same diagnostic shape) ----
    TEMPLATE_RENDER_FAILED = "template_render_failed"
    TEMPLATE_URL_NOT_STRING = "template_url_not_string"
    UNKNOWN_ASSET_ENCODING = "unknown_asset_encoding"
    ENUM_MAP_VALUE_MISSING = "enum_map_value_missing"
    TEMPLATE_TEXT_VARIABLE_NULL = "template_text_variable_null"
    EACH_VALUE_NOT_LIST = "each_value_not_list"
    EACH_KEY_DUPLICATE = "each_key_duplicate"

    # ---- ComfyUI node bindings ----
    COMFYUI_BINDING_REQUIRED = "comfyui_binding_required"
    COMFYUI_BINDING_KEY_NOT_ALLOWED = "comfyui_binding_key_not_allowed"
    COMFYUI_NODE_NOT_FOUND = "comfyui_node_not_found"
    COMFYUI_INPUT_NOT_FOUND = "comfyui_input_not_found"
    COMFYUI_INPUT_IS_LINK = "comfyui_input_is_link"
    COMFYUI_TARGET_COLLISION = "comfyui_target_collision"
    COMFYUI_CLASS_TYPE_MISMATCH = "comfyui_class_type_mismatch"
    COMFYUI_CONSUMER_NOT_FED = "comfyui_consumer_not_fed"
    COMFYUI_FPS_CONFLICT = "comfyui_fps_conflict"

    # ---- ComfyUI import routing ----
    COMFYUI_UI_FORMAT_WORKFLOW = "comfyui_ui_format_workflow"

    # ---- warning ----
    POLL_WITHOUT_TASK_ID = "poll_without_task_id"
    JSONPATH_WILDCARD_ORDER = "jsonpath_wildcard_order"
    AUTH_LITERAL_CREDENTIAL = "auth_literal_credential"


def message_key(code: DefinitionErrorCode) -> str:
    """i18n message key corresponding to a diagnostic code."""
    return f"{MESSAGE_KEY_PREFIX}{code.value}"


@dataclass(frozen=True)
class DefinitionIssue:
    """One diagnostic: location path + stable code + message awaiting rendering."""

    path: str
    code: DefinitionErrorCode
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> ValidationMessage:
        return ValidationMessage(message_key(self.code), self.params)

    def to_payload(self, translate: Translator) -> dict[str, str]:
        """Render as the API contract's ``{path, code, message}``."""
        return {"path": self.path, "code": self.code.value, "message": self.message.render(translate)}


@dataclass(frozen=True)
class DefinitionDiagnostics:
    """Complete result of one validation.

    Nonempty ``errors`` rejects writes; ``warnings`` are informational (such as polling without task_id) and do not block saving.
    """

    errors: tuple[DefinitionIssue, ...] = ()
    warnings: tuple[DefinitionIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_payload(self, translate: Translator) -> dict[str, list[dict[str, str]]]:
        return {
            "errors": [issue.to_payload(translate) for issue in self.errors],
            "warnings": [issue.to_payload(translate) for issue in self.warnings],
        }


def join_path(base: str, key: str | int) -> str:
    """Append a key name or index to a location path."""
    if isinstance(key, int):
        return f"{base}[{key}]"
    return key if base == ROOT_PATH else f"{base}.{key}"
