"""ComfyUI HTTP client — async wrapper for ComfyUI's REST API."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import httpx

from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_MAX_POLL_WAIT = 600.0


class ComfyUIClient:
    """Async client for ComfyUI's REST API."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client_id = uuid.uuid4().hex[:12]

    @property
    def base_url(self) -> str:
        return self._base

    async def get_system_stats(self) -> dict[str, Any]:
        resp = await self._get("/system_stats")
        resp.raise_for_status()
        return resp.json()

    async def get_models(self, folder: str) -> list[str]:
        """List model files in a ComfyUI model folder (e.g. 'checkpoints', 'diffusion_models')."""
        resp = await self._get(f"/models/{folder}")
        resp.raise_for_status()
        return resp.json()

    async def get_object_info(self, node_class: str | None = None) -> dict[str, Any]:
        path = f"/object_info/{node_class}" if node_class else "/object_info"
        resp = await self._get(path)
        resp.raise_for_status()
        return resp.json()

    async def queue_prompt(self, workflow: dict[str, Any]) -> str:
        """Submit a workflow and return the prompt_id."""
        payload = {"prompt": workflow, "client_id": self._client_id}
        resp = await self._post("/prompt", json=payload)
        resp.raise_for_status()
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI /prompt returned no prompt_id: {data}")
        logger.info("ComfyUI prompt queued: %s", prompt_id)
        return prompt_id

    async def get_history(self, prompt_id: str) -> dict[str, Any]:
        resp = await self._get(f"/history/{prompt_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_queue(self) -> dict[str, Any]:
        resp = await self._get("/queue")
        resp.raise_for_status()
        return resp.json()

    async def interrupt(self) -> None:
        resp = await self._post("/interrupt")
        resp.raise_for_status()

    async def download_output(
        self,
        filename: str,
        output_path: Path,
        subfolder: str = "",
        file_type: str = "output",
    ) -> Path:
        """Download a generated file from ComfyUI's /view endpoint."""
        params = {"filename": filename, "type": file_type}
        if subfolder:
            params["subfolder"] = subfolder
        resp = await self._get("/view", params=params)
        resp.raise_for_status()

        def _save() -> None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)

        import asyncio

        await asyncio.to_thread(_save)
        logger.info("ComfyUI output downloaded: %s", output_path)
        return output_path

    async def wait_for_completion(
        self,
        prompt_id: str,
        *,
        poll_interval: float = _POLL_INTERVAL,
        max_wait: float = _MAX_POLL_WAIT,
    ) -> dict[str, Any]:
        """Poll /history until the prompt completes. Returns the output dict."""
        import asyncio
        import time

        start = time.monotonic()
        while True:
            history = await self.get_history(prompt_id)
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("completed", False) or status.get("status_str") == "success":
                    return entry.get("outputs", {})
                if status.get("status_str") == "error":
                    msgs = status.get("messages", [])
                    raise RuntimeError(f"ComfyUI generation failed: {msgs}")
            elapsed = time.monotonic() - start
            if elapsed >= max_wait:
                raise TimeoutError(f"ComfyUI generation timed out after {max_wait:.0f}s")
            await asyncio.sleep(poll_interval)

    # ── internal helpers ──────────────────────────────────────────

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        client = get_http_client()
        return await client.get(f"{self._base}{path}", **kwargs)

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        client = get_http_client()
        return await client.post(f"{self._base}{path}", **kwargs)
