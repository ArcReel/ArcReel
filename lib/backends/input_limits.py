"""Effective input-count limits: minimum entry/channel values, then whole-dimension capability overrides (docs/adr/0104).

Represent three cases for reference-image and similar limits with :class:`InputLimit`:

- **Unsupported**: the input path cannot operate (image-to-image unavailable in effective image capabilities,
  or video channels without reference-to-video); counts are inapplicable.
- **Unlisted**: neither entry nor channel declares a count. Do not clip; upstream rejects excess inputs.
- **Maximum N**: at least one side declares counts; take the smaller. ``N`` may be ``0``, explicitly refusing inputs.

``0`` means maximum zero, never no clipping. Both zero and unsupported reject inputs: omit candidates
and reject at execution, never clip to zero and silently switch orchestration paths.

Standard-library dependencies only. Image/video capability evaluation share this; candidates, execution
validation, orchestration clipping, and settings all read the same evaluated result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class LimitState(StrEnum):
    UNSUPPORTED = "unsupported"
    UNRECORDED = "unrecorded"
    BOUNDED = "bounded"


@dataclass(frozen=True)
class InputLimit[N: (int, float)]:
    """Effective limit for one input kind; ``maximum`` exists only when ``BOUNDED``."""

    state: LimitState
    maximum: N | None = None

    def __post_init__(self) -> None:
        if (self.state == LimitState.BOUNDED) != (self.maximum is not None):
            raise ValueError(f"InputLimit {self.state} with maximum={self.maximum!r}")
        if self.maximum is not None and self.maximum < 0:
            raise ValueError(f"InputLimit maximum must be non-negative: {self.maximum!r}")

    @classmethod
    def unsupported(cls) -> InputLimit[N]:
        return cls(LimitState.UNSUPPORTED)

    @classmethod
    def unrecorded(cls) -> InputLimit[N]:
        return cls(LimitState.UNRECORDED)

    @classmethod
    def at_most(cls, maximum: N) -> InputLimit[N]:
        return cls(LimitState.BOUNDED, maximum)

    @classmethod
    def from_json(cls, value: N | None) -> InputLimit[N]:
        """Inverse of :meth:`to_json`: ``None`` means unlisted; other values bound counts (``0`` refuses inputs).

        Use for effective values already stored in interface shapes, such as ``VideoCapabilities`` image/audio
        limits. Unsupported and zero bounds both serialize as ``0`` and give identical candidate/execution /
        clipping decisions after reading.
        """
        return cls.unrecorded() if value is None else cls.at_most(value)

    @property
    def accepts_input(self) -> bool:
        """Whether this input kind may be supplied: unlisted or maximum greater than zero."""
        if self.state == LimitState.UNRECORDED:
            return True
        return self.maximum is not None and self.maximum > 0

    def clamp_bound(self) -> N | None:
        """Orchestration clipping bound: return positive maxima unchanged; ``None`` means do not clip.

        Also skip clipping for refused inputs: retain them for execution rejection, never clip to zero and
        silently switch paths.
        """
        if self.state == LimitState.BOUNDED and self.maximum is not None and self.maximum > 0:
            return self.maximum
        return None

    def to_json(self) -> N | int | None:
        """Interface/UI shape: unlisted ``None``, refused (zero maximum or unsupported) ``0``, otherwise maximum.

        UI displays unsupported/zero as refused; corresponding image-to-image or reference-to-video dimensions
        separately express whether the underlying input path operates.
        """
        if self.state == LimitState.UNRECORDED:
            return None
        if self.state == LimitState.UNSUPPORTED:
            return 0
        return self.maximum


def effective_input_limit[N: (int, float)](
    *, path_supported: bool, declared: Iterable[N | None], override: N | None = None
) -> InputLimit[N]:
    """Effective limit for one input kind.

    - False ``path_supported``: unsupported, even with capability overrides; its own dimension determines the path.
    - Non-``None`` ``override``: replace the entire two-sided evaluated dimension.
    - Otherwise minimum ``declared`` entry/channel values, with ``None`` meaning undeclared on that side;
      neither declared means unlisted.
    """
    if not path_supported:
        return InputLimit.unsupported()
    if override is not None:
        return InputLimit.at_most(override)
    values = [value for value in declared if value is not None]
    if not values:
        return InputLimit.unrecorded()
    return InputLimit.at_most(min(values))
