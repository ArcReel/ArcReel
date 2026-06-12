"""Audio generation service layer — core interface definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class AudioGenerationRequest:
    """Generic audio generation request. Backends ignore unsupported fields."""

    prompt: str
    output_path: Path
    duration_seconds: float = 5.0
    negative_prompt: str = ""
    seed: int | None = None


@dataclass
class AudioGenerationResult:
    """Generic audio generation result."""

    audio_path: Path
    provider: str
    model: str
    duration_seconds: float


class AudioBackend(Protocol):
    """Audio generation backend protocol."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def generate(self, request: AudioGenerationRequest) -> AudioGenerationResult: ...
