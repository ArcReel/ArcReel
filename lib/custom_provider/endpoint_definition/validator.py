"""声明式定义的共享校验器：保存、validate 接口、端点测试与 import 期唯一的判定实现。

两层闸门合起来才算通过：``schema.json`` 管结构（字段集、类型、枚举），本模块管语义——占位符
只能引用声明过的变量、凭证只从 ``auth`` 节写入、能力声明与实际引用的素材两向一致、每条取值
路径落在 JSONPath 受限子集内。两层的产出统一成 :class:`DefinitionIssue`，消费方拿到的永远是
同一套码。

纯逻辑：不碰数据库、不发请求、不读环境，输入是一份已解析的 JSON 值。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from lib.validation_messages import MessageRef
from lib.video_backends.base import ProviderJobStatus, ReferenceAudioMode, audio_capability_pair_is_coherent

from .errors import ROOT_PATH, DefinitionDiagnostics, DefinitionErrorCode, DefinitionIssue, join_path
from .jsonpath_subset import JsonPathSubsetError, parse_json_path
from .template_engine import enum_map_key

SCHEMA_PATH = Path(__file__).parent / "schema.json"

#: 定义格式自身的版本；写入时不改写文件里的 ``schema_version``，校验器也不做定义迁移。
CURRENT_SCHEMA_VERSION = "1.0.0"

#: 请求模板里随时可用的保留变量。``width`` / ``height`` 由比例与分辨率派生，不接受参数。
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

#: 只能做枚举映射的变量：供应商侧改名的都是这几个档位参数，prompt 之类改名没有意义。
ENUM_MAP_VARIABLES = frozenset({"duration", "aspect_ratio", "resolution", "generate_audio"})

#: 可声明缺省值的变量与各自的宿主类型：调用方可以不填的那几个档位参数。base_url / model /
#: prompt 每次调用都带，width / height 由比例与分辨率派生，给它们声明缺省值只会掩盖真正的
#: 缺参。类型即 ArcReel 侧参数类型——缺省值渲染前原样进上下文，错型的 aspect_ratio /
#: resolution 会让宽高派生拿不到档位（引用 {{ width }} 的定义每次未指定参数都渲染失败），
#: 错型的 duration / generate_audio 则把整值占位符保留原生类型的语义带歪。
DEFAULT_VALUE_TYPES: dict[str, type] = {
    "duration": int,
    "aspect_ratio": str,
    "resolution": str,
    "generate_audio": bool,
    "seed": int,
}
DEFAULTABLE_VARIABLES = frozenset(DEFAULT_VALUE_TYPES)

#: 列表型素材来源：只能经 ``$each`` 展开，直接内插会把整个列表串化进请求。
LIST_INPUT_SOURCES = frozenset({"reference_images", "reference_audio_files"})

#: 图片型素材来源（``inputs.*.source`` 枚举中除参考音频外的全部）。声明为必需的图输入意味着该
#: 请求形状必须带图，``capabilities.text_to_video`` 由这份集合推导；校验器与
#: :mod:`lib.custom_provider.capabilities` 的合成共用同一份，两处不得各存一份——集合漂移会让
#: 保存期放行的声明在合成期得出相反的 ``text_to_video``。
IMAGE_INPUT_SOURCES = frozenset({"start_image", "end_image", "reference_images"})


def requires_image_input(inputs: Mapping[str, Any] | None) -> bool:
    """该定义描述的请求形状是否必须带图——``capabilities.text_to_video`` 的推导来源。

    校验器的一致性检查、能力合成与 spec 投影三处共读本函数：``text_to_video`` 是从请求形状推导
    出来的位，定义里的显式声明只是一份冗余断言（不一致时保存期即报 ``capability_incoherent``）。
    任一消费方改从 ``capabilities`` 节直接取值，就会对不声明该位的合法定义得出与请求形状相反的
    结论。
    """
    return any(
        isinstance(declaration, Mapping)
        and declaration.get("required") is True
        and declaration.get("source") in IMAGE_INPUT_SOURCES
        for declaration in (inputs or {}).values()
    )


#: 声明式能产出的状态档位。``expired`` 不由声明式产生，过期语义映射到 ``failed``。
CANONICAL_STATUSES = frozenset(
    {
        ProviderJobStatus.QUEUED.value,
        ProviderJobStatus.RUNNING.value,
        ProviderJobStatus.SUCCEEDED.value,
        ProviderJobStatus.FAILED.value,
    }
)

#: 能力位与素材来源的配对：两向一致性检查逐对跑，谎报与漏报都拦。
CAPABILITY_SOURCE_PAIRS: tuple[tuple[str, str], ...] = (
    ("first_frame", "start_image"),
    ("last_frame", "end_image"),
    ("max_reference_images", "reference_images"),
    ("reference_audio_mode", "reference_audio_files"),
)

#: 格式不接受、但写定义的人容易写出来的字段名 → 其去处：单独报「字段已移除」比笼统的「未知字段」好用。
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
    "media_type": "val_ce_removed_reason_media_type",
}

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")

#: 模板里每一处 ``{{``。不落在 ``_PLACEHOLDER`` 起点上的即写法不合法：格式只认裸变量，
#: 过滤器、下标、表达式与未闭合的开括号都不是占位符，渲染时会原样发给供应商。
_PLACEHOLDER_OPEN = re.compile(r"\{\{")

_ENUM_KEYWORDS = frozenset({"enum", "const"})

#: ``schema.json`` 里给 ``$each`` 打的标记：它的 ``oneOf`` 是互斥形态而非分支联合。
_EACH_SHAPE_MARKER = "each_shape"

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


@cache
def load_schema() -> dict[str, Any]:
    """读入并缓存 ``schema.json``。对外公开，供文档站、Agent skill 与前端取同一份契约。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def _schema_validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_definition(document: object) -> DefinitionDiagnostics:
    """校验一份定义 JSON。

    结构层有错时不再跑语义层：占位符与能力检查都以字段形状成立为前提，在残缺结构上继续跑只会
    产出误导性的次生错误。
    """
    structural = tuple(_structural_issues(document))
    if structural or not isinstance(document, dict):
        return DefinitionDiagnostics(errors=structural)
    checker = _SemanticChecker(document)
    checker.run()
    return DefinitionDiagnostics(errors=tuple(checker.errors), warnings=tuple(checker.warnings))


