"""Normalize project style fields consistently before prompt rendering.

Asset images, storyboards, grids, and reference-to-video share :func:`normalize_style_value`: trim
outer whitespace and treat nonstrings (externally corrupted ``project.json`` or explicit null) as empty,
so template truth conditions never emit whitespace-only declarations. Artifact bases record raw values independently.
"""


def normalize_style_value(value: object) -> str:
    """Rendering value for ``style`` / ``style_description``."""
    return value.strip() if isinstance(value, str) else ""
