from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from lib.asset_rename import AssetRenameReport
from lib.episode_reset import EpisodeResetResult
from lib.project_manager import ProjectManager
from lib.project_migration_failure import MIGRATION_FAILURE_CODE, record_migration_failure
from server import tool_runtime as tool_runtime_module
from server.tool_runtime import (
    CallerContext,
    CreateProjectToolRequest,
    ProjectScope,
    RenameAssetRequest,
    ResetEpisodePlanningRequest,
    Services,
    ToolRequest,
    UploadSourceRequest,
    create_project,
    get_source_text,
    list_projects,
    list_source_files,
    rename_asset,
    reset_episode_planning,
    retry_project_migration,
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


async def test_create_project_rolls_back_when_metadata_initialization_fails(tmp_path: Path) -> None:
    class FailingMetadataProjectManager(ProjectManager):
        def create_project_metadata(self, *_args, **_kwargs):
            raise OSError("disk full")

    projects_root = tmp_path / "projects"
    services = Services(
        projects=FailingMetadataProjectManager(projects_root),
        workflow_planner=_Unused(),  # type: ignore[arg-type]
        capabilities=_Unused(),  # type: ignore[arg-type]
    )
    request = ToolRequest(
        CreateProjectToolRequest(
            name="demo",
            title="Demo",
            content_mode="narration",
            generation_mode="storyboard",
        )
    )

    failed = await create_project(request, CallerContext(user_id="test", source="mcp"), services)

    assert failed.problem is not None and failed.problem.code == "internal_error"
    assert not (projects_root / "demo").exists()
    ProjectManager(projects_root).create_project("demo")


async def test_create_project_settles_publication_before_propagating_cancellation(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingMetadataProjectManager(ProjectManager):
        def create_project_metadata(self, *args, **kwargs):
            started.set()
            release.wait()
            return super().create_project_metadata(*args, **kwargs)

    projects = BlockingMetadataProjectManager(tmp_path / "projects")
    services = Services(
        projects=projects,
        workflow_planner=_Unused(),  # type: ignore[arg-type]
        capabilities=_Unused(),  # type: ignore[arg-type]
    )
    caller = CallerContext(user_id="test", source="mcp")
    creation = asyncio.create_task(
        create_project(
            ToolRequest(CreateProjectToolRequest(name="demo", title="Demo")),
            caller,
            services,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        assert not projects.project_exists("demo")
        listed = await list_projects(ToolRequest(None), caller, services)
        assert listed.value == []
        creation.cancel()
        await asyncio.sleep(0)
        assert not creation.done()
    finally:
        release.set()
        creation_results = await asyncio.wait_for(asyncio.gather(creation, return_exceptions=True), timeout=1)

    assert isinstance(creation_results[0], asyncio.CancelledError)
    assert projects.project_exists("demo")


async def test_list_projects_does_not_persist_readonly_migrations(tmp_path: Path) -> None:
    services = _services(tmp_path)
    services.projects.create_project("demo")
    services.projects.create_project_metadata("demo", "Demo")
    project_file = services.projects.get_project_path("demo") / "project.json"
    legacy = json.loads(project_file.read_text(encoding="utf-8"))
    legacy["style"] = "Anime"
    legacy.pop("style_template_id", None)
    project_file.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = project_file.read_bytes()

    outcome = await list_projects(
        ToolRequest(None),
        CallerContext(user_id="test", source="mcp"),
        services,
    )

    assert outcome.problem is None
    assert outcome.value and outcome.value[0]["name"] == "demo"
    assert project_file.read_bytes() == before


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
    services.projects.create_project_metadata("demo", "Demo")
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
    services.projects.create_project_metadata("demo", "Demo")
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


async def test_upload_source_respects_migration_failure_gate(tmp_path: Path) -> None:
    services = _services(tmp_path)
    project_dir = services.projects.create_project("demo")
    record_migration_failure(project_dir, ValueError("repair required"), schema_version=7)

    outcome = await upload_source(
        ToolRequest(UploadSourceRequest(filename="novel.txt", content="hello")),
        ProjectScope(project_name="demo", projects_root=services.projects.projects_root),
        CallerContext(user_id="test", source="mcp"),
        services,
    )

    assert outcome.problem is not None
    assert outcome.problem.code == MIGRATION_FAILURE_CODE
    assert not (project_dir / "source" / "novel.txt").exists()


async def test_reset_episode_planning_settles_write_before_propagating_cancellation(tmp_path: Path) -> None:
    services = _services(tmp_path)
    services.projects.create_project("demo")
    services.projects.create_project_metadata("demo", "Demo")
    scope = ProjectScope(project_name="demo", projects_root=services.projects.projects_root)
    caller = CallerContext(user_id="test", source="mcp")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def resetter(_project_path: Path, *, from_episode: int, confirm_consumed: bool) -> EpisodeResetResult:
        started.set()
        release.wait()
        finished.set()
        return EpisodeResetResult([], [], [], [])

    task = asyncio.create_task(
        reset_episode_planning(
            ToolRequest(ResetEpisodePlanningRequest(from_episode=1)),
            scope,
            caller,
            services,
            resetter=resetter,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        task_results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)

    assert isinstance(task_results[0], asyncio.CancelledError)
    assert finished.is_set()


async def test_rename_asset_settles_write_before_propagating_cancellation(tmp_path: Path, monkeypatch) -> None:
    services = _services(tmp_path)
    services.projects.create_project("demo")
    services.projects.create_project_metadata("demo", "Demo")
    scope = ProjectScope(project_name="demo", projects_root=services.projects.projects_root)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_rename(_project: str, table: str, old_name: str, new_name: str) -> AssetRenameReport:
        started.set()
        release.wait()
        finished.set()
        return AssetRenameReport(table, old_name, new_name, 0, 0, 0, False)

    monkeypatch.setattr(services.projects, "rename_asset", blocking_rename)
    task = asyncio.create_task(
        rename_asset(
            ToolRequest(RenameAssetRequest(table="characters", old_name="A", new_name="B")),
            scope,
            CallerContext(user_id="test", source="mcp"),
            services,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        task_results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)

    assert isinstance(task_results[0], asyncio.CancelledError)
    assert finished.is_set()


async def test_retry_migration_settles_write_before_propagating_cancellation(tmp_path: Path, monkeypatch) -> None:
    services = _services(tmp_path)
    services.projects.create_project("demo")
    services.projects.create_project_metadata("demo", "Demo")
    scope = ProjectScope(project_name="demo", projects_root=services.projects.projects_root)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_migration(_project_dir: Path):
        started.set()
        release.wait()
        finished.set()
        return None

    monkeypatch.setattr(tool_runtime_module, "migrate_project_with_verdict", blocking_migration)
    task = asyncio.create_task(
        retry_project_migration(ToolRequest(None), scope, CallerContext(user_id="test", source="mcp"), services)
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        task_results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)

    assert isinstance(task_results[0], asyncio.CancelledError)
    assert finished.is_set()


def test_create_project_request_rejects_mode_specific_fields_before_writing(tmp_path: Path) -> None:
    services = _services(tmp_path)

    with pytest.raises(ValueError):
        CreateProjectToolRequest(name="demo", content_mode="narration", target_duration=30)

    assert services.projects.list_projects() == []
