"""Voice capability settings for reference-video rendering: model tier, episode toggle, count limits, and backend constraints.

All rendering layers (parse preview / three-part rendering / execution assembly) use the same voice inputs,
passed together unchanged through the call chain. A value object means adding voice capability fields changes
only this module, avoiding edits to every intermediate signature and call.

Silent detection (:attr:`VoiceRenderSettings.is_silent`) lives here too: models without audio and episodes with
audio disabled have equivalent product semantics; one property prevents duplicate divergent decisions.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.generation.video_request_facts import VideoRequestFacts


@dataclass(frozen=True)
class VoiceRenderSettings:
    """Voice inputs for one render.

    ``voice_consistency`` is the server-derived model tier (``native`` / ``soft`` / ``none``):
    only ``native`` (class A native audio reference) supports audio binding; ``none`` (class C truly silent) emits no audio.

    ``requested_generate_audio`` is the episode audio toggle (user intent, not pricing semantics).

    ``max_reference_audio`` is the effective audio-segment limit per request (``None`` means unrecorded, no truncation);
    ``provider_id`` and ``model_id`` only resolve display names for degradation warnings.

    ``audio_ready`` is the set of characters with actually usable audio: parse previews avoid filesystems and use None
    to check nonempty asset ``reference_audio``; execution supplies names for resolved, existing files.

    True ``requires_reference_image`` means the backend binds each audio segment to specific reference material
    (such as wan2.7-r2v), so offscreen-only speakers receive no audio binding.
    """

    voice_consistency: str = "soft"
    requested_generate_audio: bool = True
    max_reference_audio: int | None = 0
    provider_id: str = ""
    model_id: str = ""
    audio_ready: Collection[str] | None = None
    requires_reference_image: bool = False

    @classmethod
    def from_caps(cls, caps: Mapping[str, Any], *, audio_ready: Collection[str] | None = None) -> VoiceRenderSettings:
        """Construct from a resolved video-capability dict.

        On resolution failure callers pass empty dicts, using field defaults (``soft`` and no reference audio),
        merely omitting some voice guidance without blocking rendering. The episode toggle defaults ``True``:
        render with audio absent a disable signal. Capability failure must not silently mute an episode; callers
        separately resolve explicit disable intent into capabilities (``server/services/tasks/video_caps.py``).
        Capability-key/field mappings live only here, preventing parse-preview/SDK-splitter callers from
        omitting different bits and reporting contradictory voice conclusions.
        """
        return cls(
            voice_consistency=str(caps.get("voice_consistency") or "soft"),
            requested_generate_audio=bool(caps.get("requested_generate_audio", True)),
            max_reference_audio=_audio_count(caps.get("max_reference_audio_count", 0)),
            provider_id=str(caps.get("provider_id") or ""),
            model_id=str(caps.get("model") or ""),
            audio_ready=audio_ready,
            requires_reference_image=bool(caps.get("reference_audio_per_image") or False),
        )

    @classmethod
    def from_request_facts(
        cls, facts: VideoRequestFacts, *, audio_ready: Collection[str] | None = None
    ) -> VoiceRenderSettings:
        """Construct from video-request facts: voice settings and duration tiers share one evaluation."""
        return cls(
            voice_consistency=facts.voice_consistency,
            requested_generate_audio=facts.requested_generate_audio,
            max_reference_audio=facts.max_reference_audio_count,
            provider_id=facts.provider_id,
            model_id=facts.model_id,
            audio_ready=audio_ready,
            requires_reference_image=facts.reference_audio_per_image,
        )

    @property
    def is_silent(self) -> bool:
        """Whether this episode is inaudible: model emits no audio (class C) or episode audio is disabled.

        Omit voice-style descriptions too: although prompt text rather than audio payload, they describe audible
        timbres and waste model attention in silent clips. Dialogue does not follow this flag: silent videos
        still receive dialogue text, which providers may use for lip-sync.
        """
        return self.voice_consistency == "none" or not self.requested_generate_audio


def _audio_count(value: object) -> int | None:
    """Reference-audio limit from capabilities: ``None`` means unrecorded/no truncation; otherwise parse int, falling back zero."""
    if value is None:
        return None
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0
