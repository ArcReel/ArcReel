"""Jianying export/download domain errors: stable problem codes independently mapped by HTTP, Agent tools, and queued tasks."""

from __future__ import annotations

from typing import Any, Literal

type JianyingDraftErrorCode = Literal[
    "jianying_draft_narration_unavailable",
    "jianying_draft_blocked",
    "jianying_draft_empty",
    "jianying_draft_presentation_unavailable",
    "jianying_draft_hold_frame_unavailable",
    "jianying_draft_acceptance_failed",
    "jianying_draft_not_exported",
    "jianying_draft_invalid",
]


class JianyingDraftError(Exception):
    """Draft cannot export, fails acceptance, or cannot download; adapters map ``code`` and ``params`` carries location details."""

    def __init__(self, code: JianyingDraftErrorCode, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code: JianyingDraftErrorCode = code
        self.params = params


__all__ = ["JianyingDraftError", "JianyingDraftErrorCode"]
