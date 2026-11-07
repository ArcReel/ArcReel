"""Final-cut rendering domain errors: stable problem codes independently mapped by HTTP, Agent tools, and queued tasks."""

from __future__ import annotations

from typing import Any, Literal

type FinalCutErrorCode = Literal[
    "final_cut_narration_unavailable",
    "final_cut_presentation_unavailable",
    "final_cut_blocked",
    "final_cut_video_version_corrupted",
    "final_cut_narration_version_corrupted",
    "final_cut_empty",
    "final_cut_ffmpeg_unavailable",
    "final_cut_render_failed",
    "final_cut_acceptance_failed",
]


class FinalCutError(Exception):
    """Final cut cannot render or fails acceptance; adapters map ``code`` and ``params`` carries location details."""

    def __init__(self, code: FinalCutErrorCode, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code: FinalCutErrorCode = code
        self.params = params


__all__ = ["FinalCutError", "FinalCutErrorCode"]
