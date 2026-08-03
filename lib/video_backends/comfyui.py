"""ComfyUI workflow-backed video generation for local or private-network workers."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import anyio
import httpx

from lib.comfyui_workflow import (
    bind_scalar_inputs,
    bind_uploaded_image,
    comfyui_headers,
    extract_video_output,
    normalize_comfyui_base_url,
    validate_comfyui_endpoint_config,
)
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    ProviderJobIdPersistenceMixin,
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    submit_post,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3.0
_MIN_POLL_TIMEOUT_SECONDS = 3600.0
_POLL_TIMEOUT_PER_VIDEO_SECOND = 300.0


class ComfyUIVideoBackend(ProviderJobIdPersistenceMixin):
    """Execute one persisted ComfyUI API-format workflow as a video model."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        endpoint_config: object,
        api_key: str | None = None,
        provider_name: str = "comfyui",
        http_timeout: float = 60.0,
    ) -> None:
        self._root = normalize_comfyui_base_url(base_url)
        self._model = model
        self._provider_name = provider_name
        self._config = validate_comfyui_endpoint_config(endpoint_config)
        self._headers = comfyui_headers(api_key)
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        del model
        # ArcReel's storyboard path always supplies a first frame. Tail-frame support is
        # opt-in per discovered workflow through capability_overrides.
        return VideoCapabilities(first_frame=True, last_frame=False, max_reference_images=0)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        bindings = self._config["bindings"]
        return VideoCapabilities(
            first_frame="start_image" in bindings,
            last_frame="end_image" in bindings,
            max_reference_images=0,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        client_id = str(uuid.uuid4())
        output_prefix = self._output_prefix(request, client_id)
        workflow = bind_scalar_inputs(
            self._config,
            prompt=request.prompt,
            duration_seconds=request.duration_seconds,
            aspect_ratio=request.aspect_ratio,
            seed=request.seed,
            output_prefix=output_prefix,
        )

        async with httpx.AsyncClient(timeout=self._http_timeout, headers=self._headers) as client:
            await self._bind_request_images(client, workflow, request)
            prompt_id = await self._submit(client, workflow, client_id)
            await self._persist_provider_job_id(request, prompt_id, provider=self._provider_name)
            return await self._poll_download_and_build(client, prompt_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        async with httpx.AsyncClient(timeout=self._http_timeout, headers=self._headers) as client:
            history = await self._history(client, job_id)
            if history is None and not await self._queue_contains(client, job_id):
                raise ResumeExpiredError(job_id=job_id, provider=self._provider_name)
            return await self._poll_download_and_build(client, job_id, request, is_resume=True)

    async def _bind_request_images(
        self,
        client: httpx.AsyncClient,
        workflow: dict[str, Any],
        request: VideoGenerationRequest,
    ) -> None:
        bindings = self._config["bindings"]
        if request.start_image is not None:
            binding = bindings.get("start_image")
            if not isinstance(binding, dict):
                raise ValueError(f"ComfyUI workflow {self._model} does not expose a first-frame input")
            uploaded = await self._upload_image(client, Path(request.start_image), request, "start")
            bind_uploaded_image(workflow, binding, uploaded, loader_id="arcreel_start_image")
        if request.end_image is not None:
            binding = bindings.get("end_image")
            if not isinstance(binding, dict):
                raise ValueError(f"ComfyUI workflow {self._model} does not expose a last-frame input")
            uploaded = await self._upload_image(client, Path(request.end_image), request, "end")
            bind_uploaded_image(workflow, binding, uploaded, loader_id="arcreel_end_image")
        if request.reference_images:
            raise ValueError(f"ComfyUI workflow {self._model} does not expose reference image slots")

    async def _upload_image(
        self,
        client: httpx.AsyncClient,
        path: Path,
        request: VideoGenerationRequest,
        role: str,
    ) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)
        subfolder = self._input_subfolder(request)
        mime = IMAGE_MIME_TYPES.get(path.suffix.lower(), "image/png")
        data = await asyncio.to_thread(path.read_bytes)
        response = await client.post(
            f"{self._root}/upload/image",
            data={"type": "input", "subfolder": subfolder, "overwrite": "true"},
            files={"image": (f"{role}_{path.name}", data, mime)},
        )
        response.raise_for_status()
        payload = response.json()
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError(f"ComfyUI upload response has no file name: {payload}")
        returned_subfolder = payload.get("subfolder")
        if isinstance(returned_subfolder, str) and returned_subfolder:
            return str(PurePosixPath(returned_subfolder) / name)
        return name

    async def _submit(self, client: httpx.AsyncClient, workflow: dict[str, Any], client_id: str) -> str:
        response = await submit_post(
            lambda: client.post(f"{self._root}/prompt", json={"prompt": workflow, "client_id": client_id}),
            provider=self._provider_name,
        )
        payload = response.json()
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            node_errors = payload.get("node_errors") or payload.get("error") or payload
            raise RuntimeError(f"ComfyUI rejected workflow: {node_errors}")
        logger.info("ComfyUI workflow submitted: provider=%s model=%s prompt_id=%s", self.name, self.model, prompt_id)
        return prompt_id

    async def _history(self, client: httpx.AsyncClient, prompt_id: str) -> dict[str, Any] | None:
        response = await client.get(f"{self._root}/history/{prompt_id}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("ComfyUI history response is not an object")
        record = payload.get(prompt_id)
        return record if isinstance(record, dict) else None

    async def _queue_contains(self, client: httpx.AsyncClient, prompt_id: str) -> bool:
        response = await client.get(f"{self._root}/queue")
        response.raise_for_status()

        def _contains(value: object) -> bool:
            if value == prompt_id:
                return True
            if isinstance(value, dict):
                return any(_contains(nested) for nested in value.values())
            if isinstance(value, list):
                return any(_contains(nested) for nested in value)
            return False

        return _contains(response.json())

    async def _poll_download_and_build(
        self,
        client: httpx.AsyncClient,
        prompt_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        async def _poll() -> dict[str, Any] | None:
            try:
                return await self._history(client, prompt_id)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=prompt_id, provider=self._provider_name) from exc
                raise

        record = await poll_with_retry(
            poll_fn=_poll,
            is_done=lambda value: isinstance(value, dict) and self._history_completed(value),
            is_failed=self._history_failure,
            poll_interval=_POLL_INTERVAL_SECONDS,
            max_wait=max(_MIN_POLL_TIMEOUT_SECONDS, request.duration_seconds * _POLL_TIMEOUT_PER_VIDEO_SECOND),
            retry_if=should_retry_poll,
            label="ComfyUI",
        )
        assert isinstance(record, dict)
        output_binding = self._config["bindings"]["output"]
        descriptor = extract_video_output(record, str(output_binding["node_id"]))
        await self._download_output(client, descriptor, request.output_path)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider_name,
            model=self._model,
            duration_seconds=request.duration_seconds,
            # The ComfyUI URL may only be reachable from ArcReel's server-side tailnet.  ArcReel
            # has already downloaded the durable local copy, so do not leak that private URL to
            # a browser that may not share the same network route.
            video_uri=None,
            seed=request.seed,
            task_id=prompt_id,
            generate_audio=request.generate_audio,
        )

    @staticmethod
    def _history_completed(record: dict[str, Any]) -> bool:
        status = record.get("status")
        if isinstance(status, dict) and status.get("completed") is True:
            return True
        return isinstance(record.get("outputs"), dict) and bool(record["outputs"])

    @staticmethod
    def _history_failure(record: object) -> str | None:
        if not isinstance(record, dict):
            return None
        status = record.get("status")
        if not isinstance(status, dict):
            return None
        status_str = str(status.get("status_str", "")).lower()
        if status_str not in {"error", "failed"}:
            return None
        messages = status.get("messages")
        detail = str(messages[-1] if isinstance(messages, list) and messages else status)
        return f"ComfyUI workflow failed: {detail[:1000]}"

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_output(
        self,
        client: httpx.AsyncClient,
        descriptor: dict[str, str],
        output_path: Path,
    ) -> None:
        await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
        partial = output_path.with_name(f"{output_path.name}.part")
        try:
            async with client.stream(
                "GET",
                f"{self._root}/view",
                params={
                    "filename": descriptor["filename"],
                    "subfolder": descriptor["subfolder"],
                    "type": descriptor["type"],
                },
                timeout=None,
            ) as response:
                response.raise_for_status()
                async with await anyio.open_file(partial, "wb") as handle:
                    async for chunk in response.aiter_bytes():
                        await handle.write(chunk)
            await asyncio.to_thread(os.replace, partial, output_path)
        except BaseException:
            await asyncio.to_thread(partial.unlink, missing_ok=True)
            raise

    @staticmethod
    def _input_subfolder(request: VideoGenerationRequest) -> str:
        project = _safe_segment(request.project_name or "project")
        task = _safe_segment(request.task_id or uuid.uuid4().hex)
        return str(PurePosixPath("arcreel") / project / task)

    @staticmethod
    def _output_prefix(request: VideoGenerationRequest, fallback: str) -> str:
        project = _safe_segment(request.project_name or "project")
        task = _safe_segment(request.task_id or fallback)
        return str(PurePosixPath("arcreel") / project / task)


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    return cleaned[:96] or "item"
