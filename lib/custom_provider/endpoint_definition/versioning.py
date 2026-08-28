"""``schema_version`` 的比对与档位判定。

档位只是给用户的提示信号，**不是闸门**：任何档位的定义都须过当前 schema 才能保存，过不了即
普通校验错误，与版本无关。判定同时服务两处——导入前确认（文件版本 vs 当前版本）与重复血统的
新旧关系（既有定义的 ``meta.version`` vs 文件的）。

版本约定：加可选字段 / 放宽约束走 minor，删字段 / 改语义走 major。
"""

from __future__ import annotations

import re
from enum import StrEnum

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class SchemaVersionLevel(StrEnum):
    """文件 ``schema_version`` 相对当前版本的档位。"""

    #: 同版：直接导入。
    DIRECT = "direct"
    #: 同主版本但更旧：警告放行（当前 schema 只会比它更宽）。
    WARNING = "warning"
    #: 文件更新，或主版本更低：需用户确认。前者可能含本版不认识的字段，后者语义已变。
    #: 版本缺失或不是 semver 时也归此档——判不出新旧就不该悄悄放过去。
    CONFIRM = "confirm"


class VersionRelation(StrEnum):
    """既有定义的 ``meta.version`` 相对导入文件的新旧关系。"""

    NEWER = "newer"
    SAME = "same"
    OLDER = "older"


def parse_semver(value: object) -> tuple[int, int, int] | None:
    """把 ``"1.2.3"`` 解析成可比较的三元组；不是 semver 串时返回 None。"""
    if not isinstance(value, str):
        return None
    match = _SEMVER.match(value)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def schema_version_level(file_version: object, current_version: str) -> SchemaVersionLevel:
    """判定文件版本相对当前版本的档位。"""
    file_parsed = parse_semver(file_version)
    current_parsed = parse_semver(current_version)
    if file_parsed is None or current_parsed is None:
        return SchemaVersionLevel.CONFIRM
    if file_parsed == current_parsed:
        return SchemaVersionLevel.DIRECT
    if file_parsed > current_parsed or file_parsed[0] < current_parsed[0]:
        return SchemaVersionLevel.CONFIRM
    return SchemaVersionLevel.WARNING


def version_relation(existing_version: object, file_version: object) -> VersionRelation:
    """既有定义相对导入文件的新旧。任一侧不可解析时按 ``same`` 处理，只提示重复不谈新旧。"""
    existing_parsed = parse_semver(existing_version)
    file_parsed = parse_semver(file_version)
    if existing_parsed is None or file_parsed is None or existing_parsed == file_parsed:
        return VersionRelation.SAME
    return VersionRelation.NEWER if existing_parsed > file_parsed else VersionRelation.OLDER
