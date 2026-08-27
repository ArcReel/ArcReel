"""自定义调用端点的声明式定义格式：契约 schema 与共享校验器。

``schema.json`` 是格式的正式契约，``validator.validate_definition`` 是判定它的唯一实现——
保存、``validate`` 接口、端点测试与随版预设的 import 期都走这里，任何一处另写一份判定都会
让「保存能过、跑起来报错」重新出现。
"""

from .errors import (
    MESSAGE_KEY_PREFIX,
    ROOT_PATH,
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    message_key,
)
from .jsonpath_subset import JsonPathSubsetError, ParsedJsonPath, parse_json_path
from .validator import CURRENT_SCHEMA_VERSION, load_schema, validate_definition

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "MESSAGE_KEY_PREFIX",
    "ROOT_PATH",
    "DefinitionDiagnostics",
    "DefinitionErrorCode",
    "DefinitionIssue",
    "JsonPathSubsetError",
    "ParsedJsonPath",
    "load_schema",
    "message_key",
    "parse_json_path",
    "validate_definition",
]
