"""分享提交：打包端点文件、本地预检，以及把官方服务返回的诊断还原成可翻译的消息。

本地预检与官方服务预检、市场仓 CI 同口径：把文件放进临时市场源目录 ``endpoints/<slug>/`` 生成索引，
生成器已包含市场源校验的全部规则（``docs/adr/0078``）。诊断只报错误；定义校验器的诊断拆出 ``val_ce_*`` 键，
与端点页诊断卡同一套文案。分享额外把疑似字面凭证警告作为阻断诊断，防止凭证随定义公开。
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arcreel_market_core.definition_diagnostics import DefinitionErrorCode
from arcreel_market_core.endpoint_definition import validate_definition
from arcreel_market_core.market import ENDPOINT_ENTRY_TYPE, GenerateError, MarketIssueCode, build_index
from arcreel_market_core.market.generate import DEFINITION_FILENAME, ENDPOINTS_DIR, ICON_STEM
from arcreel_market_core.market.icon import ICON_FORMATS
from arcreel_market_core.market.issues import message_key
from arcreel_market_core.validation_messages import MessageJoin, MessagePart, MessageRef, Translator, ValidationMessage

#: 首批只接受调用端点。
SUBMISSION_TYPE = ENDPOINT_ENTRY_TYPE
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
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
            return [
                SubmissionDiagnostic(DEFINITION_FILENAME, issue.path, issue.message)
                for issue in validate_definition(json.loads(files[DEFINITION_FILENAME])).warnings
                if issue.code is DefinitionErrorCode.AUTH_LITERAL_CREDENTIAL
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