# ---------------------------------------------------------------- 结构层


def _structural_issues(document: Any) -> Iterator[DefinitionIssue]:
    for error in _schema_validator().iter_errors(document):
        yield from _translate_schema_error(_most_specific(error))


def _most_specific(error: ValidationError) -> ValidationError:
    """``anyOf`` / ``oneOf`` 的报错落在组合关键字上，逐层下钻到真正不匹配的那条子规则。

    组合里每条分支都会报错，取「定位最深、说法最具体」的那条：结构模板的分支union里，
    「``body`` 不是字符串」这种最外层的类型错对写定义的人毫无用处，真正要看的是深处那句
    「``$each`` 缺 item」。互斥形态的组合（``$each`` 的 item 与 key/value）停在组合关键字
    上：下钻只会挑中某条分支缺哪个字段，而真正的问题是两种写法混用。
    """
    while error.context and not _is_mutually_exclusive_shape(error):
        error = max(error.context, key=_specificity)
    return error


def _is_mutually_exclusive_shape(error: ValidationError) -> bool:
    schema = error.schema if isinstance(error.schema, dict) else {}
    return str(error.validator) == "oneOf" and schema.get("$comment") == _EACH_SHAPE_MARKER


def _specificity(error: ValidationError) -> tuple[int, int]:
    return len(error.absolute_path), _KEYWORD_SPECIFICITY.get(str(error.validator), 2)


def _translate_schema_error(error: ValidationError) -> Iterator[DefinitionIssue]:
    path = _format_path(error.absolute_path)
    keyword = str(error.validator)
    if _is_mutually_exclusive_shape(error):
        yield DefinitionIssue(path, DefinitionErrorCode.EACH_SHAPE_INVALID)
        return
    if keyword == "required":
        yield from _missing_field_issues(error, path)
        return
    if keyword == "additionalProperties":
        yield from _extra_field_issues(error, path)
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
        yield DefinitionIssue(path, DefinitionErrorCode.INVALID_VALUE, {"detail": error.message})
        return
    yield DefinitionIssue(path, DefinitionErrorCode.SCHEMA_VIOLATION, {"detail": error.message})


def _missing_field_issues(error: ValidationError, path: str) -> Iterator[DefinitionIssue]:
    instance = error.instance if isinstance(error.instance, dict) else {}
    required = error.validator_value if isinstance(error.validator_value, list) else []
    for name in required:
        if name not in instance:
            yield DefinitionIssue(path, DefinitionErrorCode.MISSING_FIELD, {"field": str(name)})


def _extra_field_issues(error: ValidationError, path: str) -> Iterator[DefinitionIssue]:
    schema = error.schema if isinstance(error.schema, dict) else {}
    allowed = set(schema.get("properties", {}))
    instance = error.instance if isinstance(error.instance, dict) else {}
    for name in sorted(set(instance) - allowed):
        reason_key = REMOVED_FIELD_REASONS.get(name)
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


