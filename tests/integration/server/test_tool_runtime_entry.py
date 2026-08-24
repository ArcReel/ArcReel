from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from server.tool_runtime import (
    CallerContext,
    CreateProjectToolRequest,
    ProjectScope,
    Services,
    ToolRequest,
    UploadSourceRequest,
    create_project,
    get_source_text,
    list_projects,
    list_source_files,
    upload_source,
)


class _Unused:
    pass


def _services(tmp_path: Path) -> Services:
    return Services(
        projects=ProjectManager(tmp_path / "projects"),
        workflow_planner=_Unused(),  # type: ignore[arg-type]
        capabilities=_Unused(),  # type: ignore[arg-type]
    )


async def test_entry_handlers_create_list_and_upload_a_readable_source(tmp_path: Path) -> None:
    services = _services(tmp_path)
    caller = CallerContext(user_id="test", source="mcp")

    created = await create_project(
        ToolRequest(
            CreateProjectToolRequest(
                name="demo",
                title="Demo",
                content_mode="narration",
                generation_mode="storyboard",
            )
        ),
        caller,
        services,
    )
    projects = await list_projects(ToolRequest(None), caller, services)
    scope = ProjectScope(project_name="demo", projects_root=services.projects.projects_root)
    uploaded = await upload_source(
        ToolRequest(UploadSourceRequest(filename="novel.txt", content="第一章\n你好")),
        scope,
        caller,
        services,
    )
    source_files = await list_source_files(ToolRequest(None), scope, caller, services)
    source_text = await get_source_text(ToolRequest("source/novel.txt"), scope, caller, services)

    assert created.problem is None
    assert created.value is not None
    assert created.value["name"] == "demo"
    assert projects.value == [
        {
            "name": "demo",
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "storyboard",
        }
    ]
    assert uploaded.value == {
        "filename": "novel.txt",
        "path": "source/novel.txt",
        "original_filename": "novel.txt",
        "original_kept": False,
        "used_encoding": "utf-8",
        "chapter_count": 0,
    }
    assert source_files.value is not None
    assert [entry.path for entry in source_files.value.files] == ["source/novel.txt"]
    assert source_text.value is not None
    assert source_text.value.path == "source/novel.txt"
    assert source_text.value.text == "第一章\n你好"


async def test_entry_handlers_return_typed_problems(tmp_path: Path) -> None:
    services = _services(tmp_path)
    caller = CallerContext(user_id="test", source="embedded")

    missing = await upload_source(
        ToolRequest(UploadSourceRequest(filename="novel.txt", content="hello")),
        ProjectScope(project_name="missing", projects_root=services.projects.projects_root),
        caller,
        services,
    )

    assert missing.problem is not None
    assert missing.problem.code == "project_not_found"


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("../novel.txt", "invalid_request"),
        (".secret.txt", "invalid_request"),
        ("novel.pdf", "unsupported_format"),
    ],
)
async def test_upload_source_rejects_unsafe_or_non_text_filenames(
    tmp_path: Path, filename: str, expected_code: str
) -> None:
    services = _services(tmp_path)
    services.projects.create_project("demo")
    caller = CallerContext(user_id="test", source="mcp")

    outcome = await upload_source(
        ToolRequest(UploadSourceRequest(filename=filename, content="hello")),
        ProjectScope(project_name="demo", projects_root=services.projects.projects_root),
        caller,
        services,
    )

    assert outcome.problem is not None
    assert outcome.problem.code == expected_code


async def test_upload_source_rejects_symlinked_source_directory(tmp_path: Path) -> None:
    services = _services(tmp_path)
    project_dir = services.projects.create_project("demo")
    source_dir = project_dir / "source"
    source_dir.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    source_dir.symlink_to(outside, target_is_directory=True)

    outcome = await upload_source(
        ToolRequest(UploadSourceRequest(filename="novel.txt", content="hello")),
        ProjectScope(project_name="demo", projects_root=services.projects.projects_root),
        CallerContext(user_id="test", source="mcp"),
        services,
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"
    assert not (outside / "novel.txt").exists()


def test_create_project_request_rejects_mode_specific_fields_before_writing(tmp_path: Path) -> None:
    services = _services(tmp_path)

    with pytest.raises(ValueError):
        CreateProjectToolRequest(name="demo", content_mode="narration", target_duration=30)

    assert services.projects.list_projects() == []
