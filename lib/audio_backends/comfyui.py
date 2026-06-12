"""ComfyUIAudioBackend — audio generation via ComfyUI API."""

from __future__ import annotations

import logging
from pathlib import Path

from lib.audio_backends.base import AudioGenerationRequest, AudioGenerationResult
from lib.comfyui.client import ComfyUIClient
from lib.comfyui.workflows import build_audio_workflow

logger = logging.getLogger(__name__)

PROVIDER_COMFYUI = "comfyui"


class ComfyUIAudioBackend:
    """Audio generation backend backed by a local ComfyUI server."""

    def __init__(self, *, base_url: str, model: str) -> None:
        self._client = ComfyUIClient(base_url)
        self._model = model

    @property
    def name(self) -> str:
        return PROVIDER_COMFYUI

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, request: AudioGenerationRequest) -> AudioGenerationResult:
        workflow = build_audio_workflow(
            model=self._model,
            prompt=request.prompt,
            duration=request.duration_seconds,
            negative_prompt=request.negative_prompt,
            seed=request.seed,
        )

        logger.info(
            "ComfyUI audio generation: model=%s, duration=%.1fs",
            self._model, request.duration_seconds,
        )

        prompt_id = await self._client.queue_prompt(workflow)
        outputs = await self._client.wait_for_completion(prompt_id, max_wait=600.0)

        audio_path = await self._extract_and_download(outputs, request.output_path)

        logger.info("ComfyUI audio generation complete: %s", audio_path)
        return AudioGenerationResult(
            audio_path=audio_path,
            provider=PROVIDER_COMFYUI,
            model=self._model,
            duration_seconds=request.duration_seconds,
        )

    async def _extract_and_download(self, outputs: dict, output_path: Path) -> Path:
        for _node_id, node_outputs in outputs.items():
            audios = node_outputs.get("audio", [])
            if audios:
                item = audios[0]
                return await self._client.download_output(
                    filename=item["filename"],
                    output_path=output_path,
                    subfolder=item.get("subfolder", ""),
                    file_type=item.get("type", "output"),
                )
        raise RuntimeError("ComfyUI produced no audio output")
