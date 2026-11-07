"""Effective planning target volume: explicit ``episode_target_units``, otherwise episode-duration conversion.

Episode planning chooses source volume; script planning chooses units within split episodes. Converge project settings
into effective volumes:

- Explicit ``episode_target_units`` wins, already user-specified volume requiring no conversion;
- Otherwise non-``None`` ``episode_target_duration`` converts seconds to reading units using
  :mod:`lib.speech.speech_rate`, shared with script speech estimates including project overrides,
  never independent rates;
- Neither returns ``None``, preserving caller short-video pacing judgment.

Always expose ``source`` so prompts/tool evidence identify duration conversion.
Estimated rates make converted values less authoritative than explicit user values, requiring Agent differentiation
rather than treating them as hard requirements. Drama source is not verbatim speech, containing narration while
outputs speak dialogue only; same formulas are rough, always soft/varying targets,
never precise equivalence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from lib.episode.episode_target_duration import project_episode_target_duration
from lib.infra.text_metrics import reading_unit_noun
from lib.speech.speech_rate import project_speech_rate_override, speech_rate_units_per_second

#: Top-level ``project.json`` reading-volume field interpreted by ``source_language``.
#: Hardcoded also in ``manage-project`` ``patch_project`` allowlist instructions, a machine contract,
#: guarded by mirror tests.
EPISODE_TARGET_UNITS_FIELD: str = "episode_target_units"


def project_episode_target_units(project: Mapping[str, Any] | None) -> int | None:
    """Read explicit episode volumes; absent/dirty/<1 → ``None``.

    Reading units require ``int``. Exclude ``bool`` despite ``int`` inheritance, preventing
    ``true`` becoming one character per episode. Writes through ``patch_project`` positive-integer allowlists already
    reject invalids; guards cover manual project.json edits/historical corruption.
    """
    if not isinstance(project, Mapping):
        return None
    raw = project.get(EPISODE_TARGET_UNITS_FIELD)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw >= 1 else None


@dataclass(frozen=True)
class EpisodeTargetVolume:
    """Effective per-episode volumes/provenance.

    ``source`` = ``"units"`` means explicit ``episode_target_units``, with ``seconds`` /
    ``units_per_second`` both ``None``. ``"duration"`` means converted ``units``; those two fields
    retain seconds/rates for explaining conversions.
    """

    units: int
    unit_noun: str
    source: Literal["units", "duration"]
    seconds: int | None = None
    units_per_second: float | None = None


def resolve_episode_target_volume(
    project: Mapping[str, Any] | None,
    *,
    language: str | None,
) -> EpisodeTargetVolume | None:
    """Resolve volumes: explicit > converted durations > ``None``.

    ``language`` is project ``source_language``, choosing unit nouns/conversion rates, with missing/unknown
    language fallback through :mod:`lib.speech.speech_rate`. Round target seconds × rate;
    legal minima 10s × 0.001 units/s round to 0, so clamp to 1 because zero target volume
    is unusable in planning.
    """
    unit_noun = reading_unit_noun(language)
    explicit = project_episode_target_units(project)
    if explicit is not None:
        return EpisodeTargetVolume(units=explicit, unit_noun=unit_noun, source="units")

    seconds = project_episode_target_duration(project)
    if seconds is None:
        return None
    rate = speech_rate_units_per_second(language, project_speech_rate_override(project))
    return EpisodeTargetVolume(
        units=max(1, round(seconds * rate)),
        unit_noun=unit_noun,
        source="duration",
        seconds=seconds,
        units_per_second=rate,
    )
