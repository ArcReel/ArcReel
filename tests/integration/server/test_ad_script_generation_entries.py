"""广告/短片「AI 生成脚本」的服务入口：整份重做先确认丢失清单，违约让任务失败，回执列出新增资产。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import decode_generation_problem
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_state import WorkflowStateService
from server.services.project.workflow_planner import WorkflowPlanner
from server.text_generation import TextGenerationRequest
from server.tool_runtime import (
    CallerContext,
    GenerateEpisodeScriptRequest,
    ProjectScope,
    Services,
    ToolRequest,
    execute_queued_text_task,
    generate_episode_script,
)

pytestmark = pytest.mark.usefixtures("video_request_facts")

_CALLER = CallerContext(user_id="u1", source="mcp")


def _project(tmp_path: Path, **fields: Any) -> ProjectManager:
    projects = ProjectManager(tmp_path / "projects")
    projects.create_project("demo", content_mode="ad")
    projects.create_project_metadata("demo", "Demo", "", "ad")
    projects.update_project("demo", lambda project: project.update(fields))
    return projects


def _services(projects: ProjectManager, db_factory) -> Services:
    return Services(
        projects=projects,
        workflow_planner=WorkflowPlanner(projects),
        capabilities=ConfigResolver(async_session_factory),
        queue=GenerationQueue(session_factory=db_factory, project_manager=projects),
    )


def _shot(shot_id: str, *, characters: list[str] | None = None) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "section": "opening",
        "duration_seconds": 4,
        "voiceover_text": "",
        "characters_in_shot": characters or [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        "image_prompt": {
            "scene": "雨夜街角，路灯下的积水映出霓虹",
            "composition": {"shot_type": "Medium Shot", "lighting": "冷色路灯", "ambiance": "潮湿"},
        },
        "video_prompt": {
            "action": "雨水顺着伞沿滴落，两人并肩走过街角",
            "camera_motion": "Tracking Shot",
            "ambiance_audio": "雨声",
            "dialogue": [],
        },
    }


def _save_formal_script(projects: ProjectManager) -> None:
    projects.save_script(
        "demo",
        {"episode": 1, "title": "共伞", "content_mode": "ad", "shots": [_shot("E1S01"), _shot("E1S02")]},
        "episode_1.json",
    )


def _text_model_returning(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]) -> None:
    from lib.script import script_generator

    class _Generator:
        model = "fake-text"

        async def generate(self, _request, project_name=None):
            class _Result:
                text = json.dumps(response, ensure_ascii=False)

            return _Result()

    async def create(_task_type, project_name=None, **_kwargs):
        return _Generator()

    monkeypatch.setattr(script_generator.TextGenerator, "create", create)


def _queued_task(**request: Any) -> dict[str, Any]:
    return {
        "task_id": "task-1",
        "project_name": "demo",
        "task_type": "text_episode_script",
        "payload": TextGenerationRequest(episode=1, **request).to_payload(),
    }


async def test_regenerating_an_existing_script_returns_the_loss_list_first(tmp_path: Path, db_factory) -> None:
    projects = _project(tmp_path, brief="雨夜里两个陌生人共用一把伞")
    _save_formal_script(projects)

    outcome = await generate_episode_script(
        ToolRequest(GenerateEpisodeScriptRequest(episode_id=1, regenerate=True)),
        ProjectScope("demo", projects.projects_dir),
        _CALLER,
        _services(projects, db_factory),
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "script_overwrite_required"
    overwrite = outcome.problem.params["script_overwrite"]
    assert [entry["id"] for entry in overwrite["entries"]] == ["E1S01", "E1S02"]
    assert overwrite["revision"]
    assert overwrite["text"]


async def test_regenerating_without_a_brief_or_products_is_refused_with_the_status_reason(
    tmp_path: Path, db_factory
) -> None:
    projects = _project(tmp_path)
    _save_formal_script(projects)

    outcome = await generate_episode_script(
        ToolRequest(GenerateEpisodeScriptRequest(episode_id=1, regenerate=True)),
        ProjectScope("demo", projects.projects_dir),
        _CALLER,
        _services(projects, db_factory),
    )

    status = WorkflowStateService(projects).get_status("demo", 1)
    assert outcome.problem is not None
    assert outcome.problem.code == "operation_not_admitted"
    assert outcome.problem.params["reason"] == status.operations["generate_script"].reason
    assert status.operations["generate_script"].reason == "ad_brief_and_products_missing"


def test_regenerating_cannot_be_combined_with_authoring_scope() -> None:
    with pytest.raises(ValueError, match="regenerate"):
        GenerateEpisodeScriptRequest(episode_id=1, regenerate=True, entry_ids=["E1S01"])


async def test_a_rejected_generation_fails_the_task_and_writes_nothing(
    tmp_path: Path, db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = _project(tmp_path, brief="雨夜里两个陌生人共用一把伞")
    project_path = projects.get_project_path("demo")
    before = (project_path / "project.json").read_bytes()
    drafts_before = sorted((project_path / "drafts").rglob("*"))
    _text_model_returning(monkeypatch, {"title": "共伞", "shots": [_shot("E1S01", characters=["阿杰"])]})

    with pytest.raises(RuntimeError) as caught:
        await execute_queued_text_task(_queued_task(), services=_services(projects, db_factory))

    problem = decode_generation_problem(str(caught.value))
    assert problem is not None
    assert problem.code == "ad_script_rejected"
    assert "阿杰" in problem.params["details"]
    assert not (project_path / "scripts" / "episode_1.json").exists()
    assert sorted((project_path / "drafts").rglob("*")) == drafts_before
    assert (project_path / "project.json").read_bytes() == before


async def test_the_task_result_lists_the_registered_new_assets(
    tmp_path: Path, db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = _project(tmp_path, brief="雨夜里两个陌生人共用一把伞")
    _text_model_returning(
        monkeypatch,
        {
            "title": "共伞",
            "shots": [_shot("E1S01", characters=["阿杰"])],
            "new_assets": [
                {
                    "type": "character",
                    "name": "阿杰",
                    "decision": "register",
                    "reason": "第一次出现",
                    "description": "高个青年，黑色风衣",
                }
            ],
        },
    )

    result = await execute_queued_text_task(_queued_task(), services=_services(projects, db_factory))

    assert result["new_assets"] == [{"type": "character", "name": "阿杰"}]
    assert "阿杰" in result["message"]
    assert "阿杰" in projects.load_project("demo")["characters"]
