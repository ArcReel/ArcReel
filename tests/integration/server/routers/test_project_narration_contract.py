"""HTTP 与 Agent 项目配音改项与生成提交的原子组契约。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.resolver import ConfigResolver
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import GenerationQueue
from lib.i18n import _ as i18n_message
from lib.project.project_manager import ProjectManager
from lib.speech.narration_config import NarrationConfigError
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import generate
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import bind_session_factory, use_generation_queue, use_project_manager
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
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.example.com/v1",
            api_key="sk-test",
            models=[
                {"model_id": "image", "display_name": "Image", "endpoint": "openai-images-generations"},
                {"model_id": "reader", "display_name": "Reader", "endpoint": "openai-tts"},
            ],
        )
        custom = provider.provider_id
        await session.commit()
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
        ({"audio_backend": f"{custom}/missing", "narration_voice": "alloy"}, False, first_voice),
        ({"audio_backend": f"{custom}/image", "narration_voice": "alloy"}, False, first_voice),
        ({"audio_backend": f"{custom}/reader"}, False, first_voice),
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


async def test_unlisted_builtin_tts_model_returns_catalog_error(tmp_path, monkeypatch, db_factory):
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", narration={"narration_delivery": "use_tts"})
    harness = ToolHarness("demo", tmp_path, pm, config_resolver=ConfigResolver(db_factory))
    unlisted = {"audio_backend": "dashscope/not-in-catalog", "narration_voice": "Cherry"}
    with build_projects_client(monkeypatch, pm, session_factory=db_factory) as client:
        created = client.post(
            "/api/v1/projects",
            json={
                "title": "Demo2",
                "content_mode": "narration",
                "generation_mode": "storyboard",
                "narration_delivery": "use_tts",
                **unlisted,
            },
        )
        assert created.status_code == 400, created.text
        assert created.json()["detail"] != i18n_message("project_config_invalid")
        assert "not-in-catalog" in created.json()["detail"]

        updated = client.patch("/api/v1/projects/demo", json=unlisted)
        assert updated.status_code == 400, updated.text
        assert "not-in-catalog" in updated.json()["detail"]

    agent_result = await run_declared_tool("patch_project", harness, {"settings": unlisted})
    assert agent_result.problem is not None
    assert agent_result.problem.code == "model_not_in_provider_catalog"


async def test_inheriting_project_with_discovered_custom_default_requires_voice(db_factory):
    resolver = ConfigResolver(db_factory)
    with pytest.raises(NarrationConfigError) as missing_model:
        await resolver.resolve_tts_synthesis_settings({"narration_delivery": "use_tts"})
    assert missing_model.value.code == "narration_tts_model_required"

    async with db_factory() as session:
        repo = CustomProviderRepository(session)
        await repo.create_provider(
            display_name="VoiceRelay",
            discovery_format="openai",
            base_url="https://relay.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "relay-tts",
                    "display_name": "Relay TTS",
                    "endpoint": "openai-tts",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        await session.commit()

    with pytest.raises(NarrationConfigError) as missing_voice:
        await resolver.resolve_tts_synthesis_settings({"narration_delivery": "use_tts"})
    assert missing_voice.value.code == "narration_tts_voice_required"


@pytest.mark.parametrize(
    ("override", "expected_code"),
    [
        (
            {"audio_backend": "minimax/speech-2.8-hd", "narration_voice": "male-qn-qingse", "narration_speed": 3.0},
            "narration_tts_speed_out_of_range",
        ),
        (
            {"audio_backend": "gemini-aistudio/gemini-3.8-flash-tts", "narration_voice": "not-a-gemini-voice"},
            "narration_tts_voice_invalid",
        ),
    ],
)
async def test_generation_submission_rejects_invalid_tts_profile_before_enqueue(
    override, expected_code, tmp_path, monkeypatch, db_factory
):
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", content_mode="narration", narration={"narration_delivery": "use_tts"})
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json"}],
            **override,
        ),
    )
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "夜色深沉。",
                    "characters_in_segment": [],
                    "video_prompt": None,
                    "generated_assets": {},
                }
            ],
        },
        "episode_1.json",
    )

    queue = GenerationQueue(session_factory=db_factory, project_manager=pm)
    bind_session_factory(monkeypatch, db_factory)
    use_project_manager(monkeypatch, pm)
    use_generation_queue(monkeypatch, queue)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/projects/demo/generate/tts/E1S01",
            json={"script_file": "episode_1.json"},
        )
    assert response.status_code == 422, response.text
    assert (await queue.list_tasks(project_name="demo"))["total"] == 0

    harness = ToolHarness("demo", tmp_path, pm, config_resolver=ConfigResolver(db_factory), queue=queue)
    agent_result = await run_declared_tool("generate_narration_audio", harness, {"script": "episode_1.json"})
    assert agent_result.problem is not None
    assert agent_result.problem.code == expected_code
    assert (await queue.list_tasks(project_name="demo"))["total"] == 0
