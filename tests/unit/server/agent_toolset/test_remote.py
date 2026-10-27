"""远程 MCP adapter 的错误消息口径。"""

from __future__ import annotations

from pathlib import Path

from server.agent_toolset.remote import _project_relative_outcome, project_path_spellings, relativize_project_paths
from server.tool_runtime import ToolOutcome, ToolProblem


def test_relativize_rewrites_both_the_given_and_the_resolved_project_dir(tmp_path: Path) -> None:
    real = tmp_path / "real" / "projects" / "demo"
    real.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real", target_is_directory=True)
    project_dir = alias / "projects" / "demo"
    spellings = project_path_spellings(project_dir)

    text = (
        f"草稿：{project_dir}/drafts/episode_1/a.invalid.json；"
        f"正式：{real.resolve()}/scripts/episode_1.json；目录：{project_dir}。"
    )

    assert relativize_project_paths(text, spellings) == (
        "草稿：drafts/episode_1/a.invalid.json；正式：scripts/episode_1.json；目录：.。"
    )


def test_relativize_leaves_sibling_projects_with_a_shared_prefix_alone(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    sibling = f"{project_dir}-2/project.json"

    assert relativize_project_paths(sibling, (str(project_dir),)) == sibling


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
