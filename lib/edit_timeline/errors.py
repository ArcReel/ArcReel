"""Domain errors for timeline commands: stable problem codes mapped separately by HTTP and Agent tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

type EditTimelineErrorCode = Literal[
    "project_not_found",
    "episode_not_found",
    "script_invalid",
    "timeline_not_found",
    "revision_not_found",
    "timeline_name_invalid",
    "timeline_name_conflict",
    "timeline_invalid",
    "operation_invalid",
    "revision_conflict",
    "revision_summary_invalid",
    "revision_unchanged",
]


class EditTimelineError(Exception):
    """Timeline command cannot complete; ``code`` supports adapter mapping, ``params`` carries location information."""

    def __init__(self, code: EditTimelineErrorCode, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code: EditTimelineErrorCode = code
        self.params = params


# Creator-visible error code → translation key; project/episode reuse common keys. Batch-edit errors go only to Agents, not registered here; revision_unchanged occurs on both sides.
_MESSAGE_KEYS: dict[str, str] = {
    "project_not_found": "project_not_found",
    "episode_not_found": "episode_not_found",
    "timeline_not_found": "edit_timeline_not_found",
    "revision_not_found": "edit_timeline_revision_not_found",
    "revision_unchanged": "edit_timeline_revision_unchanged",
    "timeline_name_conflict": "edit_timeline_name_conflict",
    "timeline_name_invalid": "edit_timeline_name_invalid",
    "script_invalid": "edit_timeline_script_invalid",
    "timeline_invalid": "edit_timeline_invalid",
}


def edit_timeline_message(code: str, params: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Translation key/interpolation parameters for an error code; None if not a creator-facing timeline error code."""
    key = _MESSAGE_KEYS.get(code)
    if key is None:
        return None
    values = dict(params)
    if "project" in values:
        values["name"] = values.pop("project")
    return key, values


__all__ = ["EditTimelineError", "EditTimelineErrorCode", "edit_timeline_message"]
