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
from .response_extractor import JsonPathEvaluationError, extract_value, map_status, normalize_extract_spec
from .template_engine import (
    AssetData,
    RenderedRequest,
    TemplateRenderError,
    build_context,
    encode_inputs,
    render_request,
)
from .validator import (
    CURRENT_SCHEMA_VERSION,
    IMAGE_INPUT_SOURCES,
    load_schema,
    requires_image_input,
    validate_definition,
)
from .versioning import (
    SchemaVersionLevel,
    VersionRelation,
    parse_semver,
    schema_version_level,
    version_relation,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "IMAGE_INPUT_SOURCES",
    "MESSAGE_KEY_PREFIX",
    "ROOT_PATH",
    "AssetData",
    "DefinitionDiagnostics",
    "DefinitionErrorCode",
    "DefinitionIssue",
    "JsonPathEvaluationError",
    "JsonPathSubsetError",
    "ParsedJsonPath",
    "RenderedRequest",
    "SchemaVersionLevel",
    "TemplateRenderError",
    "VersionRelation",
    "build_context",
    "encode_inputs",
    "extract_value",
    "load_schema",
    "map_status",
    "message_key",
    "normalize_extract_spec",
    "parse_json_path",
    "parse_semver",
    "render_request",
    "requires_image_input",
    "schema_version_level",
    "validate_definition",
    "version_relation",
]
