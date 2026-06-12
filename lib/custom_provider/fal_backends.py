"""fal.ai image, video, and audio backends — wraps the queue-based fal.ai REST API."""

from __future__ import annotations

import logging

from lib.audio_backends.base import AudioBackend, AudioGenerationRequest, AudioGenerationResult
from lib.custom_provider.fal_client import FalClient
from lib.image_backends.base import (
    ImageBackend,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.video_backends.base import (
    VideoBackend,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)


class FalImageBackend(ImageBackend):
    """fal.ai image generation backend (T2I + I2I)."""

    # Models known to support image-to-image
    _I2I_MODELS = {"kontext", "edit", "image-to-image", "i2i"}

    def __init__(self, *, api_key: str, base_url: str | None, model: str, provider_id: str) -> None:
        self._client = FalClient(api_key, base_url)
        self._model = model
        self._provider_id = provider_id

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        caps = {ImageCapability.TEXT_TO_IMAGE}
        if any(tag in self._model.lower() for tag in self._I2I_MODELS):
            caps.add(ImageCapability.IMAGE_TO_IMAGE)
        return caps

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        payload: dict = {"prompt": request.prompt}
        if request.image_size:
            payload["image_size"] = request.image_size

        # I2I: pass reference image URL to fal.ai
        if request.reference_images:
            ref = request.reference_images[0]
            ref_path = ref.path if hasattr(ref, "path") else str(ref)
            # fal.ai expects image_url for I2I models
            if ref_path.startswith(("http://", "https://")):
                payload["image_url"] = ref_path
            else:
                # Upload local file to get a URL, or read as base64
                import base64
                from pathlib import Path

                p = Path(ref_path)
                if p.exists():
                    b64 = base64.b64encode(p.read_bytes()).decode()
                    ext = p.suffix.lower().lstrip(".")
                    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(
                        ext, "image/png"
                    )
                    payload["image_url"] = f"data:{mime};base64,{b64}"

        logger.info(
            "fal.ai image: model=%s, prompt=%s, i2i=%s",
            self._model,
            request.prompt[:80],
            bool(request.reference_images),
        )
        result = await self._client.run(self._model, payload)

        # fal.ai returns image URLs in result
        images = result.get("images", result.get("output", {}).get("images", []))
        if not images:
            raise RuntimeError(f"fal.ai returned no images: {result}")

        image_url = images[0].get("url") if isinstance(images[0], dict) else images[0]
        if not image_url:
            raise RuntimeError(f"fal.ai image URL not found in: {images[0]}")

        # Download image
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(img_resp.content)

        logger.info("fal.ai image saved: %s", request.output_path)
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider_id,
            model=self._model,
        )


class FalVideoBackend(VideoBackend):
    """fal.ai video generation backend."""

    def __init__(self, *, api_key: str, base_url: str | None, model: str, provider_id: str) -> None:
        self._client = FalClient(api_key, base_url)
        self._model = model
        self._provider_id = provider_id

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return {VideoCapability.TEXT_TO_VIDEO}

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(reference_images=False)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload: dict = {"prompt": request.prompt}
        if request.duration_seconds:
            payload["duration"] = request.duration_seconds

        logger.info("fal.ai video: model=%s, prompt=%s", self._model, request.prompt[:80])
        result = await self._client.run(self._model, payload)

        # fal.ai returns video URL in result
        video_data = result.get("video", result.get("output", {}).get("video", {}))
        video_url = video_data.get("url") if isinstance(video_data, dict) else video_data
        if not video_url:
            # Try alternative response formats
            video_url = result.get("url") or result.get("output", {}).get("url")
        if not video_url:
            raise RuntimeError(f"fal.ai video URL not found in: {result}")

        # Download video
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as client:
            vid_resp = await client.get(video_url)
            vid_resp.raise_for_status()
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(vid_resp.content)

        logger.info("fal.ai video saved: %s", request.output_path)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider_id,
            model=self._model,
            duration_seconds=request.duration_seconds,
        )


class FalAudioBackend(AudioBackend):
    """fal.ai audio generation backend (TTS, music, sound effects)."""

    def __init__(self, *, api_key: str, base_url: str | None, model: str, provider_id: str) -> None:
        self._client = FalClient(api_key, base_url)
        self._model = model
        self._provider_id = provider_id

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, request: AudioGenerationRequest) -> AudioGenerationResult:
        # Build payload — different fal.ai audio models expect different fields
        payload: dict = {}

        # TTS models (elevenlabs, minimax, chatterbox) use "text"
        if "tts" in self._model or "speech" in self._model or "chatterbox" in self._model:
            payload["text"] = request.prompt
        # Music models (ace-step) use "tags" + "lyrics"
        elif "ace-step" in self._model or "ace_step" in self._model:
            payload["tags"] = request.prompt
            payload["duration"] = request.duration_seconds
        # Generic fallback — send as "prompt"
        else:
            payload["prompt"] = request.prompt
            if request.duration_seconds:
                payload["duration"] = request.duration_seconds

        logger.info("fal.ai audio: model=%s, prompt=%s", self._model, request.prompt[:80])
        result = await self._client.run(self._model, payload)

        # fal.ai returns audio URL in result["audio"]["url"] or result["audio"]["url"]
        audio_data = result.get("audio", {})
        audio_url = audio_data.get("url") if isinstance(audio_data, dict) else None
        if not audio_url:
            # Try alternative response formats
            audio_url = result.get("url") or result.get("output", {}).get("audio", {}).get("url")
        if not audio_url:
            raise RuntimeError(f"fal.ai audio URL not found in: {result}")

        # Download audio
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as client:
            audio_resp = await client.get(audio_url)
            audio_resp.raise_for_status()
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(audio_resp.content)

        logger.info("fal.ai audio saved: %s", request.output_path)
        return AudioGenerationResult(
            audio_path=request.output_path,
            provider=self._provider_id,
            model=self._model,
            duration_seconds=request.duration_seconds,
        )
