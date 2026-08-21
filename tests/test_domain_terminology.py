"""Guard the domain boundaries that are easy to blur in distributed prose."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PATHS = (
    REPO_ROOT / "CONTEXT.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "agent_runtime_profile",
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "lib",
    REPO_ROOT / "server",
    REPO_ROOT / "tests",
    REPO_ROOT / "website" / "docs",
)
TEXT_SUFFIXES = {".md", ".py", ".ts", ".tsx"}
HISTORICAL_PARTS = {
    ("docs", "adr"),
    ("docs", "research"),
    ("lib", "project_migrations"),
}


def _active_text_lines():
    """Yield current contract prose; omit decision history and legacy-shape migrations."""
    this_file = Path(__file__).resolve()
    for active_path in ACTIVE_PATHS:
        paths = (active_path,) if active_path.is_file() else active_path.rglob("*")
        for path in paths:
            if path == this_file or path.suffix not in TEXT_SUFFIXES:
                continue
            relative_parts = path.relative_to(REPO_ROOT).parts
            if any(
                relative_parts[: len(historical_parts)] == historical_parts for historical_parts in HISTORICAL_PARTS
            ):
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                yield path.relative_to(REPO_ROOT), line_number, line


def test_writing_syntax_names_only_inline_markers():
    """The draft envelope is a flat draft structure; only its text uses writing syntax."""
    forbidden_fragments = (
        "引用语法" + "扁平",
        "扁平" + "引用语法",
        "引用语法" + "结构",
        "flat_reference" + "_syntax",
    )
    violations = [
        f"{path}:{line_number}: {line.strip()}"
        for path, line_number, line in _active_text_lines()
        if any(fragment in line for fragment in forbidden_fragments)
    ]
    assert not violations, "引用语法越过 text 记号边界：\n" + "\n".join(violations)


def test_shot_machine_identifiers_are_described_as_storyboard_entries():
    """Machine identifiers stay shot-based; current Chinese prose calls their entries 分镜."""
    machine_identifier = r"(?:shots\[\]|shot_id|products_in_shot)"
    legacy_term = "镜" + "头"
    patterns = (
        re.compile(rf"{machine_identifier}.*{legacy_term}"),
        re.compile(rf"{legacy_term}.*{machine_identifier}"),
        re.compile(r"广告/短片" + legacy_term),
    )
    violations = [
        f"{path}:{line_number}: {line.strip()}"
        for path, line_number, line in _active_text_lines()
        if any(pattern.search(line) for pattern in patterns)
    ]
    assert not violations, "脚本条目的人类术语应为分镜：\n" + "\n".join(violations)


def test_drama_scene_machine_identifiers_are_described_as_storyboard_entries():
    """Drama scene identifiers name script entries, not registered scene assets."""
    machine_identifier = r"(?:scenes\[\]|scene_id|drama_scene)"
    asset_term = "场" + "景"
    patterns = (
        re.compile(rf"{machine_identifier}.*{asset_term}"),
        re.compile(rf"{asset_term}.*{machine_identifier}"),
    )
    violations = [
        f"{path}:{line_number}: {line.strip()}"
        for path, line_number, line in _active_text_lines()
        if any(pattern.search(line) for pattern in patterns)
    ]
    assert not violations, "drama 脚本条目应称分镜，场景保留给资产：\n" + "\n".join(violations)


def test_storyboard_entries_are_not_called_scenes_in_current_prose():
    """Natural-language aliases must not bypass the machine-identifier guard."""
    legacy_fragments = (
        "片段/" + "场景",
        "剧本" + "场景",
        "单" + "场景时长",
        "场景" + "内容",
        "场景" + "边界",
        "场景" + "级",
        "场景" + "时长",
        "场景" + "统计摘要",
        "按其中的" + "场景定位",
        "增删" + "场景",
        "每个" + "场景一条",
        "逐" + "场景",
    )
    legacy_patterns = (re.compile("每个" + "场景" + r"(?:都|宜|生成)"),)
    violations = [
        f"{path}:{line_number}: {line.strip()}"
        for path, line_number, line in _active_text_lines()
        if any(fragment in line for fragment in legacy_fragments)
        or any(pattern.search(line) for pattern in legacy_patterns)
    ]
    assert not violations, "脚本条目的自然语言术语应为分镜：\n" + "\n".join(violations)
