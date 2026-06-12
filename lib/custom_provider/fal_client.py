"""fal.ai REST API client — queue-based async inference."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# fal.ai uses two base URLs:
# - https://queue.fal.run  — async queue (recommended)
# - https://api.fal.ai      — platform API / sync
_QUEUE_BASE = "https://queue.fal.run"

_POLL_INTERVAL = 3.0
_MAX_POLL_ATTEMPTS = 200  # ~10 min at 3s interval


class FalClient:
    """Minimal fal.ai REST client using their queue-based inference API."""

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base = (base_url or _QUEUE_BASE).rstrip("/")
        self._headers = {
            "Authorization": f"Key {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def run(self, model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit inference request and poll until result is ready.

        Args:
            model_id: e.g. "fal-ai/flux-pro", "fal-ai/kling-video-v2-pro"
            payload: model-specific input parameters

        Returns:
            The result dict from fal.ai
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Submit
            submit_url = f"{self._base}/{model_id}"
            logger.info("fal.ai submit: POST %s", submit_url)
            resp = await client.post(submit_url, json=payload, headers=self._headers)
            resp.raise_for_status()
            submit_data = resp.json()

            request_id = submit_data.get("request_id")
            if not request_id:
                # Synchronous response — result already in body
                return submit_data

            status_url = submit_data.get("status_url", f"{self._base}/{model_id}/requests/{request_id}/status")
            result_url = submit_data.get("response_url", f"{self._base}/{model_id}/requests/{request_id}")

            # Step 2: Poll
            for _ in range(_MAX_POLL_ATTEMPTS):
                await asyncio.sleep(_POLL_INTERVAL)
                status_resp = await client.get(status_url, headers=self._headers)
                status_resp.raise_for_status()
                status_data = status_resp.json()
                status = status_data.get("status", "")

                if status == "COMPLETED":
                    break
                if status in ("FAILED", "CANCELLED"):
                    error_detail = status_data.get("error", status_data.get("detail", "unknown error"))
                    raise RuntimeError(f"fal.ai inference failed: {error_detail}")
            else:
                raise TimeoutError(f"fal.ai inference timed out after {_MAX_POLL_ATTEMPTS * _POLL_INTERVAL}s")

            # Step 3: Get result
            result_resp = await client.get(result_url, headers=self._headers)
            result_resp.raise_for_status()
            return result_resp.json()
