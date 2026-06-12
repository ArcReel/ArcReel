"""ComfyUIImageBackend — image generation via ComfyUI API."""

from __future__ import annotations

import logging
from pathlib import Path

from lib.comfyui.client import ComfyUIClient
from lib.comfyui.workflows import build_i2i_workflow, build_t2i_workflow, get_model_preset
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)

logger = logging.getLogger(__name__)

PROVIDER_COMFYUI = "comfyui"


class ComfyUIImageBackend:
    """Image generation backend backed by a local ComfyUI server."""

    def __init__(self, *, base_url: str, model: str, provider_id: int | None = None) -> None:
        self._client = ComfyUIClient(base_url)
        self._model = model
        self._provider_id = provider_id
        self._capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    @property
    def name(self) -> str:
        return PROVIDER_COMFYUI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    async def _get_model_settings_from_db(self) -> dict | None:
        """Read ComfyUI settings from custom_provider_model table."""
        if not self._provider_id:
            return None
        try:
            from lib.db import safe_session_factory
            from lib.db.repositories.custom_provider_repo import CustomProviderRepository

            async with safe_session_factory() as session:
                repo = CustomProviderRepository(session)
                model = await repo.get_model_by_ids(self._provider_id, self._model)
                if model and model.comfyui_sampler:
                    return {
                        "sampler": model.comfyui_sampler,
                        "steps": model.comfyui_steps,
                        "cfg": model.comfyui_cfg,
                        "negative_prompt": model.comfyui_negative_prompt,
                        "clip_skip": model.comfyui_clip_skip,
                    }
        except Exception:
            logger.warning("Failed to read ComfyUI settings from DB", exc_info=True)
        return None

    async def _get_project_overrides(self, project_name: str | None) -> dict | None:
        """Read comfyui_overrides from project.json."""
        if not project_name:
            return None
        try:
            from lib.project_manager import ProjectManager
            pm = ProjectManager()
            project = pm.load_project(project_name)
            overrides = project.get("comfyui_overrides")
            if overrides and isinstance(overrides, dict):
                # Only return non-None values
                return {k: v for k, v in overrides.items() if v is not None}
        except Exception:
            logger.warning("Failed to read project ComfyUI overrides", exc_info=True)
        return None

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        db_settings = await self._get_model_settings_from_db()
        project_overrides = await self._get_project_overrides(request.project_name)
        preset = get_model_preset(self._model, db_settings, project_overrides)

        if has_refs:
            ref_path = request.reference_images[0].path
            workflow = build_i2i_workflow(
                checkpoint=self._model,
                image_path=ref_path,
                prompt=request.prompt,
                negative_prompt=preset.get("negative_prompt", ""),
                checkpoint_name=self._model,
            )
        else:
            w, h = self._resolve_dimensions(request)
            workflow = build_t2i_workflow(
                checkpoint=self._model,
                prompt=request.prompt,
                negative_prompt=preset.get("negative_prompt", ""),
                width=w,
                height=h,
                seed=request.seed,
                steps=preset.get("steps", 20),
                cfg=preset.get("cfg", 7.0),
                sampler=preset.get("sampler", "euler"),
                checkpoint_name=self._model,
            )

        logger.info("ComfyUI image generation: model=%s, prompt=%.80s", self._model, request.prompt)

        prompt_id = await self._client.queue_prompt(workflow)
        outputs = await self._client.wait_for_completion(prompt_id)

        image_path = await self._extract_and_download_image(outputs, request.output_path)

        logger.info("ComfyUI image generation complete: %s", image_path)
        return ImageGenerationResult(
            image_path=image_path,
            provider=PROVIDER_COMFYUI,
            model=self._model,
            seed=request.seed,
        )

    async def _extract_and_download_image(self, outputs: dict, output_path: Path) -> Path:
        """Extract the first image filename from ComfyUI outputs and download it."""
        for _node_id, node_outputs in outputs.items():
            images = node_outputs.get("images", [])
            if images:
                img = images[0]
                return await self._client.download_output(
                    filename=img["filename"],
                    output_path=output_path,
                    subfolder=img.get("subfolder", ""),
                    file_type=img.get("type", "output"),
                )
        raise RuntimeError("ComfyUI produced no image output")

    @staticmethod
    def _resolve_dimensions(request: ImageGenerationRequest) -> tuple[int, int]:
        """Resolve aspect_ratio to pixel dimensions."""
        if request.image_size:
            try:
                w, h = request.image_size.split("x")
                return int(w), int(h)
            except (ValueError, AttributeError):
                pass
        ratio_map = {
            "9:16": (576, 1024),
            "16:9": (1024, 576),
            "1:1": (1024, 1024),
            "4:3": (768, 1024),
            "3:4": (1024, 768),
        }
        return ratio_map.get(request.aspect_ratio, (512, 768))
