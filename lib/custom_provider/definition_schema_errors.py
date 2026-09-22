"""把 jsonschema 的报错译成定义诊断：每种 ``kind`` 的结构层共用这一份翻译。

各 kind 的结构契约是各自的 ``schema.json``，但「缺字段报哪个码、定位串怎么拼、组合关键字下钻到
哪一层」是同一套契约——消费方按 ``code`` 决定高亮哪一节，两份 schema 各译一套只会让同一个结构
错误在两种定义上得到不同的码。

``removed_fields`` 由调用方传入：字段是否「已移除」是某一种 kind 的历史，不是翻译层的知识。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from jsonschema.exceptions import ValidationError

from lib.infra.validation_messages import MessageRef, ValidationMessage

from .definition_diagnostics import ROOT_PATH, DefinitionErrorCode, DefinitionIssue, join_path

#: 声明式 schema 里给 ``$each`` 打的标记：它的 ``oneOf`` 是互斥形态而非分支联合。
EACH_SHAPE_MARKER = "each_shape"

_ENUM_KEYWORDS = frozenset({"enum", "const"})

#: 同一深度上多条分支报错时的取舍：缺字段 / 多字段最能说明问题，笼统的类型错最没用。
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


def most_specific(error: ValidationError) -> ValidationError:
    """``anyOf`` / ``oneOf`` 的报错落在组合关键字上，逐层下钻到真正不匹配的那条子规则。

    组合里每条分支都会报错，取「定位最深、说法最具体」的那条：结构模板的分支union里，
    「``body`` 不是字符串」这种最外层的类型错对写定义的人毫无用处，真正要看的是深处那句
    「``$each`` 缺 item」。互斥形态的组合（``$each`` 的 item 与 key/value）停在组合关键字
    上：下钻只会挑中某条分支缺哪个字段，而真正的问题是两种写法混用。
    """
    while error.context and not _is_mutually_exclusive_shape(error):
        error = max(error.context, key=_specificity)
    return error


def translate_schema_error(error: ValidationError, *, removed_fields: Mapping[str, str]) -> Iterator[DefinitionIssue]:
    """把一条 jsonschema 报错译成零条或多条诊断。"""
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
    """把 jsonschema 的定位序列拼成定义内的定位串。"""
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
    """把 jsonschema 的英文散文收成少量 locale-neutral 约束模板。"""
    keyword = str(error.validator)
    if keyword in _MINIMUM_KEYWORDS:
        return ValidationMessage("val_ce_schema_minimum_constraint", {"limit": error.validator_value})
    if keyword in _MAXIMUM_KEYWORDS:
        return ValidationMessage("val_ce_schema_maximum_constraint", {"limit": error.validator_value})
    if keyword in _FORMAT_KEYWORDS:
        return ValidationMessage("val_ce_schema_format_constraint", {"constraint": error.validator_value})
    if keyword == "not":
        return ValidationMessage("val_ce_schema_forbidden_constraint")
    return ValidationMessage("val_ce_schema_generic_constraint", {"keyword": keyword})


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
