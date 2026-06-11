"""execute_tts_task 执行链单测：文本来源三分支 / 写回 narration_audio /
_get_or_create_audio_backend 缓存与自定义供应商路径 / get_media_generator(needs_audio) /
compute_affected_fingerprints tts 分支 / 任务注册表。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from lib.config.resolver import ConfigResolver, ProviderModel
from server.services import generation_tasks


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {"name": "demo", "content_mode": "narration"}
        self.script = {
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "novel_text": "却说天下大势，分久必合，合久必分。"},
                {"segment_id": "E1S02", "novel_text": "   "},
            ],
        }
        self.updated_assets = []

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script

    def update_scene_asset(self, **kwargs):
        self.updated_assets.append(kwargs)


class _FakeAudioGenerator:
    def __init__(self):
        self.audio_calls = []
        self.versions = self

    async def generate_audio_async(self, **kwargs):
        self.audio_calls.append(kwargs)
        return Path("/tmp/audio.wav"), 3

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"created_at": "2026-06-01T00:00:00Z"}]}


@pytest.fixture
def tts_env(monkeypatch, tmp_path):
    pm = _FakePM(tmp_path / "projects" / "demo")
    gen = _FakeAudioGenerator()
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(gen))
    monkeypatch.setattr(ConfigResolver, "resolve_narration_voice", _async_return("Cherry"))
    return pm, gen


class TestExecuteTtsTask:
    async def test_explicit_payload_text(self, tts_env):
        pm, gen = tts_env
        result = await generation_tasks.execute_tts_task("demo", "E1S01", {"text": "你好世界"})
        assert result == {
            "version": 3,
            "file_path": "audio/segment_E1S01.wav",
            "created_at": "2026-06-01T00:00:00Z",
            "resource_type": "audio",
            "resource_id": "E1S01",
        }
        call = gen.audio_calls[0]
        assert call["text"] == "你好世界"
        assert call["voice"] == "Cherry"
        assert call["resource_id"] == "E1S01"
        # 无 script_file → 不写回 narration_audio
        assert pm.updated_assets == []

    async def test_text_from_script_segment_and_writeback(self, tts_env):
        pm, gen = tts_env
        await generation_tasks.execute_tts_task("demo", "E1S01", {"script_file": "episode_1.json"})
        assert gen.audio_calls[0]["text"] == "却说天下大势，分久必合，合久必分。"
        wb = pm.updated_assets[0]
        assert wb["asset_type"] == "narration_audio"
        assert wb["asset_path"] == "audio/segment_E1S01.wav"
        assert wb["scene_id"] == "E1S01"
        assert wb["script_filename"] == "episode_1.json"

    async def test_no_text_no_script_file_raises(self, tts_env):
        with pytest.raises(ValueError, match="payload.text 或 payload.script_file"):
            await generation_tasks.execute_tts_task("demo", "E1S01", {})

    async def test_segment_not_found_raises(self, tts_env):
        with pytest.raises(ValueError, match="segment not found"):
            await generation_tasks.execute_tts_task("demo", "NOPE", {"script_file": "episode_1.json"})

    async def test_blank_novel_text_raises(self, tts_env):
        with pytest.raises(ValueError, match="无可合成的旁白文本"):
            await generation_tasks.execute_tts_task("demo", "E1S02", {"script_file": "episode_1.json"})

    def test_tts_registered_in_executors_and_change_specs(self):
        assert generation_tasks._TASK_EXECUTORS["tts"] is generation_tasks.execute_tts_task
        kind, event, label, _ = generation_tasks._TASK_CHANGE_SPECS["tts"]
        assert kind == "segment"
        assert event == "tts_ready"


class TestGetOrCreateAudioBackend:
    async def test_custom_provider_routes_to_custom_factory(self, monkeypatch):
        sentinel = object()
        calls = []

        async def _fake_create_custom(provider_name, model_id, media_type):
            calls.append((provider_name, model_id, media_type))
            return sentinel

        monkeypatch.setattr(generation_tasks, "_create_custom_backend", _fake_create_custom)
        monkeypatch.setattr(generation_tasks, "_backend_cache", {})

        b1 = await generation_tasks._get_or_create_audio_backend("custom-3", {"model": "tts-1"}, None)
        b2 = await generation_tasks._get_or_create_audio_backend("custom-3", {"model": "tts-1"}, None)

        assert b1 is sentinel and b2 is sentinel
        assert calls == [("custom-3", "tts-1", "audio")], "第二次调用须命中缓存，不再重建 backend"

    async def test_builtin_created_and_cached(self, monkeypatch):
        created = []
        sentinel = object()

        def _fake_create(name, **kwargs):
            created.append((name, kwargs))
            return sentinel

        monkeypatch.setattr("lib.audio_backends.create_backend", _fake_create)
        monkeypatch.setattr(generation_tasks, "_fill_simple_provider_kwargs", _async_return(None))
        monkeypatch.setattr(generation_tasks, "_backend_cache", {})

        b1 = await generation_tasks._get_or_create_audio_backend(
            "dashscope", {}, None, default_audio_model="qwen3-tts-flash"
        )
        b2 = await generation_tasks._get_or_create_audio_backend(
            "dashscope", {}, None, default_audio_model="qwen3-tts-flash"
        )
        assert b1 is sentinel and b2 is sentinel
        assert len(created) == 1, "第二次调用须命中缓存，不再重建 backend"

    async def test_payload_model_overrides_default(self, monkeypatch):
        created = []

        def _fake_create(name, **kwargs):
            created.append(name)
            return object()

        monkeypatch.setattr("lib.audio_backends.create_backend", _fake_create)

        filled = []

        async def _fake_fill(backend_name, resolver, kwargs, effective_model):
            filled.append(effective_model)

        monkeypatch.setattr(generation_tasks, "_fill_simple_provider_kwargs", _fake_fill)
        monkeypatch.setattr(generation_tasks, "_backend_cache", {})

        await generation_tasks._get_or_create_audio_backend(
            "dashscope", {"model": "explicit-model"}, None, default_audio_model="fallback-model"
        )
        assert filled == ["explicit-model"]


class TestGetMediaGeneratorNeedsAudio:
    async def test_only_audio_backend_constructed(self, monkeypatch, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        project_path.mkdir(parents=True)
        pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)

        sentinel_backend = object()
        audio_resolutions = []

        class _FakeResolver:
            async def resolve_audio_backend(self, project, payload):
                audio_resolutions.append((project, payload))
                return ProviderModel("dashscope", "qwen3-tts-flash")

        @asynccontextmanager
        async def _fake_session(self):
            yield _FakeResolver()

        monkeypatch.setattr(ConfigResolver, "session", _fake_session)
        monkeypatch.setattr(generation_tasks, "_get_or_create_audio_backend", _async_return(sentinel_backend))

        generator = await generation_tasks.get_media_generator(
            "demo", payload={"k": 1}, require_image_backend=False, needs_audio=True
        )
        assert generator._audio_backend is sentinel_backend
        assert generator._image_backend is None
        assert generator._video_backend is None
        assert audio_resolutions == [(pm.project, {"k": 1})]


class TestComputeAffectedFingerprintsTts:
    def test_tts_includes_audio_path(self, monkeypatch, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        (project_path / "audio").mkdir(parents=True)
        (project_path / "audio" / "segment_E1S01.wav").write_bytes(b"RIFF")
        pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)

        fp = generation_tasks.compute_affected_fingerprints("demo", "tts", "E1S01")
        assert "audio/segment_E1S01.wav" in fp
