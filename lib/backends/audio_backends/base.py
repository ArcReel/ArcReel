"""Core interfaces for speech-synthesis (TTS) services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class AudioCapability(StrEnum):
    """Capabilities supported by audio backends."""

    TEXT_TO_SPEECH = "text_to_speech"
    #: Send synthesis ``speed`` to providers. Undeclared backends ignore rate, so project narration-speed settings have no effect.
    SPEECH_SPEED = "speech_speed"


@dataclass(frozen=True)
class VoiceOption:
    """Voice catalog entry: ``id`` is the synthesis ``voice`` parameter, ``label`` the human-readable display name."""

    id: str
    label: str


@dataclass
class AudioSynthesisRequest:
    """Common synthesis request; backends ignore unsupported fields."""

    text: str
    output_path: Path
    voice: str
    language_type: str = "Chinese"
    # Rates affect only backends declaring ``AudioCapability.SPEECH_SPEED``; synchronous qwen3-tts-flash lacks support
    # because speech_rate exists only in realtime WebSocket variants. Backends log debug messages and ignore it.
    speed: float | None = None


@dataclass
class AudioSynthesisResult:
    """Common synthesis result.

    Report usage through the usage contract (docs/adr/0100). ``text`` is verbatim provider input;
    character-pricing calculators use the price table's Chinese-character-counts-as-two attribute.
    Token-priced models additionally report ``input_tokens`` and ``audio_output_tokens``,
    ``None`` when providers do not report them.
    """

    provider: str
    model: str
    text: str
    output_path: Path
    input_tokens: int | None = None
    audio_output_tokens: int | None = None


class AudioBackend(Protocol):
    """Speech-synthesis backend protocol."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> set[AudioCapability]: ...

    def list_voices(self) -> list[VoiceOption]: ...

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult: ...
