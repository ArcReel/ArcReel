"""Terminology drift defense: active Chinese copy calls `instructions` only “附加指令”.

Previously tool schemas, prompt headings, and subagent guidance used three names. Models infer
argument semantics from copy, so inconsistent names make main/subagents interpret one input
differently. Scan active backend/profile/frontend source and tests only; historical CHANGELOG,
docs/adr, and skill-optimization-workspace research retain original terminology and are excluded.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Active-copy roots and suffixes scanned under each.
SCAN_ROOTS: dict[str, frozenset[str]] = {
    "lib": frozenset({".py"}),
    "server": frozenset({".py"}),
    "scripts": frozenset({".py"}),
    "tests": frozenset({".py"}),
    "agent_runtime_profile": frozenset({".md"}),
    "frontend/src": frozenset({".ts", ".tsx"}),
}

#: Historical archives: rewriting old terminology would alter historical records.
EXCLUDED_DIRS = (REPO / "agent_runtime_profile" / "skill-optimization-workspace",)

LEGACY_TERMS = ("用户意见", "附加说明", "常驻偏好")

CANONICAL_TERM = "附加指令"


def _is_excluded(path: Path) -> bool:
    return any(path.is_relative_to(excluded) for excluded in EXCLUDED_DIRS)


def test_legacy_instruction_terms_are_absent_from_active_text() -> None:
    """Report file/line for old labels; replace them with “附加指令” or unambiguous nonterm wording."""
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
