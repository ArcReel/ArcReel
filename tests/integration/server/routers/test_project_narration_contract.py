"""HTTP 与 Agent 项目配音改项使用相同的原子组状态转换。"""

import pytest

from lib.config.resolver import ConfigResolver
from lib.project.project_manager import ProjectManager
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool
from tests.integration.server.routers.projects_router_support import build_projects_client


@pytest.mark.parametrize("entry", ["http", "agent"])
async def test_project_narration_transitions(entry, tmp_path, monkeypatch, db_factory):
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", narration={"narration_delivery": "use_tts"})
    harness = ToolHarness("demo", tmp_path, pm, config_resolver=ConfigResolver(db_factory))
    model = {"audio_backend": "minimax/speech-2.8-hd"}
    group = {**model, "narration_voice": "cloned"}
    gemini = {"audio_backend": "gemini-aistudio/gemini-3.8-flash-tts"}
    first_voice = {**gemini, "narration_voice": "Zephyr"}
    steps = [
        ({"narration_voice": "solo"}, False, {}),
        ({"narration_speed": 1.2}, False, {}),
        (group, True, group),
        ({"narration_speed": 1.1}, True, {**group, "narration_speed": 1.1}),
        ({"narration_speed": None}, True, group),
        ({"narration_speed": 3}, False, group),
        ({"narration_speed": 1.2}, True, {**group, "narration_speed": 1.2}),
        (gemini, True, first_voice),
        ({"narration_voice": "cloned"}, False, first_voice),
        ({"audio_backend": "custom-1/no-suggestions"}, False, first_voice),
        ({"audio_backend": None, "narration_voice": "cloned"}, False, first_voice),
        ({"narration_delivery": "post_production"}, True, first_voice),
        ({"audio_backend": None}, True, {}),
        (group, True, group),
        ({"audio_backend": "", "narration_voice": "", "narration_speed": None}, True, {}),
    ]
    with build_projects_client(monkeypatch, pm, session_factory=db_factory) as client:
        for patch, accepted, expected in steps:
            if entry == "http":
                response = client.patch("/api/v1/projects/demo", json=patch)
                assert response.status_code == (200 if accepted else 422), (patch, response.text)
            else:
                result = await run_declared_tool("patch_project", harness, {"settings": patch})
                assert (result.problem is None) is accepted, (patch, result.problem)
            project = pm.load_project("demo")
            assert {
                key: project[key] for key in ("audio_backend", "narration_voice", "narration_speed") if key in project
            } == expected
            if "narration_delivery" in patch:
                assert project["narration_delivery"] == patch["narration_delivery"]
