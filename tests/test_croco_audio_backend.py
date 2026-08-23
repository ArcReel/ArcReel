"""Croco Music 3 request contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.audio_backends.base import AudioSynthesisRequest
from lib.audio_backends.croco import CrocoAudioBackend

pytestmark = pytest.mark.unit


class _Client:
    def __init__(self) -> None:
        self.submission = None

    async def submit_job(self, **kwargs):
        self.submission = kwargs
        return {"job_id": "music-job"}

    async def wait_until_terminal(self, _job_id: str):
        return {"status": "succeeded"}

    async def list_outputs(self, _job_id: str):
        return {"items": [{"output_id": "audio", "delivery_state": "ready"}]}

    async def download_output(self, _job_id: str, _output_id: str, output_path: Path):
        output_path.write_bytes(b"music")


async def test_croco_audio_passes_music3_structured_parameters(tmp_path: Path) -> None:
    backend = CrocoAudioBackend(api_key="token")
    client = _Client()
    backend._client = client
    output = tmp_path / "bgm.mp3"

    await backend.synthesize(
        AudioSynthesisRequest(
            text="Global Metadata: calm instrumental",
            output_path=output,
            voice="",
            lyrics="",
            max_duration=42.5,
            seed=7,
            tiled_decode=False,
            output_format="mp3",
            client_job_id="arcreel:music:test",
        )
    )

    assert client.submission == {
        "model_id": "minimax-music-3",
        "operation": "audio.generate",
        "contract_version": "1",
        "parameters": {
            "caption": "Global Metadata: calm instrumental",
            "lyrics": "",
            "max_duration": 42.5,
            "seed": 7,
            "tiled_decode": False,
            "output_format": "mp3",
        },
        "client_job_id": "arcreel:music:test",
    }
    assert output.read_bytes() == b"music"
