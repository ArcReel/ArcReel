"""Custom invocation-endpoint definition format: contract schema and shared validator.

``validator.validate_definition`` is the sole definition-validation implementation: saving, the ``validate`` interface, endpoint tests,
and bundled-preset import all use it. Separate validation anywhere would reintroduce definitions that save successfully but fail at runtime.
It dispatches by definition ``kind``; the declarative structure contract is ``schema.json``, and ComfyUI implementations live in
``arcreel_market_core.comfyui``。
"""

from arcreel_market_core.definition_diagnostics import (
    MESSAGE_KEY_PREFIX,
    ROOT_PATH,
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    message_key,
)

from .jsonpath_subset import JsonPathSubsetError, ParsedJsonPath, parse_json_path
from .kinds import COMFYUI_KIND, DECLARATIVE_KIND
from .media_type import DEFAULT_DECLARATIVE_MEDIA_TYPE, definition_media_type
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
    current_schema_version,
    load_schema,
    requires_image_input,
    validate_definition,
)
from .versioning import (
    SchemaVersionLevel,
    VersionRelation,
    meets_min_app_version,
    parse_semver,
    schema_version_level,
    version_relation,
)

__all__ = [
    "COMFYUI_KIND",
    "CURRENT_SCHEMA_VERSION",
    "DECLARATIVE_KIND",
    "DEFAULT_DECLARATIVE_MEDIA_TYPE",
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
    "current_schema_version",
    "definition_media_type",
    "encode_inputs",
    "extract_value",
    "load_schema",
    "map_status",
    "meets_min_app_version",
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
