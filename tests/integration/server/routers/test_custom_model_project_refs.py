"""自定义供应商改模型 ID / 删除模型 / 删除供应商时，项目级与全局的模型引用同步。

改 ID 是同一个模型换了标识：项目的全部 10 个引用键、``model_settings`` 与全局设置都改写成新 ID。
删除按 docs/adr/0054 不清理视频桶键，但被删模型的 ``model_settings`` 一定清掉，
否则之后同 ID 的新模型会接管这些引用。
"""

from __future__ import annotations

import io
import wave
from collections.abc import Generator

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.artifacts.version_manager import VersionManager
from lib.config.model_setting_keys import GLOBAL_MODEL_SETTING_KEYS, PROJECT_MODEL_SETTING_KEYS
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.config.tts import patch_global_tts
from lib.db import get_async_session
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.project.project_manager import ProjectManager
from server.routers import custom_providers, provider_models
from server.services.tasks.generation_tasks import execute_tts_task
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import bind_session_factory, use_project_manager

VIDEO_BUCKET_KEYS = ("video_provider_i2v", "video_provider_r2v")
CLEARED_ON_DELETE = tuple(k for k in PROJECT_MODEL_SETTING_KEYS if k not in VIDEO_BUCKET_KEYS)


@pytest.fixture
def model_refs_client(db_factory: async_sessionmaker[AsyncSession]) -> Generator[TestClient]:
    app = FastAPI()

    async def _override_session():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    override_auth(app)
    app.include_router(provider_models.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(custom_providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def model_refs_pm(tmp_path, monkeypatch) -> ProjectManager:
    manager = ProjectManager(tmp_path)
    use_project_manager(monkeypatch, manager)
    return manager


def _model(model_id: str, *, default: bool = False) -> dict:
    return {
        "model_id": model_id,
        "display_name": model_id,
        "endpoint": "openai-chat",
        "is_default": default,
        "is_enabled": True,
    }


def _project(model_refs_pm: ProjectManager, name: str, ref: str) -> None:
    model_refs_pm.create_project(name)
    model_refs_pm.create_project_metadata(name, name, "Anime", "narration")

    def _mutate(p: dict) -> None:
        for key in PROJECT_MODEL_SETTING_KEYS:
            p[key] = ref
        p.update(narration_voice="voice-clone", narration_speed=1.25)
        p["model_settings"] = {ref: {"resolution": "1080p"}, "other/keep": {"resolution": "720p"}}

    model_refs_pm.update_project(name, _mutate)


def _refs(model_refs_pm: ProjectManager, name: str) -> dict:
    p = model_refs_pm.load_project(name)
    return {k: p.get(k) for k in PROJECT_MODEL_SETTING_KEYS}


def _model_settings_keys(model_refs_pm: ProjectManager, name: str) -> list[str]:
    return sorted(model_refs_pm.load_project(name).get("model_settings", {}))


async def _provider(db_factory, models: list[dict]) -> str:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.example.com/v1",
            api_key="k",
            models=models,
        )
        await session.commit()
        return f"custom-{provider.id}"


async def _set_globals(db_factory, ref: str) -> None:
    async with db_factory() as session:
        svc = ConfigService(session)
        await svc.set_setting("narration_voice", "voice-clone")
        await svc.set_setting("narration_speed", "1.25")
        for key in GLOBAL_MODEL_SETTING_KEYS:
            await svc.set_setting(key, ref)
        await session.commit()


async def _globals(db_factory) -> dict[str, str]:
    async with db_factory() as session:
        svc = ConfigService(session)
        return {key: await svc.get_setting(key, "") for key in GLOBAL_MODEL_SETTING_KEYS}


def _assert_deleted_model_refs_cleared(model_refs_pm: ProjectManager, name: str, pid: str, model_id: str) -> None:
    ref = f"{pid}/{model_id}"
    refs = _refs(model_refs_pm, name)
    assert all(key not in refs or refs[key] is None for key in CLEARED_ON_DELETE), refs
    assert all(refs[key] == ref for key in VIDEO_BUCKET_KEYS), refs
    assert {"audio_backend", "narration_voice", "narration_speed"}.isdisjoint(model_refs_pm.load_project(name))
    assert _model_settings_keys(model_refs_pm, name) == ["other/keep"]


def _assert_not_hijacked_by_readded(
    model_refs_client: TestClient, model_refs_pm: ProjectManager, name: str, pid: str, model_id: str
) -> None:
    model_refs_pm.update_project(name, lambda p: [p.pop(key, None) for key in VIDEO_BUCKET_KEYS])
    added = model_refs_client.post(
        f"/api/v1/providers/{pid}/models", json={"model_id": model_id, "endpoint": "openai-chat"}
    )
    assert added.status_code == 201, added.text
    refs = model_refs_client.get(f"/api/v1/providers/{pid}/model-references", params={"model_id": model_id}).json()
    assert refs["projects"] == []
    assert f"{pid}/{model_id}" not in _model_settings_keys(model_refs_pm, name)


async def test_rename_rewrites_project_model_settings_and_global_refs(model_refs_client, db_factory, model_refs_pm):
    pid = await _provider(db_factory, [_model("keep", default=True), _model("old-a")])
    _project(model_refs_pm, "pa", f"{pid}/old-a")
    await _set_globals(db_factory, f"{pid}/old-a")

    resp = model_refs_client.patch(f"/api/v1/providers/{pid}/models/old-a", json={"model_id": "new-a"})
    assert resp.status_code == 200, resp.text

    assert _refs(model_refs_pm, "pa") == dict.fromkeys(PROJECT_MODEL_SETTING_KEYS, f"{pid}/new-a")
    assert model_refs_pm.load_project("pa")["model_settings"] == {
        f"{pid}/new-a": {"resolution": "1080p"},
        "other/keep": {"resolution": "720p"},
    }
    assert await _globals(db_factory) == dict.fromkeys(GLOBAL_MODEL_SETTING_KEYS, f"{pid}/new-a")

    assert (
        model_refs_client.patch(f"/api/v1/providers/{pid}/models/keep", json={"model_id": "old-a"}).status_code == 200
    )
    assert _refs(model_refs_pm, "pa") == dict.fromkeys(PROJECT_MODEL_SETTING_KEYS, f"{pid}/new-a")
    refs = model_refs_client.get(f"/api/v1/providers/{pid}/model-references", params={"model_id": "old-a"}).json()
    assert refs["projects"] == []
    from lib.backends.text_backends.base import TextTaskType
    from lib.video_runtime.text import TextRuntime

    target = await TextRuntime(ConfigResolver(db_factory)).for_task(TextTaskType.SCRIPT, "pa")
    assert (target.provider_id, target.model_id) == (pid, "new-a")


async def test_rename_leaves_other_providers_and_models_untouched(model_refs_client, db_factory, model_refs_pm):
    pid = await _provider(db_factory, [_model("old-a", default=True), _model("sibling")])
    other = await _provider(db_factory, [_model("old-a", default=True)])
    _project(model_refs_pm, "pa", f"{other}/old-a")
    _project(model_refs_pm, "pb", f"{pid}/sibling")

    resp = model_refs_client.patch(f"/api/v1/providers/{pid}/models/old-a", json={"model_id": "new-a"})
    assert resp.status_code == 200, resp.text

    assert _refs(model_refs_pm, "pa") == dict.fromkeys(PROJECT_MODEL_SETTING_KEYS, f"{other}/old-a")
    assert _refs(model_refs_pm, "pb") == dict.fromkeys(PROJECT_MODEL_SETTING_KEYS, f"{pid}/sibling")


async def test_delete_model_clears_project_refs_except_video_buckets(model_refs_client, db_factory, model_refs_pm):
    pid = await _provider(db_factory, [_model("keep", default=True), _model("del-b")])
    _project(model_refs_pm, "pb", f"{pid}/del-b")
    _project(model_refs_pm, "switched", f"{pid}/keep")
    await _set_globals(db_factory, f"{pid}/del-b")

    assert model_refs_client.delete(f"/api/v1/providers/{pid}/models/del-b").status_code == 204

    switched = model_refs_pm.load_project("switched")
    assert (switched["audio_backend"], switched["narration_voice"], switched["narration_speed"]) == (
        f"{pid}/keep",
        "voice-clone",
        1.25,
    )
    _assert_deleted_model_refs_cleared(model_refs_pm, "pb", pid, "del-b")
    async with db_factory() as session:
        saved = await ConfigService(session).get_all_settings()
        assert not any(saved[key] for key in ("default_audio_backend", "narration_voice", "narration_speed"))
    assert not any(v == f"{pid}/del-b" for k, v in (await _globals(db_factory)).items() if k not in _GLOBAL_BUCKETS)
    for key in VIDEO_BUCKET_KEYS:
        assert _refs(model_refs_pm, "pb")[key] == f"{pid}/del-b"


_GLOBAL_BUCKETS = ("default_video_backend_i2v", "default_video_backend_r2v")


async def test_delete_model_then_readd_same_id_is_not_taken_over(model_refs_client, db_factory, model_refs_pm):
    pid = await _provider(db_factory, [_model("keep", default=True), _model("del-b")])
    _project(model_refs_pm, "pb", f"{pid}/del-b")
    await _set_globals(db_factory, f"{pid}/keep")
    assert model_refs_client.delete(f"/api/v1/providers/{pid}/models/del-b").status_code == 204
    async with db_factory() as session:
        saved = await ConfigService(session).get_all_settings()
        assert tuple(saved[key] for key in ("default_audio_backend", "narration_voice", "narration_speed")) == (
            f"{pid}/keep",
            "voice-clone",
            "1.25",
        )
    _assert_not_hijacked_by_readded(model_refs_client, model_refs_pm, "pb", pid, "del-b")


async def test_put_removing_model_clears_project_refs(model_refs_client, db_factory, model_refs_pm):
    pid = await _provider(db_factory, [_model("keep", default=True), _model("put-c")])
    _project(model_refs_pm, "pc", f"{pid}/put-c")

    db_id = int(pid.removeprefix("custom-"))
    full = model_refs_client.get(f"/api/v1/custom-providers/{db_id}").json()
    kept = [
        {k: m[k] for k in ("model_id", "display_name", "endpoint", "is_default", "is_enabled")}
        for m in full["models"]
        if m["model_id"] != "put-c"
    ]
    put = model_refs_client.put(
        f"/api/v1/custom-providers/{db_id}",
        json={"display_name": full["display_name"], "base_url": full["base_url"], "models": kept},
    )
    assert put.status_code == 200, put.text

    _assert_deleted_model_refs_cleared(model_refs_pm, "pc", pid, "put-c")
    _assert_not_hijacked_by_readded(model_refs_client, model_refs_pm, "pc", pid, "put-c")


async def test_put_keeping_all_models_leaves_project_refs(model_refs_client, db_factory, model_refs_pm):
    pid = await _provider(db_factory, [_model("keep", default=True), _model("put-c")])
    _project(model_refs_pm, "pc", f"{pid}/put-c")
    before = model_refs_pm.load_project("pc")

    db_id = int(pid.removeprefix("custom-"))
    full = model_refs_client.get(f"/api/v1/custom-providers/{db_id}").json()
    kept = [
        {k: m[k] for k in ("model_id", "display_name", "endpoint", "is_default", "is_enabled")} for m in full["models"]
    ]
    put = model_refs_client.put(
        f"/api/v1/custom-providers/{db_id}",
        json={"display_name": "新名字", "base_url": full["base_url"], "models": kept},
    )
    assert put.status_code == 200, put.text

    after = model_refs_pm.load_project("pc")
    assert {k: after.get(k) for k in PROJECT_MODEL_SETTING_KEYS} == {
        k: before.get(k) for k in PROJECT_MODEL_SETTING_KEYS
    }
    assert after["model_settings"] == before["model_settings"]


async def test_delete_provider_clears_model_settings_of_its_models(model_refs_client, db_factory, model_refs_pm):
    pid = await _provider(db_factory, [_model("m1", default=True), _model("m2")])
    _project(model_refs_pm, "pd", f"{pid}/m1")

    def _more(p: dict) -> None:
        p["model_settings"][f"{pid}/m2"] = {"resolution": "4k"}

    model_refs_pm.update_project("pd", _more)

    db_id = int(pid.removeprefix("custom-"))
    assert model_refs_client.delete(f"/api/v1/custom-providers/{db_id}").status_code == 204

    assert {"audio_backend", "narration_voice", "narration_speed"}.isdisjoint(model_refs_pm.load_project("pd"))
    assert _model_settings_keys(model_refs_pm, "pd") == ["other/keep"]


def _sample_wav(level: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(level.to_bytes(2, "little") * 16000)
    return buffer.getvalue()


@pytest.mark.parametrize("whole_provider", [False, True], ids=["model", "provider"])
async def test_deleted_tts_override_inherits_and_synthesizes_again(
    model_refs_client, model_refs_pm, db_factory, monkeypatch, whole_provider
):
    manager = model_refs_pm
    bind_session_factory(monkeypatch, db_factory)
    pid = await _provider(db_factory, [{**_model("relay", default=True), "endpoint": "openai-tts"}, _model("keep")])
    backend = f"{pid}/relay"
    await _set_globals(db_factory, backend)
    manager.create_project("demo")
    manager.create_project_metadata("demo", narration={"narration_delivery": "use_tts"})
    script = {
        "episode": 1,
        "title": "旁白",
        "summary": "",
        "content_mode": "narration",
        "novel": {"title": "旁白", "chapter": "1"},
        "segments": [
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "novel_text": "一路向前。",
                "characters_in_segment": [],
                "image_prompt": None,
                "video_prompt": None,
            }
        ],
    }
    manager.save_script("demo", script, "episode_1.json")
    override = {"audio_backend": backend, "narration_voice": "voice-clone", "narration_speed": 1.25}
    _project(manager, "switched", backend)
    root = manager.get_project_path("demo")
    relative = "audio/segment_E1S01.wav"
    payload = {"script_file": "episode_1.json"}
    with respx.mock as upstream:
        upstream.post("https://relay.example.com/v1/audio/speech").mock(
            return_value=httpx.Response(200, content=_sample_wav(1), headers={"Content-Type": "audio/wav"})
        )
        await execute_tts_task("demo", "E1S01", payload)
    assert (root / relative).read_bytes() == _sample_wav(1)

    manager.update_project("demo", lambda project: project.update(override))
    next_model = "minimax/speech-2.8-hd"
    switched = {"audio_backend": next_model, "narration_voice": "male-qn-qingse", "narration_speed": 1.1}
    list_projects = manager.list_projects

    def switch_before_cleanup():
        projects = list_projects()
        manager.update_project("switched", lambda project: project.update(switched))
        return projects

    with monkeypatch.context() as cleanup:
        cleanup.setattr(manager, "list_projects", switch_before_cleanup)
        url = (
            f"/api/v1/custom-providers/{pid.removeprefix('custom-')}"
            if whole_provider
            else f"/api/v1/providers/{pid}/models/relay"
        )
        response = model_refs_client.delete(url)
    assert response.status_code == 204, response.text
    assert override.keys().isdisjoint(manager.load_project("demo"))
    assert {k: manager.load_project("switched")[k] for k in switched} == switched
    async with db_factory() as session:
        svc = ConfigService(session)
        saved = await svc.get_all_settings()
        assert not any(saved[key] for key in ("default_audio_backend", "narration_voice", "narration_speed"))
        await patch_global_tts(svc, session, {"default_audio_backend": next_model})
        group = await svc.get_all_settings()
        assert group["narration_voice"] == "male-qn-qingse"
        assert not group["narration_speed"]
        await svc.set_provider_config("minimax", "api_key", "test")
        await session.commit()
    key = ArtifactKey.episode_audio(1, "E1S01")
    assert ArtifactCurrencyResolver(root).compare(key, artifact_path=relative).status is ArtifactStatus.CURRENT
    with respx.mock as upstream:
        upstream.post("https://api.minimaxi.com/v1/t2a_v2").mock(
            return_value=httpx.Response(
                200, json={"base_resp": {"status_code": 0}, "data": {"audio": _sample_wav(2).hex()}}
            )
        )
        await execute_tts_task("demo", "E1S01", payload)
    assert (root / relative).read_bytes() == _sample_wav(2)
    history = VersionManager(root).get_versions("audio", "E1S01")
    first, second = history["versions"]
    assert history["current_version"] == second["version"]
    assert (root / first["file"]).read_bytes() == _sample_wav(1)
    assert (second["tts_provider_id"], second["tts_model_id"], second["tts_voice"], second["tts_speed"]) == (
        "minimax",
        "speech-2.8-hd",
        "male-qn-qingse",
        None,
    )
    assert second["tts_actual_duration_seconds"] == pytest.approx(1.0)
