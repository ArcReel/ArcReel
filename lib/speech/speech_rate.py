"""Single source of truth for speech-rate estimates.

Centralize how long spoken text takes to read for duration suggestions, episode
size conversions and checks that speech fits within scene duration. This avoids
drifting per-caller rate constants. Subtitle timing does not use speech rate.

Rates are reading units per second. Reuse the language-specific units from
``lib.infra.text_metrics`` (Chinese characters / CJK punctuation for zh; words
for en / vi / ru), so units follow the language without separate character rules.
Rates are adjustable, overridden by ``source_language``, and otherwise fall back
to defaults. Register new languages only in ``SPEECH_RATE_UPS_BY_LANGUAGE``;
callers must not hardcode rates.

Precedence: project override (top-level ``speech_rate_units_per_second`` in
``project.json``, parsed by ``project_speech_rate_override``) > language default >
global default. Apply overrides only here; consumers use this module's two
functions instead of independently reading the project.json field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lib.infra.text_metrics import count_reading_units

#: Default reading units per second: intelligible Chinese speech commonly uses 4-6 characters/s; use 5.
#: Missing/unregistered languages use this value, matching the counter's zh fallback.
DEFAULT_SPEECH_RATE_UPS: float = 5.0

#: Rate overrides keyed by project ``source_language`` (zh / en / vi / ru).
#: en / vi / ru use words, commonly 2-3 per second; use 2.5. These are not directly
#: comparable with zh characters/s, so register rates by language. Adjust estimates to actual speech.
SPEECH_RATE_UPS_BY_LANGUAGE: dict[str, float] = {
    "zh": 5.0,
    "en": 2.5,
    "vi": 2.5,
    "ru": 2.5,
}


#: Top-level project.json override field, measured in reading units per second.
#: Unlike the TTS provider's ``narration_speed`` multiplier, this is an absolute rate.
SPEECH_RATE_FIELD: str = "speech_rate_units_per_second"

#: Inclusive override bounds in reading units/s. The wide range rejects obvious mistakes
#: such as TTS multipliers or milliseconds; no advisory preference is imposed within it.
#:
#: The lower bound also leaves headroom for downstream times: a hypothetical 1e6-unit passage
#: (far longer than real speech) at 0.001 units/s takes 1e9 seconds, or 1e15 Jianying microseconds,
#: still three orders below int64's limit. Merely requiring a finite estimate would admit rates
#: around 1e-302; downstream multipliers such as microsecond conversion could then produce inf
#: or integer overflow. Protect headroom, not just finiteness. 0.001 is 1000 seconds per unit, far slower than real speech.
MIN_SPEECH_RATE_UPS: float = 0.001
MAX_SPEECH_RATE_UPS: float = 20.0


def is_valid_speech_rate(value: float) -> bool:
    """Whether the value is in the project override range (``0.001 <= value <= 20``).

    Frontend validation, request models and persisted-data read guards share these
    bounds. Inputs may be values parsed directly from project.json, so handle two
    pathological cases here: JSON integers have no bit-width limit and ``float()``
    raises ``OverflowError`` beyond double precision's range; JSON booleans are
    ``int`` subclasses and ``float(True)`` would yield a valid ``1.0``. Treat both as
    unusable. The bounds also exclude ``inf`` and ``nan`` (their range comparisons
    are false) and leave room for downstream duration conversions; see
    ``MIN_SPEECH_RATE_UPS``.
    """
    if isinstance(value, bool):
        return False
    try:
        rate = float(value)
    except OverflowError:
        return False
    return MIN_SPEECH_RATE_UPS <= rate <= MAX_SPEECH_RATE_UPS


def project_speech_rate_override(project: Mapping[str, Any] | None) -> float | None:
    """Parse a project.json speech-rate override; return ``None`` for absent, invalid or out-of-range values.

    ``None`` means no override: callers pass it unchanged to the two functions below
    to use the language default. Projects without this field use that default.
    Create/PATCH request models reject out-of-range writes using the same bounds.
    This read guard covers manually edited project.json and invalid historical data:
    a bad estimate field should not crash a generation that has already been paid for.
    """
    if not isinstance(project, Mapping):
        return None
    raw = project.get(SPEECH_RATE_FIELD)
    # bool subclasses int: exclude it so invalid True does not lower the rate to 1 unit/s.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    # Validate before float conversion; valid values are safe and huge integers fail the range check.
    return float(raw) if is_valid_speech_rate(raw) else None


def speech_rate_units_per_second(language: str | None = None, override: float | None = None) -> float:
    """Return reading units per second: use a valid ``override``, otherwise the language default.

    The project override is parsed by ``project_speech_rate_override``. Out-of-range
    or non-finite values mean no override. Language codes are case-insensitive;
    ``None``, empty and unregistered languages use ``DEFAULT_SPEECH_RATE_UPS``.
    """
    if override is not None and is_valid_speech_rate(override):
        return float(override)
    if not language:
        return DEFAULT_SPEECH_RATE_UPS
    return SPEECH_RATE_UPS_BY_LANGUAGE.get(language.strip().lower(), DEFAULT_SPEECH_RATE_UPS)


def estimate_spoken_seconds(text: str | None, language: str | None = None, override: float | None = None) -> float:
    """Estimate the seconds needed to read ``text`` in ``language``.

    Divide reading units by speech rate (see ``lib.infra.text_metrics.count_reading_units``
    and ``speech_rate_units_per_second`` for units and precedence). None, empty,
    whitespace-only and punctuation-only text with no reading units take 0 seconds.
    This is a term in speech-duration sums; all callers share the same conversion.
    """
    if not text:
        return 0.0
    units = count_reading_units(text, language)
    if units <= 0:
        return 0.0
    return units / speech_rate_units_per_second(language, override)
