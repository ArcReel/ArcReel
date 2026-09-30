"""分享提交：打包端点文件、本地预检，以及把官方服务返回的诊断还原成可翻译的消息。

本地预检与官方服务预检、市场仓 CI 同口径：把文件放进临时市场源目录 ``endpoints/<slug>/`` 生成索引，
生成器已包含市场源校验的全部规则（``docs/adr/0078``）。诊断只报错误；定义校验器的诊断拆出 ``val_ce_*`` 键，
与端点页诊断卡同一套文案。分享额外把疑似字面凭证警告作为阻断诊断，防止凭证随定义公开；
请求内容随定义原样公开（ComfyUI 的整份 workflow，声明式 submit / poll / result 三节中除响应取值 extract 外的全部内容），
其中任意层级键名或 URL 查询参数名形似凭证、剔除占位符后仍有字面值的同样阻断；请求头的值另按字面凭证形态判定。
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

#: 首批只接受调用端点。
SUBMISSION_TYPE = ENDPOINT_ENTRY_TYPE
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: 键名按分隔符与大小写切分、去掉复数 s 后含这些片段即视为凭证；只看键名，模型文件名等长串字面值不受影响，
#: ``tokenizer`` 这类键名也不命中。``Proxy-Authorization``、``Set-Cookie`` 按片段命中。
CREDENTIAL_NAME_PARTS = frozenset(
    {"apikey", "token", "secret", "password", "passwd", "credential", "authorization", "cookie"}
)
#: 请求头映射的键名：其下的值另按字面凭证形态判定，覆盖自定义头名。
_HEADERS = "headers"
LITERAL_CREDENTIAL_KEY = "market_submission_literal_credential"
#: 各类定义随分享原样公开的请求内容所在的节；``auth`` 节由定义校验器的字面凭证检查负责。
_REQUEST_SECTIONS = {"comfyui": ("workflow",), "declarative": ("submit", "poll", "result")}
#: 声明式请求节里的响应取值：键是取值名、值是 JSONPath，不进请求，不扫描。
_RESPONSE_EXTRACT = "extract"
#: 请求里允许的图标文件名，与市场仓目录约定一致。
ICON_FILENAMES = frozenset(f"{ICON_STEM}.{extension}" for extension in ICON_FORMATS)


@dataclass(frozen=True)
class SubmissionDiagnostic:
    """一条提交诊断。``file`` 相对条目目录（slug 本身不合规时为空串），``path`` 是文件内的位置。"""

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
    """条目目录内的文件：定义本体原样序列化，外加可选图标。"""
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
    """请求内容里键名或 URL 查询参数名形似凭证、剔除占位符后仍有字面值的位置。"""
    for section in _REQUEST_SECTIONS.get(str(definition.get("kind")), ()):
        content = definition.get(section)
        if isinstance(content, Mapping):
            content = {key: value for key, value in content.items() if key != _RESPONSE_EXTRACT}
        yield from _literal_credentials(section, content, named_credential=False, in_headers=False)


def _literal_credentials(path: str, value: Any, *, named_credential: bool, in_headers: bool) -> Iterator[str]:
    """递归遍历字典与列表：凭证键名下的字符串（含其中嵌套的各层）、字符串里的 URL 查询参数名形似凭证，有字面值即命中；
    请求头映射下的字符串形似字面凭证也命中。"""
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
    """官方服务 ``submission_invalid`` 的 ``params.diagnostics``；形状不对的条目跳过。"""
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
    """参数里嵌套的 ``{code, params}`` 是消息，先翻译再代入外层；列表还原成片段序列。"""
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
