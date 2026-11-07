"""String-oriented text preprocessing."""

from __future__ import annotations

import re

# Opening fence: ``` followed by optional whitespace, case-insensitive json, whitespace and newline.
# Accept ```json / ```JSON / ``` json / ```  JSON and other whitespace variants.
_OPENING_FENCE = re.compile(r"^```[ \t]*(?:json)?[ \t]*\n?", re.IGNORECASE)
# Closing fence: final ``` and its preceding newline.
_CLOSING_FENCE = re.compile(r"\n?```$")


def normalize_newlines(text: str) -> str:
    """Normalize ``\\r\\n`` and lone ``\\r`` to ``\\n``, matching ``Path.read_text`` universal-newline translation."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_json_code_fences(text: str) -> str:
    """Remove outer Markdown fences from LLM output for json.loads.

    After trimming both ends, remove the opening ``` fence with optional whitespace
    and case-insensitive json annotation (```JSON / ```Json / ``` json / ```  JSON),
    then remove the closing ``` fence and trim again. Bare JSON is only stripped.
    """
    text = text.strip()
    text = _OPENING_FENCE.sub("", text)
    text = _CLOSING_FENCE.sub("", text)
    return text.strip()
