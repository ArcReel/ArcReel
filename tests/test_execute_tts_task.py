"""execute_tts_task 执行链单测：文本来源三分支 / 写回 narration_audio /
_get_or_create_audio_backend 缓存与自定义供应商路径 /
compute_affected_fingerprints tts 分支 / 任务注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from lib.config.resolver import ConfigResolver, ProviderModel
from server.services import generation_context, generation_tasks
from server.services.generation_context import AudioLaneResult, GenerationContext


def _audio_ctx(generator, *, voice="Cherry", speed=None):
    """把 audio lane 解析产物拼成假 GenerationContext，替换 resolve_generation_context 单点。"""
    ctx = GenerationContext(
        generator=generator,
        audio_lane=AudioLaneResult(
            provider_model=ProviderModel("dashscope", "qwen3-tts-flash"),
            backend_name="dashscope",
            backend_model="qwen3-tts-flash",
            narration_voice=voice,
            narration_speed=speed,
        ),
    )

    async def _resolve(*args, **kwargs):
        assert kwargs.get("audio") is not None
        assert kwargs.get("image") is None
        assert kwargs.get("video") is None
        return ctx

    return _resolve


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
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", _audio_ctx(gen))
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

    async def test_narration_speed_passed_to_generator(self, tts_env, monkeypatch):
        pm, gen = tts_env
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _audio_ctx(gen, speed=1.5))
        await generation_tasks.execute_tts_task("demo", "E1S01", {"text": "你好"})
        assert gen.audio_calls[0]["speed"] == 1.5

    async def test_unset_narration_speed_passes_none(self, tts_env):
        pm, gen = tts_env
        await generation_tasks.execute_tts_task("demo", "E1S01", {"text": "你好"})
        assert gen.audio_calls[0]["speed"] is None

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
    """audio backend 构造统一委托 assemble_backend；缓存留在调用方编排层。"""

    async def test_custom_provider_routes_through_assemble(self, monkeypatch):
        sentinel = object()
        calls = []

        async def _fake_assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            calls.append((provider_id, media_type, model_id))
            return sentinel

        monkeypatch.setattr(generation_context, "assemble_backend", _fake_assemble)
        monkeypatch.setattr(generation_context, "_backend_cache", generation_context._BackendCache())

        resolver = cast(ConfigResolver, None)
        b1 = await generation_context._get_or_create_audio_backend("custom-3", {"model": "tts-1"}, resolver)
        b2 = await generation_context._get_or_create_audio_backend("custom-3", {"model": "tts-1"}, resolver)

        assert b1 is sentinel and b2 is sentinel
        assert calls == [("custom-3", "audio", "tts-1")], "第二次调用须命中缓存，不再重建 backend"

    async def test_builtin_created_and_cached(self, monkeypatch):
        created = []
        sentinel = object()

        async def _fake_assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            created.append((provider_id, media_type, model_id))
            return sentinel

        monkeypatch.setattr(generation_context, "assemble_backend", _fake_assemble)
        monkeypatch.setattr(generation_context, "_backend_cache", generation_context._BackendCache())

        resolver = cast(ConfigResolver, None)
        b1 = await generation_context._get_or_create_audio_backend(
            "dashscope", {}, resolver, default_audio_model="qwen3-tts-flash"
        )
        b2 = await generation_context._get_or_create_audio_backend(
            "dashscope", {}, resolver, default_audio_model="qwen3-tts-flash"
        )
        assert b1 is sentinel and b2 is sentinel
        assert created == [("dashscope", "audio", "qwen3-tts-flash")], "第二次调用须命中缓存，不再重建 backend"

    async def test_payload_model_overrides_default(self, monkeypatch):
        calls = []

        async def _fake_assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            calls.append(model_id)
            return object()

        monkeypatch.setattr(generation_context, "assemble_backend", _fake_assemble)
        monkeypatch.setattr(generation_context, "_backend_cache", generation_context._BackendCache())

        await generation_context._get_or_create_audio_backend(
            "dashscope",
            {"model": "explicit-model"},
            cast(ConfigResolver, None),
            default_audio_model="fallback-model",
        )
        assert calls == ["explicit-model"]


class TestComputeAffectedFingerprintsTts:
    def test_tts_includes_audio_path(self, monkeypatch, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        (project_path / "audio").mkdir(parents=True)
        (project_path / "audio" / "segment_E1S01.wav").write_bytes(b"RIFF")
        pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)

        fp = generation_tasks.compute_affected_fingerprints("demo", "tts", "E1S01")
        assert "audio/segment_E1S01.wav" in fp