def _format_path(parts: Sequence[str | int]) -> str:
    path = ROOT_PATH
    for part in parts:
        path = join_path(path, part)
    return path


# ---------------------------------------------------------------- 语义层


class _Scope:
    """占位符的可见范围：所在节决定保留变量是否可用，locals 是 ``$each`` 引入的循环变量。"""

    __slots__ = ("locals", "section")

    def __init__(self, *, section: str, locals_: frozenset[str] = frozenset()) -> None:
        self.section = section
        self.locals = locals_

    def with_locals(self, names: set[str]) -> _Scope:
        return _Scope(section=self.section, locals_=self.locals | frozenset(names))


class _SemanticChecker:
    """在结构成立的定义上跑语义规则，逐条把违规记成诊断。"""

    def __init__(self, document: Mapping[str, Any]) -> None:
        self._document = document
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
        self._check_capabilities()

    # ---- 记录 ----

    def _error(self, path: str, code: DefinitionErrorCode, **params: Any) -> None:
        self.errors.append(DefinitionIssue(path, code, params))

    def _warn(self, path: str, code: DefinitionErrorCode, **params: Any) -> None:
        self.warnings.append(DefinitionIssue(path, code, params))

    # ---- auth ----

    def _check_auth_section(self) -> None:
        headers: Mapping[str, Any] = self._auth.get("headers") or {}
        query: Mapping[str, Any] = self._auth.get("query") or {}
        scope = _Scope(section="auth")
        for group, values in (("headers", headers), ("query", query)):
            for name, template in values.items():
                path = join_path(join_path("auth", group), name)
                self._scan_template(template, path, scope)
                if _looks_like_literal_credential(str(template)):
                    self._warn(path, DefinitionErrorCode.AUTH_LITERAL_CREDENTIAL)
        self._check_header_names(join_path("auth", "headers"), headers)
        if not headers and not query:
            return
        if not any("api_key" in _placeholder_names(str(value)) for value in (*headers.values(), *query.values())):
            self._error("auth", DefinitionErrorCode.AUTH_WITHOUT_API_KEY)

    # ---- 请求节 ----

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
        if section == "poll" and "task_id" not in _placeholder_names(json.dumps(request, ensure_ascii=False)):
            self._warn("poll", DefinitionErrorCode.POLL_WITHOUT_TASK_ID)

    def _check_header_names(self, path: str, headers: Mapping[str, Any]) -> None:
        """同一张头表里不得有大小写不同的同名键：HTTP 头名不区分大小写，两条会一起发出去。"""
        seen: dict[str, str] = {}
        for name in headers:
            first = seen.setdefault(name.lower(), name)
            if first != name:
                self._error(join_path(path, name), DefinitionErrorCode.HEADER_NAME_DUPLICATE, header=name, first=first)

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
            if key == "usage":
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

    # ---- 模板扫描 ----

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
        # 数组位置铺元素、对象位置铺键值对：两种展开的产物形状不同，写反了没有可执行语义。
        if in_array != ("item" in directive):
            self._error(path, DefinitionErrorCode.EACH_POSITION_MISMATCH)
        # 循环体内 `index` 恒为序号：拿它当元素别名，两个值就再也分不开。
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
        for fragment in _malformed_placeholders(template):
            self._error(path, DefinitionErrorCode.MALFORMED_PLACEHOLDER, fragment=fragment)
        for name in _placeholder_names(template):
            self._check_variable(name, path, scope)

    def _check_variable(self, name: str, path: str, scope: _Scope) -> None:
        if name == "api_key":
            if scope.section != "auth":
                self._error(path, DefinitionErrorCode.API_KEY_OUTSIDE_AUTH)
            return
        if name == "task_id":
            if scope.section not in {"poll", "result"}:
                self._error(path, DefinitionErrorCode.TASK_ID_OUT_OF_SCOPE)
            return
        if name == "result_id":
            self._check_result_id(path, scope)
            return
        if name in scope.locals or name in BASE_VARIABLES:
            return
        if name.startswith("inputs."):
            self._check_input_reference(name.removeprefix("inputs."), path, scope)
            return
        self._error(path, DefinitionErrorCode.UNDECLARED_VARIABLE, name=name)

    def _check_result_id(self, path: str, scope: _Scope) -> None:
        if scope.section != "result":
            self._error(path, DefinitionErrorCode.RESULT_ID_OUT_OF_SCOPE)
            return
        poll_extract = (self._document.get("poll") or {}).get("extract") or {}
        if "result_id" not in poll_extract:
            self._error(path, DefinitionErrorCode.RESULT_ID_WITHOUT_EXTRACT)

    def _check_input_reference(self, name: str, path: str, scope: _Scope) -> None:
        if name not in self._inputs:
            self._error(path, DefinitionErrorCode.UNDECLARED_VARIABLE, name=f"inputs.{name}")
            return
        if scope.section != "submit":
            self._error(path, DefinitionErrorCode.INPUT_OUT_OF_SCOPE, name=name)
            return
        if name in self._list_inputs:
            self._error(path, DefinitionErrorCode.LIST_INPUT_REQUIRES_EACH, name=name)
            return
        self._referenced_inputs.add(name)

    # ---- 字典与能力 ----

    def _check_enum_maps(self) -> None:
        for name in self._document.get("enum_maps") or {}:
            if name in ENUM_MAP_VARIABLES:
                continue
            self._error(
                join_path("enum_maps", name),
                DefinitionErrorCode.ENUM_MAP_VARIABLE_NOT_ALLOWED,
                variable=name,
                allowed=" / ".join(sorted(ENUM_MAP_VARIABLES)),
            )

    def _check_defaults(self) -> None:
        enum_maps: Mapping[str, Mapping[str, Any]] = self._document.get("enum_maps") or {}
        for name, value in (self._document.get("defaults") or {}).items():
            if name not in DEFAULTABLE_VARIABLES:
                self._error(
                    join_path("defaults", name),
                    DefinitionErrorCode.DEFAULT_VARIABLE_NOT_ALLOWED,
                    variable=name,
                    allowed=" / ".join(sorted(DEFAULTABLE_VARIABLES)),
                )
                continue
            expected = DEFAULT_VALUE_TYPES[name]
            # bool 是 int 的子类：True 会冒充合法的 duration / seed，反向单判。
            if not isinstance(value, expected) or (isinstance(value, bool) and expected is not bool):
                self._error(
                    join_path("defaults", name),
                    DefinitionErrorCode.DEFAULT_VALUE_TYPE_INVALID,
                    variable=name,
                    expected=expected.__name__,
                )
                continue
            # 缺省值写的是 ArcReel 侧的值，渲染时照常过 enum_maps：查不到表就等于每次未指定参数
            # 的请求都在渲染期失败，这种定义不该存得下来。
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

    def _check_capabilities(self) -> None:
        capabilities: Mapping[str, Any] = self._document.get("capabilities") or {}
        used_sources = {decl.get("source") for name, decl in self._inputs.items() if name in self._referenced_inputs}
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
        return isinstance(value, int) and value > 0
    if capability == "reference_audio_mode":
        return value is not None and value != "none"
    return value is True


