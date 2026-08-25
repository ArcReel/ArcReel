from __future__ import annotations

import ast
import asyncio
import os
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from lib.async_thread import run_sync_transaction
from lib.project_manager import ProjectManager
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow_state import WorkflowStatus
from server import draft_workflow, tool_runtime
from server import text_generation as shared_text_generation
from server.agent_runtime.sdk_tools import text_generation as sdk_text_generation
from server.tool_runtime import (
    CallerContext,
    DraftLocator,
    PatchEpisodeMetaRequest,
    PatchEpisodeScriptRequest,
    ProjectScope,
    Services,
    ToolRequest,
    get_episode_script,
    get_project_content,
    get_source_text,
    get_step1_content,
    get_video_capabilities,
    get_workflow_plan,
    list_project_files,
    list_source_files,
    patch_episode_meta,
    patch_episode_script,
    read_project_file,
)


class _Projects:
    def __init__(self, project: dict):
        self.project = project
        self.load_script_threads: list[int] = []
        self.readonly_loads = 0

    def load_project(self, name: str) -> dict:
        assert name == "demo"
        return self.project

    def load_project_readonly(self, name: str) -> dict:
        assert name == "demo"
        self.readonly_loads += 1
        return self.project

    def load_script(self, name: str, script: str) -> dict:
        assert name == "demo"
        assert script == "episode_1.json"
        self.load_script_threads.append(threading.get_ident())
        return {"episode": 1, "segments": []}


class _Planner:
    def __init__(self, status: WorkflowStatus):
        self.status = status

    async def get_plan(
        self,
        project_name: str,
        request: WorkflowPlanRequest,
        *,
        user_id: str,
        queue=None,
        config_resolver=None,
    ):
        assert project_name == "demo"
        assert user_id == "u1"
        return build_workflow_plan(self.status, narration_delivery=request.narration_delivery)


class _Capabilities:
    async def video_capabilities_for_project(self, project: dict, *, capability=None) -> dict:
        assert project["generation_mode"] == "storyboard"
        assert capability is None
        return {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        imported
        for node in ast.walk(tree)
        for imported in (
            [node.module]
            if isinstance(node, ast.ImportFrom) and node.module
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
    }


def _status() -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "project_revision": "sha256-v1:project",
            "source_revision": None,
            "project": {"content_mode": "ad", "generation_mode": "storyboard", "grid_storyboard": False},
            "target": {
                "episode": 1,
                "script": "scripts/episode_1.json",
                "script_filename": "episode_1.json",
                "source": "source/episode_1.txt",
            },
            "state": "FINAL_SCRIPT",
            "blockers": [],
            "gates": {"step1_review": {"state": "not_applicable", "revision": None}},
            "artifacts": {
                "asset_inventory": {"state": "not_applicable"},
                "asset_sheets": {},
                "step1": {"state": "not_applicable"},
                "script": {"state": "missing"},
                "storyboards": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                "videos": {"current_ids": [], "stale_ids": [], "missing_ids": []},
                "audio": {"state": "not_applicable", "current_ids": [], "stale_ids": [], "missing_ids": []},
            },
            "next_action": {"type": "generate_script", "reason": "script missing"},
        }
    )


