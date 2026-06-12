"""ComfyUIVideoBackend — video generation via ComfyUI API."""

from __future__ import annotations

import logging
from pathlib import Path

from lib.comfyui.client import ComfyUIClient
from lib.comfyui.workflows import build_t2v_workflow
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

PROVIDER_COMFYUI = "comfyui"


class ComfyUIVideoBackend:
    """Video generation backend backed by a local ComfyUI server."""

    def __init__(self, *, base_url: str, model: str) -> None:
        self._client = ComfyUIClient(base_url)
        self._model = model

    @property
    def name(self) -> str:
        return PROVIDER_COMFYUI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(reference_images=True, max_reference_images=1)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        w, h = self._resolve_dimensions(request)
        frames = max(1, request.duration_seconds * 8)  # ~8 fps default

        workflow = build_t2v_workflow(
            checkpoint=self._model,
            prompt=request.prompt,
            width=w,
            height=h,
            frames=frames,
            seed=request.seed,
            checkpoint_name=self._model,
        )

        logger.info(
            "ComfyUI video generation: model=%s, %dx%dx%d frames",
            self._model, w, h, frames,
        )

        prompt_id = await self._client.queue_prompt(workflow)
        outputs = await self._client.wait_for_completion(prompt_id, max_wait=1800.0)

        video_path = await self._extract_and_download(outputs, request.output_path)

        logger.info("ComfyUI video generation complete: %s", video_path)
        return VideoGenerationResult(
            video_path=video_path,
            provider=PROVIDER_COMFYUI,
            model=self._model,
            duration_seconds=request.duration_seconds,
            seed=request.seed,
        )

    async def _extract_and_download(self, outputs: dict, output_path: Path) -> Path:
        """Extract output from ComfyUI (gifs/videos or image sequences)."""
        for _node_id, node_outputs in outputs.items():
            # VHS_VideoCombine outputs gifs/videos
            for key in ("gifs", "videos"):
                items = node_outputs.get(key, [])
                if items:
                    item = items[0]
                    return await self._client.download_output(
                        filename=item["filename"],
                        output_path=output_path,
                        subfolder=item.get("subfolder", ""),
                        file_type=item.get("type", "output"),
                    )
            # Fallback: grab image sequence and note it
            images = node_outputs.get("images", [])
            if images:
                img = images[0]
                return await self._client.download_output(
                    filename=img["filename"],
                    output_path=output_path,
                    subfolder=img.get("subfolder", ""),
                    file_type=img.get("type", "output"),
                )
        raise RuntimeError("ComfyUI produced no video output")

    @staticmethod
    def _resolve_dimensions(request: VideoGenerationRequest) -> tuple[int, int]:
        if request.resolution:
            try:
                w, h = request.resolution.split("x")
                return int(w), int(h)
            except (ValueError, AttributeError):
                pass
        ratio_map = {
            "9:16": (576, 1024),
            "16:9": (1024, 576),
            "1:1": (768, 768),
        }
        return ratio_map.get(request.aspect_ratio, (512, 768))