def _placeholder_names(text: str) -> list[str]:
    return _PLACEHOLDER.findall(text)


#: 凭证长相：20+ 位含数字的 token 串（API key / JWT / base64 的公共形态）。版本号、固定
#: 字段这类短静态值不命中；误报的代价只是一条不拦保存的 warning。
_CREDENTIAL_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{20,}")


def _looks_like_literal_credential(template: str) -> bool:
    """auth 值剔除占位符后，剩余字面部分是否形似直接写入的凭证。

    凭证以 api_key 占位符形态出现是导出剥凭证的前提；字面凭证会随导出与「复制为我的」
    原样外流，只能靠形态识别提示。
    """
    literal = _PLACEHOLDER.sub(" ", template)
    return any(any(ch.isdigit() for ch in token) for token in _CREDENTIAL_TOKEN.findall(literal))


def _malformed_placeholders(text: str) -> list[str]:
    """所有不构成合法占位符的 ``{{`` 片段，取到最近的 ``}}``（没有就到串尾）。"""
    valid_starts = {match.start() for match in _PLACEHOLDER.finditer(text)}
    fragments: list[str] = []
    for match in _PLACEHOLDER_OPEN.finditer(text):
        if match.start() in valid_starts:
            continue
        closing = text.find("}}", match.start())
        fragments.append(text[match.start() : closing + 2] if closing != -1 else text[match.start() :])
    return fragments


def _url_query_names(url: object) -> set[str]:
    if not isinstance(url, str) or "?" not in url:
        return set()
    query = url.split("?", 1)[1].split("#", 1)[0]
    # 按服务端解析后的名字比对：`%74oken`、`api+key` 与 `token`、`api key` 分别是同一个参数，
    # 原样比对会漏掉重名。解码口径取 parse_qsl，与查询串的通行解析一致。
    return {name for name, _ in parse_qsl(query, keep_blank_values=True)}
