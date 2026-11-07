"""Character voice binding: a project setting determining how voices are constrained.

``prompt`` (default) only inserts the character's ``voice_style`` into prompts as
a soft constraint. ``reference_audio`` attaches the configured reference audio to
video-model requests for native voice consistency across clips. Both paths are
resolved into a ``voice_consistency`` tier by
:func:`lib.config.resolver.derive_voice_consistency`; rendering, script preview
and execution-time attachment read that tier. This module only reads and
normalizes values, without repeating the decision.

This leaf module has no internal lib dependencies and is shared by lib.config,
higher layers and the server.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast, get_args

#: Character voice binding. Keep values synchronized with frontend ``CharacterVoiceBinding``.
CharacterVoiceBinding = Literal["prompt", "reference_audio"]

#: Default: soft prompt constraint. Reference audio is optional and requires explicit user selection.
DEFAULT_CHARACTER_VOICE_BINDING: CharacterVoiceBinding = "prompt"

VALID_CHARACTER_VOICE_BINDINGS: frozenset[str] = frozenset(get_args(CharacterVoiceBinding))

PROJECT_FIELD = "character_voice_binding"


def character_voice_binding(project: Mapping[str, object] | None) -> CharacterVoiceBinding | None:
    """Return the project's declared binding, or None without project context.

    Use None rather than a default tier, matching
    :func:`lib.config.resolver.caps_generation_mode`: context-free queries such as
    the model catalog must not imply the user explicitly selected a binding.

    If a manually edited project.json contains an invalid value, use the default
    prompt constraint. This fallback only omits reference audio, which is safer
    than interpreting an unrecognized value as reference-audio binding.
    """
    if project is None:
        return None
    raw = project.get(PROJECT_FIELD)
    if isinstance(raw, str) and raw in VALID_CHARACTER_VOICE_BINDINGS:
        return cast(CharacterVoiceBinding, raw)
    return DEFAULT_CHARACTER_VOICE_BINDING


__all__ = [
    "DEFAULT_CHARACTER_VOICE_BINDING",
    "PROJECT_FIELD",
    "VALID_CHARACTER_VOICE_BINDINGS",
    "CharacterVoiceBinding",
    "character_voice_binding",
]
