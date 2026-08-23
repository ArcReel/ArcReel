from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.test_changed import build_plan, collect_changed_paths, frontend_commands

pytestmark = pytest.mark.unit


def _write(root: Path, relative: str, content: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_changed_test_file_is_selected_directly(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_widget.py", "def test_widget(): assert True\n")

    plan = build_plan(tmp_path, ["tests/test_widget.py"], "main")

    assert plan.backend.mode == "related"
    assert plan.backend.tests == ["tests/test_widget.py"]


def test_python_import_graph_selects_transitive_test(tmp_path: Path) -> None:
    _write(tmp_path, "lib/core.py", "VALUE = 1\n")
    _write(tmp_path, "server/service.py", "from lib.core import VALUE\n")
    _write(tmp_path, "tests/test_service.py", "from server.service import VALUE\n\ndef test_value(): assert VALUE\n")

    plan = build_plan(tmp_path, ["lib/core.py"], "main")

    assert plan.backend.mode == "related"
    assert plan.backend.tests == ["tests/test_service.py"]


def test_relative_import_graph_selects_dependent_test(tmp_path: Path) -> None:
    _write(tmp_path, "lib/pkg/__init__.py")
    _write(tmp_path, "lib/pkg/core.py", "VALUE = 1\n")
    _write(tmp_path, "lib/pkg/service.py", "from .core import VALUE\n")
    _write(
        tmp_path, "tests/test_pkg_service.py", "from lib.pkg.service import VALUE\n\ndef test_value(): assert VALUE\n"
    )

    plan = build_plan(tmp_path, ["lib/pkg/core.py"], "main")

    assert plan.backend.mode == "related"
    assert plan.backend.tests == ["tests/test_pkg_service.py"]


def test_uncovered_python_change_escalates_to_full_backend(tmp_path: Path) -> None:
    _write(tmp_path, "lib/orphan_module.py", "VALUE = 1\n")

    plan = build_plan(tmp_path, ["lib/orphan_module.py"], "main")

    assert plan.backend.mode == "full"
    assert "没有可证明相关的测试" in plan.backend.reasons[0]


@pytest.mark.parametrize("path", ["pyproject.toml", "uv.lock", "tests/conftest.py", "tests/fakes.py"])
def test_shared_backend_infrastructure_escalates_to_full(tmp_path: Path, path: str) -> None:
    _write(tmp_path, path)

    plan = build_plan(tmp_path, [path], "main")

    assert plan.backend.mode == "full"


def test_deleted_python_module_escalates_to_full_backend(tmp_path: Path) -> None:
    plan = build_plan(tmp_path, ["lib/deleted.py"], "main")

    assert plan.backend.mode == "full"
    assert "被删除" in plan.backend.reasons[0]


def test_frontend_source_uses_related_mode(tmp_path: Path) -> None:
    _write(tmp_path, "frontend/src/components/Card.tsx", "export const Card = () => null;\n")

    plan = build_plan(tmp_path, ["frontend/src/components/Card.tsx"], "main")

    assert plan.frontend.mode == "related"
    assert plan.frontend.sources == ["src/components/Card.tsx"]

    commands = frontend_commands(plan, tmp_path / "frontend")
    assert commands == [
        [
            "pnpm",
            "exec",
            "vitest",
            "related",
            "--run",
            "--passWithNoTests",
            str((tmp_path / "frontend/src/components/Card.tsx").resolve()),
        ]
    ]


def test_frontend_test_is_selected_directly(tmp_path: Path) -> None:
    _write(tmp_path, "frontend/src/components/Card.test.tsx", "test('card', () => {});\n")

    plan = build_plan(tmp_path, ["frontend/src/components/Card.test.tsx"], "main")

    assert plan.frontend.mode == "related"
    assert plan.frontend.tests == ["src/components/Card.test.tsx"]


def test_frontend_test_infrastructure_escalates_to_full(tmp_path: Path) -> None:
    _write(tmp_path, "frontend/src/test/setup.ts")

    plan = build_plan(tmp_path, ["frontend/src/test/setup.ts"], "main")

    assert plan.frontend.mode == "full"


def test_frontend_i18n_adds_backend_contract_tests(tmp_path: Path) -> None:
    _write(tmp_path, "frontend/src/i18n/zh/dashboard.ts")
    for test in (
        "tests/test_frontend_mcp_tool_i18n.py",
        "tests/test_frontend_skill_i18n.py",
        "tests/test_frontend_task_type_i18n.py",
        "tests/test_i18n_consistency.py",
    ):
        _write(tmp_path, test, "def test_contract(): assert True\n")

    plan = build_plan(tmp_path, ["frontend/src/i18n/zh/dashboard.ts"], "main")

    assert plan.backend.mode == "related"
    assert set(plan.backend.tests) == {
        "tests/test_frontend_mcp_tool_i18n.py",
        "tests/test_frontend_skill_i18n.py",
        "tests/test_frontend_task_type_i18n.py",
        "tests/test_i18n_consistency.py",
    }


def test_frontend_workflow_type_adds_backend_contract_test(tmp_path: Path) -> None:
    _write(tmp_path, "frontend/src/types/workflow.ts")
    _write(tmp_path, "tests/test_workflow_action_types.py", "def test_contract(): assert True\n")

    plan = build_plan(tmp_path, ["frontend/src/types/workflow.ts"], "main")

    assert plan.backend.mode == "related"
    assert plan.backend.tests == ["tests/test_workflow_action_types.py"]


def test_profile_change_selects_explicit_contract_tests(tmp_path: Path) -> None:
    _write(tmp_path, "agent_runtime_profile/.claude/agents/example.md")
    _write(tmp_path, "tests/test_agent_profile_lint.py", "def test_profile(): assert True\n")
    _write(tmp_path, "tests/test_profile_manifest.py", "def test_manifest(): assert True\n")

    plan = build_plan(tmp_path, ["agent_runtime_profile/.claude/agents/example.md"], "main")

    assert plan.backend.mode == "related"
    assert plan.backend.tests == ["tests/test_agent_profile_lint.py", "tests/test_profile_manifest.py"]


def test_document_only_change_runs_related_consistency_checks(tmp_path: Path) -> None:
    _write(tmp_path, "website/docs/guide.md")

    plan = build_plan(tmp_path, ["website/docs/guide.md"], "main")

    assert plan.website.mode == "related"
    assert "库存" in plan.website.reasons[0]


def test_git_change_collection_merges_branch_and_worktree_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    _write(tmp_path, "base.py", "BASE = 1\n")
    subprocess.run(["git", "add", "base.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "feature"], cwd=tmp_path, check=True, capture_output=True)
    _write(tmp_path, "committed.py", "COMMITTED = 1\n")
    subprocess.run(["git", "add", "committed.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=tmp_path, check=True, capture_output=True)
    _write(tmp_path, "base.py", "BASE = 2\n")
    _write(tmp_path, "staged.py", "STAGED = 1\n")
    subprocess.run(["git", "add", "staged.py"], cwd=tmp_path, check=True)
    _write(tmp_path, "untracked.py", "UNTRACKED = 1\n")

    changed = collect_changed_paths(tmp_path, "main")

    assert {"base.py", "committed.py", "staged.py", "untracked.py"} <= set(changed)
