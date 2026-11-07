"""Runtime shape predicates for persisted schemas.

Dataclass annotations describe the expected schema, but instances also come
from disk JSON through ``from_dict`` and similar entry points. Construction must
therefore validate actual shapes. Predicates accept ``object`` so the types express
this division: annotations state expectations; predicates check reality.
Construction checks must not become tautologies about their own annotations.
"""

from collections.abc import Mapping
from math import isfinite

__all__ = [
    "is_bool",
    "is_finite_number",
    "is_int",
    "is_mapping",
    "is_shape",
    "is_str",
]


def is_int(value: object, *, minimum: int | None = None) -> bool:
    """Strict integer check: ``bool`` subclasses ``int`` but is not an integer here."""
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return minimum is None or value >= minimum


def is_finite_number(value: object) -> bool:
    """Finite real-number check, also excluding ``bool``."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return isfinite(value)


def is_bool(value: object) -> bool:
    return isinstance(value, bool)


def is_str(value: object) -> bool:
    return isinstance(value, str)


def is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


def is_shape(value: object, expected: type | tuple[type, ...]) -> bool:
    """General shape check for persisted fields without a dedicated predicate."""
    return isinstance(value, expected)
