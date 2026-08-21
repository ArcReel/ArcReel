"""产出违约的条目类型与报告渲染，三条 step1 路线共用。

违约是「机器产物没过内容约束」这一件事的通用形状：一条 ``code``（违约类的机读标识）、一个
``label``（定位前缀，如 ``unit E1U02`` / ``segment E1S03``）、一段面向 Agent 的消息，外加可选的
行号与发声准入定位。参考路线、drama、narration 的判定各不相同，但条目形状与「一次判定收齐多条
再渲染成报告」的口径完全相同，故收在本模块——路线中立层（``lib`` 顶层）。

草稿信封（``lib.draft_quarantine``）与三条路线都无关，却要落盘与渲染违约条目；类型住在
参考路线子包里会让它反向依赖一条具体路线。本模块是这些类型的定义处，各路线的校验器与信封
都只依赖它；``lib.reference_video.draft_validation`` 原样再导出这些名字，参考路线的既有导入
路径不变。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any


class DraftViolation(ValueError):
    """草稿产出违约。引用语法误用只是其中一类——原文锚、台词量、台词保真与生成侧的补充
    判定同走这个类型。消息含定位与修复出路，供工具错误信封原样回传给 Agent。

    ``code`` 是违约类的机读标识，``label`` 是定位前缀（``unit E1U02`` / ``segment E1S03`` 一类）：
    消息本身面向 Agent、措辞可改，报告的分组与测试的按类断言不该挂在措辞上。两者均可为空——
    异常在校验器外被构造时（如生成侧的补充判定）只有消息。

    ``line`` 是该单元正文内 0-based 的原始行号（``text.splitlines()`` 坐标系，与前端
    ``toScriptLines`` 的 ``sourceLine`` 同一坐标系），仅在校验发生于具体某一行时才有意义
    （如语法误用）；单元级、无自然行归属的违约（台词量超载、引用未登记等）留空，供呈现层
    区分「行内锚定」与「落卡内聚合区」两条路径。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        label: str = "",
        line: int | None = None,
        locations: tuple[dict[str, object], ...] = (),
        reason: str | None = None,
        action: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.label = label
        self.line = line
        self.locations = locations
        self.reason = reason
        self.action = action


class DraftViolations(DraftViolation):
    """一次校验收集到的多条违约。消息即逐条报告，``items`` 保留结构化条目。

    继承 :class:`DraftViolation` 而非另立类型：既有调用方按 ``DraftViolation`` 捕获与断言，
    聚合体走同一分支才不会在「一条」与「多条」之间分叉出两套处置路径。
    """

    def __init__(self, items: Sequence[DraftViolation]):
        super().__init__(render_violation_report(items), code="multiple", label="")
        self.items: list[DraftViolation] = list(items)


def violation_items(exc: DraftViolation) -> list[DraftViolation]:
    """把单条或聚合的违约一律摊平成条目列表，供报告渲染与待修复草稿落盘取用。"""
    return list(exc.items) if isinstance(exc, DraftViolations) else [exc]


def collect_violations(checks: Iterable[Callable[[], Any]]) -> list[DraftViolation]:
    """依次执行各校验，收集 :class:`DraftViolation` 而不在首个违约处中断。

    单个校验函数内部仍是首个违约即抛（各判定共用一次遍历、后续判定以前面的结论为前提），
    故一次调用最多贡献一条；把每个单元的各个判定入口分别传进来，报告就能覆盖到所有单元而
    不是停在第一个坏单元上——Agent 一轮就能看全要改什么。

    只吞 ``DraftViolation``：其余异常（解析器内部错误、脏数据引发的类型错误）照常上抛，
    不被伪装成一条内容违约。
    """
    found: list[DraftViolation] = []
    for check in checks:
        try:
            check()
        except DraftViolation as exc:
            found.extend(violation_items(exc))
    return found


def render_violation_report(violations: Sequence[DraftViolation]) -> str:
    """把违约条目渲染成逐条编号的报告文本（一行一条，带违约类标注）。"""
    lines: list[str] = []
    for index, violation in enumerate(violations, start=1):
        suffix = f"[{violation.code}] " if violation.code else ""
        lines.append(f"{index}. {suffix}{violation}")
    return "\n".join(lines)


__all__ = [
    "DraftViolation",
    "DraftViolations",
    "collect_violations",
    "render_violation_report",
    "violation_items",
]
