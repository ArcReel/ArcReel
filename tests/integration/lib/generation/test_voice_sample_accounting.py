"""Voice sample resource IDs survive real audio generation and usage readback."""

import asyncio
import json
from typing import ClassVar

import pytest

from lib.backends.audio_backends.base import AudioCapability, AudioSynthesisRequest, AudioSynthesisResult
from lib.billing.ledger import Ledger
from lib.db.repositories.usage_repo import UsageRepository
from lib.generation.media_generator import MediaGenerator
from server.services.tasks.generation_tasks import voice_sample_resource_id
from tests.fakes import write_backend_output


class SampleAudioBackend:
    name = "dashscope"
    model = "qwen3-tts-flash"
    capabilities: ClassVar[set[AudioCapability]] = {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self):
        return []

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        await write_backend_output(request.output_path, b"sample-audio")
        return AudioSynthesisResult(self.name, self.model, request.text, request.output_path)


@pytest.mark.parametrize("character_name", ["艾莉", "A" * 200, "艾" * 100])
async def test_voice_sample_generation_records_long_resource_id(session_factory, tmp_path, character_name):
    task_id = "a" * 32
    sample_id = voice_sample_resource_id(character_name, task_id)
    await asyncio.to_thread(
        (tmp_path / "project.json").write_text, json.dumps({"characters": {character_name: {}}}), encoding="utf-8"
    )
    generator = await asyncio.to_thread(
        MediaGenerator, tmp_path, audio_backend=SampleAudioBackend(), audio_provider_id="dashscope"
    )
    generator.ledger = Ledger(session_factory=session_factory)

    output, version = await generator.generate_audio_async(
        text="试听文本",
        resource_id=sample_id,
        voice="Cherry",
        task_id=task_id,
        target_resource=("characters", character_name),
    )

    assert await asyncio.to_thread(output.read_bytes) == b"sample-audio"
    assert version == 1
    async with session_factory() as session:
        repository = UsageRepository(session)
        page = await repository.list_records()
        assert page["total"] == 1
        record = await repository.get_record(page["items"][0]["id"])
    assert record is not None
    assert record["status"] == "success"
    assert record["segment_id"] == sample_id
    assert record["task_id"] == task_id
