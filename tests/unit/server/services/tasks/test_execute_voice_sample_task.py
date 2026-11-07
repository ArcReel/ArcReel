"""execute_character_voice_sample_task unit coverage: explicitly required text/voice, persisted output and upload-equivalent duration/size validation, resource_id namespace isolation and task registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.config.resolver import ProviderModel
from lib.generation.generation_queue import DispatchProviderChanged
from lib.project.resource_paths import resource_relative_path
from server.services.tasks import generation_tasks
from server.services.tasks.generation_context import AudioLaneResult, GenerationContext
from tests.fakes import use_generation_context, use_project_manager, write_backend_output


def _audio_ctx(generator):
    ctx = GenerationContext(
        generator=generator,
        audio_lane=AudioLaneResult(
            provider_model=ProviderModel("dashscope", "qwen3-tts-flash"),
            backend_name="dashscope",
            backend_model="qwen3-tts-flash",
            voices=(),
        ),
    )

    async def _resolve(*args, **kwargs):
        assert kwargs.get("audio") is not None
        return ctx

    return _resolve


class _FakePM:
    def __init__(self, project_path: Path):
        self._project_path = project_path
        self.project = {"name": "demo", "characters": {"艾莉": {}, "E1S01": {}}}

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self._project_path


class _FakeAudioGenerator:
    def __init__(self, project_path: Path, content: bytes = b"fake-wav-bytes"):
        self._project_path = project_path
        self._content = content
        self.audio_calls: list[dict] = []
        self.versions = self

    async def generate_audio_async(self, *, text, resource_id, voice, speed, task_id=None, target_resource=None):
        self.target_resource = target_resource
        self.audio_calls.append(
            {"text": text, "resource_id": resource_id, "voice": voice, "speed": speed, "task_id": task_id}
        )
        path = self._project_path / resource_relative_path("audio", resource_id)
        # Production backends write artifacts in worker threads; substitutes likewise avoid blocking the event loop.
        await write_backend_output(path, self._content)
        return path, 1

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"created_at": "2026-06-01T00:00:00Z"}]}


@pytest.fixture
def voice_sample_env(monkeypatch, tmp_path):
    project_path = tmp_path / "projects" / "demo"
    project_path.mkdir(parents=True)
    pm = _FakePM(project_path)
    gen = _FakeAudioGenerator(project_path)
    use_project_manager(monkeypatch, pm)
    use_generation_context(monkeypatch, _audio_ctx(gen))

    async def _always_valid_duration(content, suffix):
        return 5.0

    monkeypatch.setattr(generation_tasks, "probe_audio_duration_seconds", _always_valid_duration)
    return pm, gen


class TestExecuteCharacterVoiceSampleTask:
    async def test_success(self, voice_sample_env):
        _pm, gen = voice_sample_env
        result = await generation_tasks.execute_character_voice_sample_task(
            "demo", "艾莉", {"prompt": "你好，这是一段声音示例。", "voice": "Cherry"}, task_id="task-1"
        )

        assert gen.audio_calls == [
            {
                "text": "你好，这是一段声音示例。",
                "resource_id": "voice_sample__艾莉__task-1",
                "voice": "Cherry",
                "speed": None,
                "task_id": "task-1",
            }
        ]
        # Voice samples belong to characters: deleting the character trashes samples; do not recheck by the sample's own resource ID.
        assert gen.target_resource == ("characters", "艾莉")
        assert result["resource_type"] == "audio"
        assert result["resource_id"] == "voice_sample__艾莉__task-1"
        assert result["character_name"] == "艾莉"
        assert result["voice"] == "Cherry"
        assert result["duration_seconds"] == 5.0
        assert result["file_path"] == resource_relative_path("audio", "voice_sample__艾莉__task-1")

    async def test_provider_changed_since_claim_is_raised_before_synthesis(self, voice_sample_env):
        """If resolved audio provider differs from the claimed slot, do not synthesize; let the worker update the record and requeue."""
        _pm, gen = voice_sample_env
        with pytest.raises(DispatchProviderChanged) as caught:
            await generation_tasks.execute_character_voice_sample_task(
                "demo",
                "艾莉",
                {"prompt": "你好，这是一段声音示例。", "voice": "Cherry"},
                task_id="task-1",
                claimed_provider_id="openai",
            )

        assert (caught.value.claimed_provider_id, caught.value.actual_provider_id) == ("openai", "dashscope")
        assert gen.audio_calls == []

    async def test_resource_id_namespaced_away_from_narration_segments(self, voice_sample_env):
        # Narration segment IDs may collide with character names; voice_sample_resource_id prefixes isolate namespaces.
        _pm, gen = voice_sample_env
        await generation_tasks.execute_character_voice_sample_task(
            "demo", "E1S01", {"prompt": "文本", "voice": "Ethan"}, task_id="task-1"
        )
        assert gen.audio_calls[0]["resource_id"] == "voice_sample__E1S01__task-1"
        assert gen.audio_calls[0]["resource_id"] != "E1S01"

    async def test_distinct_tasks_get_distinct_files(self, voice_sample_env):
        # Regenerating before confirming a previous successful sample creates a new task; resource ID must vary with task_id,
        # or the new write would overwrite the file still referenced by the earlier result.file_path.
        _pm, gen = voice_sample_env
        await generation_tasks.execute_character_voice_sample_task(
            "demo", "艾莉", {"prompt": "文本 A", "voice": "Cherry"}, task_id="task-A"
        )
        await generation_tasks.execute_character_voice_sample_task(
            "demo", "艾莉", {"prompt": "文本 B", "voice": "Cherry"}, task_id="task-B"
        )
        resource_ids = {call["resource_id"] for call in gen.audio_calls}
        assert resource_ids == {"voice_sample__艾莉__task-A", "voice_sample__艾莉__task-B"}

    async def test_missing_task_id_raises(self, voice_sample_env):
        with pytest.raises(ValueError, match="task_id"):
            await generation_tasks.execute_character_voice_sample_task(
                "demo", "艾莉", {"prompt": "你好", "voice": "Cherry"}
            )

    async def test_missing_text_raises(self, voice_sample_env):
        with pytest.raises(ValueError, match="prompt"):
            await generation_tasks.execute_character_voice_sample_task(
                "demo", "艾莉", {"voice": "Cherry"}, task_id="task-1"
            )

    async def test_blank_text_raises(self, voice_sample_env):
        with pytest.raises(ValueError, match="prompt"):
            await generation_tasks.execute_character_voice_sample_task(
                "demo", "艾莉", {"prompt": "   ", "voice": "Cherry"}, task_id="task-1"
            )

    async def test_missing_voice_raises(self, voice_sample_env):
        with pytest.raises(ValueError, match="voice"):
            await generation_tasks.execute_character_voice_sample_task(
                "demo", "艾莉", {"prompt": "你好"}, task_id="task-1"
            )

    async def test_duration_out_of_range_raises(self, voice_sample_env, monkeypatch):
        async def _too_long(content, suffix):
            return 20.0

        monkeypatch.setattr(generation_tasks, "probe_audio_duration_seconds", _too_long)
        with pytest.raises(ValueError, match="时长"):
            await generation_tasks.execute_character_voice_sample_task(
                "demo", "艾莉", {"prompt": "你好", "voice": "Cherry"}, task_id="task-1"
            )

    async def test_duration_none_skips_validation(self, voice_sample_env, monkeypatch):
        # Without bundled ffmpeg, probe_audio_duration_seconds returns None; allow degraded behavior by repository convention.
        async def _unavailable(content, suffix):
            return None

        monkeypatch.setattr(generation_tasks, "probe_audio_duration_seconds", _unavailable)
        result = await generation_tasks.execute_character_voice_sample_task(
            "demo", "艾莉", {"prompt": "你好", "voice": "Cherry"}, task_id="task-1"
        )
        assert result["duration_seconds"] is None

    async def test_oversized_file_raises(self, voice_sample_env, monkeypatch):
        monkeypatch.setattr(generation_tasks, "AUDIO_REFERENCE_MAX_BYTES", 4)
        with pytest.raises(ValueError, match="MB"):
            await generation_tasks.execute_character_voice_sample_task(
                "demo", "艾莉", {"prompt": "你好", "voice": "Cherry"}, task_id="task-1"
            )

    async def test_character_deleted_after_enqueue_raises(self, voice_sample_env):
        # Character deleted after enqueue but before worker claim: do not spend on synthesis API calls for orphan previews.
        pm, gen = voice_sample_env
        del pm.project["characters"]["艾莉"]
        with pytest.raises(ValueError, match="character not found"):
            await generation_tasks.execute_character_voice_sample_task(
                "demo", "艾莉", {"prompt": "你好", "voice": "Cherry"}, task_id="task-1"
            )
        assert gen.audio_calls == []

    def test_registered_in_task_executors(self):
        assert generation_tasks._TASK_EXECUTORS["voice_sample"] is generation_tasks.execute_character_voice_sample_task

    def test_voice_sample_resource_id_deterministic(self):
        assert generation_tasks.voice_sample_resource_id("艾莉", "task-1") == "voice_sample__艾莉__task-1"

    def test_voice_sample_resource_id_truncates_long_character_names(self):
        long_name = "A" * 200
        resource_id = generation_tasks.voice_sample_resource_id(long_name, "task-1")
        # Trim character-name portions to _SAMPLE_ID_NAME_MAX_BYTES while retaining the complete task-specific suffix,
        # leaving room for VersionManager filename suffixes to avoid NAME_MAX overflow.
        assert resource_id.endswith("__task-1")
        assert len(resource_id.encode("utf-8")) < 128

    def test_voice_sample_resource_id_truncates_multibyte_names_without_corrupting_encoding(self):
        long_name = "艾" * 100
        resource_id = generation_tasks.voice_sample_resource_id(long_name, "task-1")
        prefix = "voice_sample__"
        suffix = "__task-1"
        safe_name = resource_id[len(prefix) : -len(suffix)]
        # If byte trimming cuts a multibyte character, drop the entire character. Assert actual retained character count, not only
        # successful encoding/decoding: that alone would miss wrong limits or excessive trimming.
        expected_chars = generation_tasks._SAMPLE_ID_NAME_MAX_BYTES // len("艾".encode())
        assert safe_name == "艾" * expected_chars
        assert len(safe_name.encode("utf-8")) <= generation_tasks._SAMPLE_ID_NAME_MAX_BYTES
        assert resource_id.endswith(suffix)
