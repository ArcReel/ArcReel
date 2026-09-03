"""漂移防御：`instructions` 参数在活动文案里只叫「附加指令」。

同一参数曾在工具 schema、prompt 分节标题与子智能体指引中有三套叫法，模型据文案理解参数
语义，多套叫法会让主 Agent 与子智能体对同一入参给出不一致的解释。扫描范围限定活动文案
（后端、Agent profile、前端源码与其测试），历史归档（CHANGELOG、docs/adr、
skill-optimization-workspace 调研笔记）保留原文不纳入。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: 活动文案所在的顶层根，以及各自要扫的后缀。
SCAN_ROOTS: dict[str, frozenset[str]] = {
    "lib": frozenset({".py"}),
    "server": frozenset({".py"}),
    "scripts": frozenset({".py"}),
    "tests": frozenset({".py"}),
    "agent_runtime_profile": frozenset({".md"}),
    "frontend/src": frozenset({".ts", ".tsx"}),
}

#: 历史归档：语句写于旧术语时期，改写等于篡改归档。
EXCLUDED_DIRS = (REPO / "agent_runtime_profile" / "skill-optimization-workspace",)

LEGACY_TERMS = ("用户意见", "附加说明")

CANONICAL_TERM = "附加指令"


def _is_excluded(path: Path) -> bool:
    return any(path.is_relative_to(excluded) for excluded in EXCLUDED_DIRS)


def test_legacy_instruction_terms_are_absent_from_active_text() -> None:
    """扫到旧称即报文件与行号：命中处要么改用「附加指令」，要么换个不撞术语的说法。"""
    hits: list[str] = []
    for root, suffixes in SCAN_ROOTS.items():
        for path in (REPO / root).rglob("*"):
            if path.suffix not in suffixes or _is_excluded(path):
                continue
            if path == Path(__file__):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hits += [f"{path.relative_to(REPO)}:{lineno} 含「{term}」" for term in LEGACY_TERMS if term in line]

    assert not hits, f"活动文案须统一用「{CANONICAL_TERM}」，以下位置仍是旧称：\n" + "\n".join(hits)
