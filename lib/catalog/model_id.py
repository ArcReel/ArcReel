"""Provider-model ID path-segment validation.

Model IDs enter URL paths (``/models/{model_id:path}``). WHATWG URL normalizes ``.`` / ``..`` and their
percent encodings as dot segments, resolving ``a/../b`` into ``b``. Reject server-side after splitting by ``/``;
frontend encoding alone cannot prevent this.
"""

from __future__ import annotations


def has_dot_segment(model_id: str) -> bool:
    """Whether any ``/``-split model ID segment equals ``.`` / ``..``; ``a.b``, ``gpt-4.1``, and ``x/y`` are unaffected."""
    return any(segment in (".", "..") for segment in model_id.split("/"))
