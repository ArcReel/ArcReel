"""Model-entry lifecycle: available, retiring, or discontinued by current UTC dates (docs/adr/0103).

Entry lifecycle represents vendor-level retirement shared by built-in/custom providers: every corresponding provider model uses
:func:`entry_lifecycle`. Compute each time, outside caches rebuilt only on catalog changes; crossing shutdown dates within one process
immediately affects candidates/calls even without synchronization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from arcreel_catalog_core import CatalogEntry

#: ``available``; ``retiring`` for future shutdown; ``discontinued``.
LifecycleStatus = Literal["available", "retiring", "discontinued"]


@dataclass(frozen=True)
class Lifecycle:
    """Entry lifecycle with shutdown dates/official successors (vendor/official model name, same media type).

    Always expose dates/successors unchanged; successors alone without shutdown dates still mean available.
    """

    status: LifecycleStatus
    shutdown_date: date | None = None
    successor: str | None = None

    @property
    def discontinued(self) -> bool:
        return self.status == "discontinued"


#: Models without corresponding entries lack lifecycle information, treated as available.
AVAILABLE = Lifecycle("available")


def utc_today() -> date:
    """Lifecycle today: current UTC date; tests replace this function to inject clocks."""
    return datetime.now(UTC).date()


def entry_lifecycle(entry: CatalogEntry | None, today: date | None = None) -> Lifecycle:
    """Entry lifecycle on ``today``, default :func:`utc_today`.

    Shutdown on/before today or true ``discontinued`` means discontinued, including shutdown day; future dates mean retiring, otherwise available.
    ``entry`` = ``None`` without corresponding entries means available.
    """
    if entry is None:
        return AVAILABLE
    shutdown = entry.shutdown_date
    if entry.discontinued or (shutdown is not None and shutdown <= (today or utc_today())):
        status: LifecycleStatus = "discontinued"
    elif shutdown is not None:
        status = "retiring"
    else:
        status = "available"
    return Lifecycle(status, shutdown, entry.successor)
