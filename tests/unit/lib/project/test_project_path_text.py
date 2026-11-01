"""项目目录绝对路径改写为项目内相对路径的拼写覆盖。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from lib.project.project_path_text import project_path_spellings, relativize_project_paths


def test_relativize_rewrites_both_the_given_and_the_resolved_project_dir(tmp_path: Path) -> None:
    real = tmp_path / "real" / "projects" / "demo"
    real.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real", target_is_directory=True)
    project_dir = alias / "projects" / "demo"
    spellings = project_path_spellings(project_dir)

    text = (
        f"草稿：{project_dir}/drafts/episode_1/a.invalid.json；"
        f"正式：{real.resolve()}/scripts/episode_1.json；目录：{project_dir}。无法读取{project_dir}/project.json"
    )

    assert relativize_project_paths(text, spellings) == (
        "草稿：drafts/episode_1/a.invalid.json；正式：scripts/episode_1.json；目录：.。无法读取project.json"
    )


def test_relativize_leaves_sibling_projects_with_a_shared_prefix_alone(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    sibling = f"{project_dir}-2/project.json"

    assert relativize_project_paths(sibling, (str(project_dir),)) == sibling


def test_relativize_rewrites_windows_paths_quoted_by_os_errors() -> None:
    """OSError 按 repr 引用文件名，Windows 路径的反斜杠成对出现；改写后仍是可定位的项目内相对路径。"""
    project_dir = PureWindowsPath(r"C:\data\projects\demo")
    script = project_dir / "scripts" / "episode_1.json"
    text = str(FileNotFoundError(2, "No such file or directory", str(script)))

    rewritten = relativize_project_paths(text, project_path_spellings(project_dir))

    assert rewritten == str(FileNotFoundError(2, "No such file or directory", r"scripts\episode_1.json"))
    assert "C:" not in rewritten


def test_relativize_rewrites_plain_and_forward_slash_windows_paths() -> None:
    project_dir = PureWindowsPath(r"C:\data\projects\demo")
    spellings = project_path_spellings(project_dir)
    text = rf"原样：{project_dir}\scripts\episode_1.json；正斜杠：C:/data/projects/demo/drafts/a.json；目录：{project_dir}。"

    assert (
        relativize_project_paths(text, spellings) == r"原样：scripts\episode_1.json；正斜杠：drafts/a.json；目录：.。"
    )


def test_relativize_leaves_escaped_sibling_projects_with_a_shared_prefix_alone() -> None:
    project_dir = PureWindowsPath(r"C:\data\projects\demo")
    sibling = str(FileNotFoundError(2, "No such file or directory", r"C:\data\projects\demo-2\project.json"))

    assert relativize_project_paths(sibling, project_path_spellings(project_dir)) == sibling


def test_relativize_rewrites_paths_whose_repr_escapes_single_quotes() -> None:
    """文件名同时含两种引号时，repr 改用单引号包裹并转义项目目录里的单引号。"""
    project_dir = PurePosixPath("/data/o'brien/projects/demo")
    text = str(PermissionError(13, "Permission denied", f'{project_dir}/scripts/x"y.json'))

    rewritten = relativize_project_paths(text, project_path_spellings(project_dir))

    assert rewritten == str(PermissionError(13, "Permission denied", 'scripts/x"y.json'))


def test_relativize_leaves_paths_that_only_embed_the_project_dir_alone(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    embedded = f"绑定：/mnt{project_dir}/config.json；相对：backup{project_dir}/project.json"

    assert relativize_project_paths(embedded, project_path_spellings(project_dir)) == embedded
