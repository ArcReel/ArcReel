"""MiniMax voice design client for the synchronous ``/v1/voice_design`` operation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from lib.minimax_shared import (
    minimax_headers,
    minimax_voice_base_url,
    resolve_minimax_api_key,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import with_retry_async
from lib.video_backends.base import should_retry_submit, submit_post

logger = logging.getLogger(__name__)

_VOICE_DESIGN_ENDPOINT = "/voice_design"


@dataclass(frozen=True)
class VoiceDesignRequest:
    """Inputs accepted by the MiniMax voice design operation."""

    prompt: str
    voice_id: str


@dataclass(frozen=True)
class VoiceDesignResult:
    """The voice identifier confirmed by MiniMax."""

    provider: str
    voice_id: str


def extract_voice_design_id(payload: object) -> str:
    """Return ``voice_id`` from a successful response and surface business errors."""
    if not isinstance(payload, dict):
        raise RuntimeError("MiniMax voice design response must be an object")

    base_resp = payload.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code")
        if status_code is not None and status_code != 0:
            status_msg = base_resp.get("status_msg") or ""
            raise RuntimeError(f"MiniMax voice design failed status_code={status_code}: {status_msg}".strip())

    voice_id = payload.get("voice_id")
    if not isinstance(voice_id, str) or not voice_id.strip():
        raise RuntimeError("MiniMax voice design response is missing voice_id")
    return voice_id


class MiniMaxVoiceDesignClient:
    """Create reusable voice identifiers from natural-language voice prompts."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_voice_base_url(base_url)
        self._http_timeout = http_timeout

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResult:
        prompt = request.prompt.strip()
        voice_id = request.voice_id.strip()
        if not prompt:
            raise ValueError("voice design prompt must not be blank")
        if not voice_id:
            raise ValueError("voice design voice_id must not be blank")

        payload = {"prompt": prompt, "voice_id": voice_id}
        response = await self._submit(payload)
        return VoiceDesignResult(
            provider=PROVIDER_MINIMAX,
            voice_id=extract_voice_design_id(response),
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict[str, str]) -> object:
        logger.info(
            "Calling MiniMax voice design API voice_id=%s prompt_chars=%d",
            payload["voice_id"],
            len(payload["prompt"]),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            response = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_VOICE_DESIGN_ENDPOINT}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            return response.json()
