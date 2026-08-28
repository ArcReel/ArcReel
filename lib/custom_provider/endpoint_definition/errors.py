"""声明式定义的诊断载体：稳定错误码 + 定位路径 + locale-neutral 消息。

保存、``validate`` 接口、端点测试与 import 期共用同一份诊断，消费边界各自渲染语言，因此产出
点只带 ``ValidationMessage``（key + params），不带成品文案。``code`` 是跨边界的稳定契约：前端
按它决定高亮哪一节，测试按它断言，翻译按它取文案（每个码都有 ``val_ce_<code>`` 消息键）。

``path`` 是定义 JSON 内的定位串，根为 ``$``，其余按 ``submit.extract.video_url[0]`` 这样的
点号 + 下标写法拼出，直接对应 UI 里的字段。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lib.validation_messages import ValidationMessage

#: 诊断消息键的统一前缀：``code`` 与消息键一一对应，新增码必须同步三种语言的消息。
MESSAGE_KEY_PREFIX = "val_ce_"

#: 定义内的根路径。
ROOT_PATH = "$"


class DefinitionErrorCode(StrEnum):
    """定义校验能产出的全部诊断码。

    前半段是结构层（JSON Schema 转译而来），后半段是语义层（占位符作用域、凭证唯一写入口、
    能力两向一致、JSONPath 子集）。warning 码单列在末尾。
    """

    # ---- 结构层 ----
    MISSING_FIELD = "missing_field"
    UNKNOWN_FIELD = "unknown_field"
    REMOVED_FIELD = "removed_field"
    INVALID_TYPE = "invalid_type"
    INVALID_ENUM_VALUE = "invalid_enum_value"
    INVALID_VALUE = "invalid_value"
    SCHEMA_VIOLATION = "schema_violation"

    # ---- 占位符作用域 ----
    MALFORMED_PLACEHOLDER = "malformed_placeholder"
    UNDECLARED_VARIABLE = "undeclared_variable"
    API_KEY_OUTSIDE_AUTH = "api_key_outside_auth"
    AUTH_WITHOUT_API_KEY = "auth_without_api_key"
    AUTH_HEADER_CONFLICT = "auth_header_conflict"
    HEADER_NAME_DUPLICATE = "header_name_duplicate"
    AUTH_QUERY_CONFLICT = "auth_query_conflict"
    TASK_ID_OUT_OF_SCOPE = "task_id_out_of_scope"
    RESULT_ID_OUT_OF_SCOPE = "result_id_out_of_scope"
    RESULT_ID_WITHOUT_EXTRACT = "result_id_without_extract"

    # ---- 素材与展开构造 ----
    INPUT_OUT_OF_SCOPE = "input_out_of_scope"
    LIST_INPUT_REQUIRES_EACH = "list_input_requires_each"
    EACH_IN_NOT_LIST_INPUT = "each_in_not_list_input"
    EACH_SHAPE_INVALID = "each_shape_invalid"
    EACH_POSITION_MISMATCH = "each_position_mismatch"
    EACH_ALIAS_RESERVED = "each_alias_reserved"
    WHEN_UNKNOWN_INPUT = "when_unknown_input"
    INPUT_NOT_REFERENCED = "input_not_referenced"

    # ---- 字典与能力 ----
    ENUM_MAP_VARIABLE_NOT_ALLOWED = "enum_map_variable_not_allowed"
    DEFAULT_VARIABLE_NOT_ALLOWED = "default_variable_not_allowed"
    DEFAULT_VALUE_TYPE_INVALID = "default_value_type_invalid"
    DEFAULT_VALUE_NOT_IN_ENUM_MAP = "default_value_not_in_enum_map"
    STATUS_MAP_TARGET_INVALID = "status_map_target_invalid"
    CAPABILITY_DECLARED_WITHOUT_INPUT = "capability_declared_without_input"
    CAPABILITY_INPUT_WITHOUT_DECLARATION = "capability_input_without_declaration"
    CAPABILITY_INCOHERENT = "capability_incoherent"

    # ---- JSONPath 子集 ----
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

    # ---- 渲染期（端点测试按同一份诊断结构下发）----
    TEMPLATE_RENDER_FAILED = "template_render_failed"

    # ---- warning ----
    POLL_WITHOUT_TASK_ID = "poll_without_task_id"
    JSONPATH_WILDCARD_ORDER = "jsonpath_wildcard_order"


def message_key(code: DefinitionErrorCode) -> str:
    """诊断码对应的 i18n 消息键。"""
    return f"{MESSAGE_KEY_PREFIX}{code.value}"


@dataclass(frozen=True)
class DefinitionIssue:
    """一条诊断：定位路径 + 稳定码 + 待渲染的消息。"""

    path: str
    code: DefinitionErrorCode
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> ValidationMessage:
        return ValidationMessage(message_key(self.code), self.params)

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, str]:
        """渲染成接口契约里的 ``{path, code, message}``。"""
        return {"path": self.path, "code": self.code.value, "message": self.message.render(translate)}


@dataclass(frozen=True)
class DefinitionDiagnostics:
    """一次校验的完整结果。

    ``errors`` 非空即拒绝写入；``warnings`` 只提示（如轮询没引用 task_id），不拦保存。
    """

    errors: tuple[DefinitionIssue, ...] = ()
    warnings: tuple[DefinitionIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, list[dict[str, str]]]:
        return {
            "errors": [issue.to_payload(translate) for issue in self.errors],
            "warnings": [issue.to_payload(translate) for issue in self.warnings],
        }


def join_path(base: str, key: str | int) -> str:
    """把一段键名或下标接到定位路径末尾。"""
    if isinstance(key, int):
        return f"{base}[{key}]"
    return key if base == ROOT_PATH else f"{base}.{key}"
