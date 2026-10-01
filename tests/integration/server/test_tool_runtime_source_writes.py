"""Agent 写源文的服务命令：覆盖整本源文的文件与编辑源文，都按对齐重映射分集账本，有受影响的集时先确认。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.project.project_manager import ProjectManager
from server.tool_runtime import (
    CallerContext,
    EditSourceTextRequest,
    ProjectScope,
    Services,
    SourceReplacement,
    ToolRequest,
    UploadSourceRequest,
    edit_source_text,
    upload_source,
)

TEXT = "第一章。少年下山。\n第二章。城里起火。\n"
CH2 = TEXT.index("第二章")


class _Unused:
    pass


class _Queue:
    def __init__(self, busy: set[int] | None = None) -> None:
        self.busy = busy or set()

    async def list_tasks(self, *, project_name, status, page, page_size):
        del project_name, page, page_size
        items = [{"resource_id": "script_plan", "script_file": None, "payload": {"episode": e}} for e in self.busy]
        return {"items": items if status == "running" else []}


def _project(tmp_path: Path, *, busy: set[int] | None = None) -> tuple[Services, ProjectScope, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    project_dir = pm.get_project_path("demo")
    (project_dir / "source" / "a.txt").write_text(TEXT, encoding="utf-8")
    (project_dir / "source" / "episode_3.txt").write_text("番外原文。\n", encoding="utf-8")

    def cut(episode: int, start: int, end: int) -> dict[str, Any]:
        return {
            "episode": episode,
            "title": f"集{episode}",
            "script_file": f"scripts/episode_{episode}.json",
            "source_origin": "whole_source",
            "source_range": {"source_file": "source/a.txt", "start": start, "end": end},
        }

    own = {"episode": 3, "title": "番外", "script_file": "scripts/episode_3.json", "source_origin": "own"}
    pm.update_project(
        "demo",
        lambda project: project.update(
            whole_source_files=[{"source_file": "source/a.txt"}],
            episodes=[cut(1, 0, CH2), cut(2, CH2, len(TEXT)), own],
            episode_id_high_water=3,
        ),
    )
    services = Services(projects=pm, workflow_planner=_Unused(), capabilities=_Unused(), queue=_Queue(busy))
    return services, ProjectScope(project_name="demo", data_root=pm.data_root), project_dir


_CALLER = CallerContext(user_id="test", source="embedded")


async def _edit(services: Services, scope: ProjectScope, **fields: Any):
    return await edit_source_text(ToolRequest(EditSourceTextRequest(**fields)), scope, _CALLER, services)


async def _upload(services: Services, scope: ProjectScope, **fields: Any):
    return await upload_source(ToolRequest(UploadSourceRequest(**fields)), scope, _CALLER, services)


def _episode(services: Services, episode: int) -> dict[str, Any]:
    return next(e for e in services.projects.load_project("demo")["episodes"] if e["episode"] == episode)


async def test_editing_a_whole_source_file_lists_affected_episodes_before_writing(tmp_path: Path) -> None:
    services, scope, project_dir = _project(tmp_path)
    replacements = [SourceReplacement(old_text="少年下山", new_text="少年走下山")]

    asked = await _edit(services, scope, filename="a.txt", replacements=replacements)

    assert asked.value is not None
    assert asked.value.confirmation_required is True
    assert (asked.value.impact["changed_without_products"], asked.value.impact["shifted"]) == ([1], [2])
    assert "集1" in asked.value.message
    assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == TEXT

    applied = await _edit(services, scope, filename="a.txt", replacements=replacements, revision=asked.value.revision)

    assert applied.value is not None
    assert applied.value.confirmation_required is False
    assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == TEXT.replace("少年下山", "少年走下山")
    assert _episode(services, 1)["ledger_status"] == "stale"
    assert _episode(services, 2)["source_range"]["start"] == CH2 + 1


async def test_a_replacement_must_match_exactly_once(tmp_path: Path) -> None:
    services, scope, project_dir = _project(tmp_path)

    missing = await _edit(
        services, scope, filename="a.txt", replacements=[SourceReplacement(old_text="不存在", new_text="x")]
    )
    ambiguous = await _edit(
        services, scope, filename="a.txt", replacements=[SourceReplacement(old_text="章。", new_text="章：")]
    )

    for outcome in (missing, ambiguous):
        assert outcome.problem is not None
        assert outcome.problem.code == "invalid_request"
    assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == TEXT


async def test_an_own_source_episode_is_rewritten_but_a_cut_episode_is_refused(tmp_path: Path) -> None:
    services, scope, project_dir = _project(tmp_path)

    own = await _edit(services, scope, episode_id=3, text="改过的番外。\n")
    cut = await _edit(services, scope, episode_id=1, text="改写切出集。\n")

    assert own.value is not None
    assert own.value.confirmation_required is False
    assert (project_dir / "source" / "episode_3.txt").read_text(encoding="utf-8") == "改过的番外。\n"
    assert cut.problem is not None
    assert cut.problem.code == "episode_source_derived"


async def test_replacing_a_registered_file_goes_through_the_same_confirmation(tmp_path: Path) -> None:
    services, scope, project_dir = _project(tmp_path)
    new_text = "第一章。少年下山。\n"

    asked = await _upload(services, scope, filename="a.txt", content=new_text, on_conflict="replace")

    assert asked.value is not None
    assert asked.value["confirmation_required"] is True
    assert asked.value["impact"]["removed"] == [2]
    assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == TEXT

    applied = await _upload(
        services, scope, filename="a.txt", content=new_text, on_conflict="replace", revision=asked.value["revision"]
    )

    assert applied.value is not None
    assert applied.value["confirmation_required"] is False
    assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == new_text
    assert [e["episode"] for e in services.projects.load_project("demo")["episodes"]] == [1, 3]


async def test_a_change_is_refused_while_a_displaced_episode_has_active_tasks(tmp_path: Path) -> None:
    services, scope, project_dir = _project(tmp_path, busy={2})
    new_text = "第一章。少年下山。\n"

    asked = await _upload(services, scope, filename="a.txt", content=new_text, on_conflict="replace")
    assert asked.value is not None
    refused = await _upload(
        services, scope, filename="a.txt", content=new_text, on_conflict="replace", revision=asked.value["revision"]
    )

    assert refused.problem is not None
    assert refused.problem.code == "source_file_change_tasks_active"
    assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == TEXT
