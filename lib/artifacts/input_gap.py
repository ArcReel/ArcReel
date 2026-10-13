"""生成输入的缺口，以及它在产物依据里的缺席记录。

缺口只拦生成这个动作，不阻止依据投影（``docs/adr/0098``）：期望依据照常由已有的输入值构造，
缺的项记在依据的 ``absent`` 里。没有缺口时不写这个键，依据与输入完整时的形态逐字节相同，
已登记条目的摘要不受影响。补齐缺口让依据变化，已登记的产物按常规判过期。
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass

from lib.project.project_schema import parse_project_schema_version

#: 依据里记录缺席输入的键。
ABSENT_INPUTS_FIELD = "absent"

#: 从这一 schema 起生成缺口不阻止依据投影。
GAP_TOLERANT_BASIS_SCHEMA_VERSION = 18


def project_basis_records_gaps(project: Mapping[str, object]) -> bool:
    """项目的期望依据是否把生成缺口记为缺席。

    schema 低于 18 的项目只在迁移链中出现：v17→v18 之前各步的整份激活与来源补写沿用「有缺口即
    投影不出、不登记」的口径，原先因缺口没登记的产物由 v17→v18 改前改后各规划一次后补录。
    """

    return parse_project_schema_version(project) >= GAP_TOLERANT_BASIS_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class InputGap:
    """生成输入不成立的一处缺口。

    ``name`` 是缺口指向的对象：引用缺口是剧本里写的引用名，语义缺口是目标自身的资源 id。
    """

    code: str
    asset_type: str | None = None
    name: str | None = None

    def evidence(self) -> dict[str, object]:
        """缺席记录：缺口码与它指向的对象。"""

        evidence: dict[str, object] = {"code": self.code}
        if self.asset_type is not None:
            evidence["type"] = self.asset_type
        if self.name is not None:
            evidence["id"] = self.name
        return evidence


def add_absent_inputs(inputs: MutableMapping[str, object], gaps: Sequence[InputGap]) -> None:
    """有缺口才把缺席记录写进依据输入，按缺口顺序、逐项去重。"""

    if gaps:
        inputs[ABSENT_INPUTS_FIELD] = [gap.evidence() for gap in dict.fromkeys(gaps)]


__all__ = [
    "ABSENT_INPUTS_FIELD",
    "GAP_TOLERANT_BASIS_SCHEMA_VERSION",
    "InputGap",
    "add_absent_inputs",
    "project_basis_records_gaps",
]
