"""远程 MCP adapter 的错误消息口径。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project.project_manager import ProjectManager
from server.agent_toolset.remote import _project_relative_outcome, resolve_project_scope
from server.tool_runtime import ToolOutcome, ToolProblem


def test_project_relative_outcome_rewrites_detail_and_action_only_for_problems(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    spellings = (str(project_dir),)
    failed = ToolOutcome(
        problem=ToolProblem("draft_invalid", f"见 {project_dir}/drafts/x.json", action=f"打开 {project_dir}/drafts")
    )
    succeeded = ToolOutcome(value={"message": f"{project_dir}/drafts/x.json"})

    rewritten = _project_relative_outcome(failed, spellings).problem

    assert rewritten == ToolProblem("draft_invalid", "见 drafts/x.json", action="打开 drafts")
    assert _project_relative_outcome(succeeded, spellings) is succeeded


def test_project_relative_outcome_rewrites_nested_params(tmp_path: Path) -> None:
    """迁移失败记录读不出时，OSError 文本带着记录的绝对路径进入 params.details[].violation。"""
    project_dir = tmp_path / "projects" / "demo"
    record = f"{project_dir}/.migration_failure.json"
    failed = ToolOutcome(
        problem=ToolProblem(
            "project_migration_failed",
            "项目数据升级失败",
            params={
                "details": [{"file": ".migration_failure.json", "violation": f"Permission denied: '{record}'"}],
                "episode": 1,
                "paths": (record,),
            },
        )
    )

    rewritten = _project_relative_outcome(failed, (str(project_dir),)).problem

    assert rewritten is not None
    assert rewritten.params == {
        "details": [{"file": ".migration_failure.json", "violation": "Permission denied: '.migration_failure.json'"}],
        "episode": 1,
        "paths": (".migration_failure.json",),
    }


@pytest.fixture(scope="module")
def project_locations(tmp_path_factory: pytest.TempPathFactory) -> ProjectManager:
    root = tmp_path_factory.mktemp("locations")
    projects = ProjectManager(root / "data")
    (projects.projects_dir / "empty").mkdir(parents=True)
    outside = root / "outside"
    outside.mkdir()
    (outside / "project.json").write_text("{}", encoding="utf-8")
    (projects.projects_dir / "escape").symlink_to(outside, target_is_directory=True)
    return projects


@pytest.mark.parametrize(
    ("project", "error"),
    [
        pytest.param(None, ValueError, id="missing"),
        pytest.param(7, ValueError, id="non-string"),
        pytest.param("", ValueError, id="empty-name"),
        pytest.param("   ", ValueError, id="whitespace"),
        ("../demo", ValueError),
        ("demo/..", ValueError),
        ("absent", FileNotFoundError),
        ("empty", FileNotFoundError),
        ("escape", ValueError),
    ],
)
def test_invalid_project_locations(project_locations: ProjectManager, project: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        resolve_project_scope(project, project_locations)
