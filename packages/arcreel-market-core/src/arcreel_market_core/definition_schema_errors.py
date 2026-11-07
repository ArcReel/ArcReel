"""Translate jsonschema errors into definition diagnostics, sharing one translation for every ``kind``'s structure layer.

Each kind has its own ``schema.json`` structure contract, but missing-field codes, location paths, and how deeply to descend through composition keywords
share one contract. Consumers use ``code`` to highlight sections; separate translators for the two schemas would assign the same structural
error different codes across definition kinds.

Callers supply ``removed_fields``: removed fields are history specific to a kind, not knowledge owned by the translation layer.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from jsonschema.exceptions import ValidationError

from arcreel_market_core.validation_messages import MessageRef, ValidationMessage

from .definition_diagnostics import ROOT_PATH, DefinitionErrorCode, DefinitionIssue, join_path

#: Marker for ``$each`` in the declarative schema: its ``oneOf`` represents mutually exclusive forms rather than a branch union.
EACH_SHAPE_MARKER = "each_shape"

_ENUM_KEYWORDS = frozenset({"enum", "const"})

#: Preference among errors at the same depth: missing / extra fields are most informative, generic type errors least informative.
_KEYWORD_SPECIFICITY: Mapping[str, int] = {
    "type": 0,
    "anyOf": 1,
    "oneOf": 1,
    "not": 1,
    "required": 3,
    "additionalProperties": 3,
}

_VALUE_SHAPE_KEYWORDS = frozenset(
    {"pattern", "format", "minLength", "maxLength", "minimum", "maximum", "minItems", "minProperties", "propertyNames"}
)

_MINIMUM_KEYWORDS = frozenset({"minLength", "minimum", "minItems", "minProperties"})
_MAXIMUM_KEYWORDS = frozenset({"maxLength", "maximum"})
_FORMAT_KEYWORDS = frozenset({"pattern", "format", "propertyNames"})

#: Detail templates for jsonschema constraint violations: normalize third-party English prose into a small set of locale-neutral message keys.
SCHEMA_MINIMUM_KEY = "val_ce_schema_minimum_constraint"
SCHEMA_MAXIMUM_KEY = "val_ce_schema_maximum_constraint"
SCHEMA_FORMAT_KEY = "val_ce_schema_format_constraint"
SCHEMA_FORBIDDEN_KEY = "val_ce_schema_forbidden_constraint"
SCHEMA_GENERIC_KEY = "val_ce_schema_generic_constraint"
SCHEMA_CONSTRAINT_KEYS = (
    SCHEMA_MINIMUM_KEY,
    SCHEMA_MAXIMUM_KEY,
    SCHEMA_FORMAT_KEY,
    SCHEMA_FORBIDDEN_KEY,
    SCHEMA_GENERIC_KEY,
)


def most_specific(error: ValidationError) -> ValidationError:
    """``anyOf`` / ``oneOf`` errors land on composition keywords; descend to the actual mismatching subrule.

    Every branch in a composition reports errors; choose the deepest, most specific one. Within structural template branch unions,
    an outer type error such as ``body`` not being a string is unhelpful to definition authors; the relevant error is the nested
    missing item in ``$each``. For mutually exclusive forms (``$each`` item versus key/value), stop at the composition keyword:
    descending would select one branch's missing field, when the actual issue is mixing the two forms.
    """
    while error.context and not _is_mutually_exclusive_shape(error):
        error = max(error.context, key=_specificity)
    return error


def translate_schema_error(error: ValidationError, *, removed_fields: Mapping[str, str]) -> Iterator[DefinitionIssue]:
    """Translate one jsonschema error into zero or more diagnostics."""
    path = format_path(error.absolute_path)
    keyword = str(error.validator)
    if _is_mutually_exclusive_shape(error):
        yield DefinitionIssue(path, DefinitionErrorCode.EACH_SHAPE_INVALID)
        return
    if keyword == "required":
        yield from _missing_field_issues(error, path)
        return
    if keyword == "additionalProperties":
        yield from _extra_field_issues(error, path, removed_fields)
        return
    if keyword == "type":
        yield DefinitionIssue(
            path, DefinitionErrorCode.INVALID_TYPE, {"expected": _format_allowed(error.validator_value)}
        )
        return
    if keyword in _ENUM_KEYWORDS:
        yield DefinitionIssue(
            path, DefinitionErrorCode.INVALID_ENUM_VALUE, {"allowed": _format_allowed(error.validator_value)}
        )
        return
    if keyword in _VALUE_SHAPE_KEYWORDS:
        yield DefinitionIssue(
            path,
            DefinitionErrorCode.INVALID_VALUE,
            {"detail": _schema_detail(error)},
        )
        return
    yield DefinitionIssue(
        path,
        DefinitionErrorCode.SCHEMA_VIOLATION,
        {"detail": _schema_detail(error)},
    )


def format_path(parts: Sequence[str | int]) -> str:
    """Join jsonschema location components into a path within the definition."""
    path = ROOT_PATH
    for part in parts:
        path = join_path(path, part)
    return path


def _is_mutually_exclusive_shape(error: ValidationError) -> bool:
    schema = error.schema if isinstance(error.schema, dict) else {}
    return str(error.validator) == "oneOf" and schema.get("$comment") == EACH_SHAPE_MARKER


def _specificity(error: ValidationError) -> tuple[int, int]:
    return len(error.absolute_path), _KEYWORD_SPECIFICITY.get(str(error.validator), 2)


def _schema_detail(error: ValidationError) -> ValidationMessage:
    """Normalize jsonschema English prose into a small set of locale-neutral constraint templates."""
    keyword = str(error.validator)
    if keyword in _MINIMUM_KEYWORDS:
        return ValidationMessage(SCHEMA_MINIMUM_KEY, {"limit": error.validator_value})
    if keyword in _MAXIMUM_KEYWORDS:
        return ValidationMessage(SCHEMA_MAXIMUM_KEY, {"limit": error.validator_value})
    if keyword in _FORMAT_KEYWORDS:
        return ValidationMessage(SCHEMA_FORMAT_KEY, {"constraint": error.validator_value})
    if keyword == "not":
        return ValidationMessage(SCHEMA_FORBIDDEN_KEY)
    return ValidationMessage(SCHEMA_GENERIC_KEY, {"keyword": keyword})


def _missing_field_issues(error: ValidationError, path: str) -> Iterator[DefinitionIssue]:
    instance = error.instance if isinstance(error.instance, dict) else {}
    required = error.validator_value if isinstance(error.validator_value, list) else []
    for name in required:
        if name not in instance:
            yield DefinitionIssue(path, DefinitionErrorCode.MISSING_FIELD, {"field": str(name)})


def _extra_field_issues(
    error: ValidationError, path: str, removed_fields: Mapping[str, str]
) -> Iterator[DefinitionIssue]:
    schema = error.schema if isinstance(error.schema, dict) else {}
    allowed = set(schema.get("properties", {}))
    instance = error.instance if isinstance(error.instance, dict) else {}
    for name in sorted(set(instance) - allowed):
        reason_key = removed_fields.get(name)
        if reason_key is None:
            yield DefinitionIssue(path, DefinitionErrorCode.UNKNOWN_FIELD, {"field": name})
        else:
            yield DefinitionIssue(
                path, DefinitionErrorCode.REMOVED_FIELD, {"field": name, "reason": MessageRef(reason_key)}
            )


def _format_allowed(value: object) -> str:
    if isinstance(value, list):
        return " / ".join("null" if item is None else str(item) for item in value)
    return "null" if value is None else str(value)