async def test_workflow_plan_returns_typed_domain_outcome() -> None:
    project = {"generation_mode": "storyboard"}
    outcome = await get_workflow_plan(
        ToolRequest(WorkflowPlanRequest(episode=1)),
        ProjectScope("demo", Path("/projects")),
        CallerContext(user_id="u1", source="embedded"),
        Services(projects=_Projects(project), workflow_planner=_Planner(_status()), capabilities=_Capabilities()),
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.status.target is not None
    assert outcome.value.status.target.episode == 1


async def test_video_capabilities_returns_typed_domain_outcome() -> None:
    project = {"generation_mode": "storyboard", "content_mode": "drama"}
    projects = _Projects(project)
    outcome = await get_video_capabilities(
        ToolRequest(None),
        ProjectScope("demo", Path("/projects")),
        CallerContext(user_id="u1", source="embedded"),
        Services(projects=projects, workflow_planner=_Planner(_status()), capabilities=_Capabilities()),
    )

    assert outcome.problem is None
    assert outcome.value == {"provider_id": "fake", "model": "video-1", "supported_durations": [4, 6]}
    assert projects.readonly_loads == 1


async def test_patch_episode_script_returns_typed_revision_conflict() -> None:
    project = {"generation_mode": "storyboard"}
    projects = _Projects(project)
    caller_thread = threading.get_ident()
    outcome = await patch_episode_script(
        ToolRequest(
            PatchEpisodeScriptRequest.model_validate(
                {
                    "script": "episode_1.json",
                    "base_revision": "sha256-v1:" + "0" * 64,
                    "operations": [{"op": "remove", "id": "E1S01"}],
                }
            )
        ),
        ProjectScope("demo", Path("/projects")),
        CallerContext(user_id="u1", source="embedded"),
        Services(projects=projects, workflow_planner=_Planner(_status()), capabilities=_Capabilities()),
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.problems[0].code == "revision_conflict"
    assert projects.load_script_threads
    assert all(thread != caller_thread for thread in projects.load_script_threads)


async def test_sync_transaction_finishes_worker_before_propagating_cancellation() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def transaction() -> None:
        started.set()
        release.wait()
        finished.set()

    task = asyncio.create_task(run_sync_transaction(transaction))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        task_results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)

    assert isinstance(task_results[0], asyncio.CancelledError)
    assert finished.is_set()


def test_text_generation_dependency_points_from_host_adapters_to_shared_handler() -> None:
    shared_path = Path(shared_text_generation.__file__)
    sdk_path = shared_path.parent / "agent_runtime" / "sdk_tools" / "text_generation.py"
    shared_imports = _imported_modules(shared_path)
    sdk_imports = _imported_modules(sdk_path)

    assert "claude_agent_sdk" not in shared_imports
    assert not any(module.startswith("server.agent_runtime.sdk_tools") for module in shared_imports)
    assert "server.tool_runtime" in sdk_imports
    assert "server.text_generation" in sdk_imports
    assert '"is_error"' not in shared_path.read_text(encoding="utf-8")


async def test_patch_episode_meta_returns_typed_domain_outcome(tmp_path: Path, monkeypatch) -> None:
    from lib.project_manager import ProjectManager

    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo")
    projects.create_project_metadata("demo", "Demo", "", "narration")
    projects.save_script("demo", {"title": "旧标题", "segments": []}, "episode_1.json", validate=False)
    services = Services(projects=projects, workflow_planner=_Planner(_status()), capabilities=_Capabilities())
    caller_thread = threading.get_ident()
    mutation_threads: list[int] = []
    original_locked_script = projects.locked_script

    @contextmanager
    def tracked_locked_script(project_name: str, filename: str):
        mutation_threads.append(threading.get_ident())
        with original_locked_script(project_name, filename) as script:
            yield script

    monkeypatch.setattr(projects, "locked_script", tracked_locked_script)

    outcome = await patch_episode_meta(
        ToolRequest(PatchEpisodeMetaRequest(script="episode_1.json", field="title", value=" 新标题 ")),
        ProjectScope("demo", projects.projects_root),
        CallerContext(user_id="u1", source="mcp"),
        services,
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.model_dump(mode="json") == {
        "message": "✅ 已更新分集title为「新标题」",
        "script": "episode_1.json",
        "field": "title",
        "value": "新标题",
    }
    assert projects.load_script("demo", "episode_1.json")["title"] == "新标题"
    assert mutation_threads
    assert all(thread != caller_thread for thread in mutation_threads)


async def test_content_readers_return_body_and_revision_from_the_same_snapshot(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "demo"
    (project_dir / "scripts").mkdir(parents=True)
    step1_dir = project_dir / "drafts" / "episode_1"
    step1_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        f'{{"content_mode":"drama","schema_version":{CURRENT_PROJECT_SCHEMA_VERSION}}}', encoding="utf-8"
    )
    (project_dir / "scripts" / "episode_1.json").write_text(
        '{"episode":1,"title":"第一集","scenes":[]}', encoding="utf-8"
    )
    (step1_dir / "step1_normalized_script.json").write_text('{"title":"第一集","scenes":[]}', encoding="utf-8")
    projects = ProjectManager(tmp_path)
    services = Services(projects=projects, workflow_planner=_Planner(_status()), capabilities=_Capabilities())
    scope = ProjectScope("demo", tmp_path)
    caller = CallerContext(user_id="u1", source="mcp")
    caller_thread = threading.get_ident()
    reader_threads: list[int] = []
    original_load_project_readonly = projects.load_project_readonly
    original_load_script_readonly = projects.load_script_readonly

    def tracked_load_project_readonly(project_name: str) -> dict:
        reader_threads.append(threading.get_ident())
        return original_load_project_readonly(project_name)

    def tracked_load_script_readonly(project_name: str, filename: str) -> dict:
        reader_threads.append(threading.get_ident())
        return original_load_script_readonly(project_name, filename)

    monkeypatch.setattr(projects, "load_project_readonly", tracked_load_project_readonly)
    monkeypatch.setattr(projects, "load_script_readonly", tracked_load_script_readonly)

    project = await get_project_content(ToolRequest(None), scope, caller, services)
    script = await get_episode_script(ToolRequest("episode_1.json"), scope, caller, services)
    step1 = await get_step1_content(ToolRequest(1), scope, caller, services)

    assert project.problem is None
    assert project.value is not None
    assert project.value.project == {"content_mode": "drama", "schema_version": CURRENT_PROJECT_SCHEMA_VERSION}
    assert project.value.revision.startswith("sha256-v1:")
    assert script.problem is None
    assert script.value is not None
    assert script.value.script["title"] == "第一集"
    assert script.value.revision.startswith("sha256-v1:")
    assert step1.problem is None
    assert step1.value is not None
    assert step1.value.content["title"] == "第一集"
    assert len(reader_threads) == 3
    assert all(thread != caller_thread for thread in reader_threads)


async def test_file_readers_share_a_business_file_allowlist_and_reject_symlinks(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    (project_dir / "source").mkdir(parents=True)
    (project_dir / "scripts").mkdir()
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (project_dir / "project.json").write_text('{"content_mode":"drama"}', encoding="utf-8")
    (project_dir / "source" / "novel.txt").write_text("原文", encoding="utf-8")
    (project_dir / "source" / "episode_1.txt").write_text("第一集原文", encoding="utf-8")
    (project_dir / "source" / "directory.txt").mkdir()
    (project_dir / "scripts" / "episode_1.json").write_text('{"episode":1,"scenes":[]}', encoding="utf-8")
    (drafts / "step1_normalized_script.json").write_text('{"title":"第一集","scenes":[]}', encoding="utf-8")
    (project_dir / ".env").write_text("SECRET=value", encoding="utf-8")
    (project_dir / "source" / "linked.txt").symlink_to(project_dir / ".env")
    projects = ProjectManager(tmp_path)
    services = Services(projects=projects, workflow_planner=_Planner(_status()), capabilities=_Capabilities())
    scope = ProjectScope("demo", tmp_path)
    caller = CallerContext(user_id="u1", source="mcp")
    sources = await list_source_files(ToolRequest(None), scope, caller, services)
    source = await get_source_text(ToolRequest("source/episode_1.txt"), scope, caller, services)
    step1 = await get_step1_content(ToolRequest(1), scope, caller, services)
    files = await list_project_files(ToolRequest(None), scope, caller, services)
    script = await read_project_file(ToolRequest("scripts/episode_1.json"), scope, caller, services)
    sensitive = await read_project_file(ToolRequest(".env"), scope, caller, services)
    linked = await read_project_file(ToolRequest("source/linked.txt"), scope, caller, services)
    nonregular = await read_project_file(ToolRequest("source/directory.txt"), scope, caller, services)
    traversal = await read_project_file(ToolRequest("../demo/project.json"), scope, caller, services)

    assert sources.problem is None
    assert sources.value is not None
    assert [entry.path for entry in sources.value.files] == ["source/episode_1.txt", "source/novel.txt"]
    assert source.value is not None and source.value.text == "第一集原文"
    assert step1.value is not None and step1.value.content["title"] == "第一集"
    assert files.value is not None
    assert {entry.path for entry in files.value.files} == {
        "project.json",
        "source/episode_1.txt",
        "source/novel.txt",
        "scripts/episode_1.json",
        "drafts/episode_1/step1_normalized_script.json",
    }
    assert script.value is not None and script.value.content["episode"] == 1
    assert script.value.etag.startswith("sha256-v1:")
    assert sensitive.problem is not None and sensitive.problem.code == "unsafe_path"
    assert linked.problem is not None and linked.problem.code == "unsafe_path"
    assert nonregular.problem is not None and nonregular.problem.code == "unsafe_path"
    assert traversal.problem is not None and traversal.problem.code == "unsafe_path"


async def test_project_file_read_holds_the_checked_file_snapshot(tmp_path: Path, monkeypatch) -> None:
    if os.open not in os.supports_dir_fd:
        pytest.skip("requires openat-style directory descriptors")
    project_dir = tmp_path / "demo"
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text("{}", encoding="utf-8")
    source = source_dir / "novel.txt"
    source.write_text("safe", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    original_fdopen = os.fdopen

    def _swap_after_open(fd, *args, **kwargs):
        source.unlink()
        source.symlink_to(outside)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(tool_runtime.os, "fdopen", _swap_after_open)
    services = Services(
        projects=ProjectManager(tmp_path), workflow_planner=_Planner(_status()), capabilities=_Capabilities()
    )

    outcome = await read_project_file(
        ToolRequest("source/novel.txt"),
        ProjectScope("demo", tmp_path),
        CallerContext(user_id="u1", source="mcp"),
        services,
    )

    assert outcome.problem is None
    assert outcome.value is not None and outcome.value.content == "safe"


async def test_project_file_read_rejects_oversized_regular_file(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text("{}", encoding="utf-8")
    with (source_dir / "novel.txt").open("wb") as handle:
        handle.seek(tool_runtime.BUSINESS_FILE_MAX_BYTES)
        handle.write(b"x")
    services = Services(
        projects=ProjectManager(tmp_path), workflow_planner=_Planner(_status()), capabilities=_Capabilities()
    )

    outcome = await read_project_file(
        ToolRequest("source/novel.txt"),
        ProjectScope("demo", tmp_path),
        CallerContext(user_id="u1", source="mcp"),
        services,
    )

    assert outcome.problem is not None and outcome.problem.code == "file_too_large"


@pytest.mark.parametrize("episode", [0, -1, True, 1.5, "1"])
def test_draft_locator_requires_a_strict_positive_episode(episode: object) -> None:
    with pytest.raises(ValueError):
        DraftLocator(episode=episode, doc_type="reference_step1")  # type: ignore[arg-type]


def test_draft_locator_rejects_unknown_document_types() -> None:
    with pytest.raises(ValueError):
        DraftLocator(episode=1, doc_type="unsupported")  # type: ignore[arg-type]


def test_draft_dependency_points_from_sdk_adapter_to_shared_workflow() -> None:
    def imports(module) -> set[str]:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        return {
            name
            for node in ast.walk(tree)
            for name in (
                [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
        }

    shared_imports = imports(draft_workflow)
    sdk_imports = imports(sdk_text_generation)
    shared_source = Path(draft_workflow.__file__).read_text(encoding="utf-8")

    assert "claude_agent_sdk" not in shared_imports
    assert not any(name.startswith("server.agent_runtime.sdk_tools") for name in shared_imports)
    assert "server.draft_workflow" in sdk_imports
    assert '"is_error"' not in shared_source
