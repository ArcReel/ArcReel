"""Shared six-route speech contract cases for planning and generation entry tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SpeechContractCase:
    content_mode: str
    generation_mode: str
    kind: str
    unit_id: str
    mixed_unit: dict[str, Any]
    expected_locations: tuple[tuple[str | int, ...], ...]

    @property
    def route_id(self) -> str:
        return f"{self.content_mode}-{self.generation_mode}"

    def unit(self) -> dict[str, Any]:
        return deepcopy(self.mixed_unit)

    def script(self) -> dict[str, Any]:
        return {"content_mode": self.content_mode, "episode": 1, self.kind: [self.unit()]}


SPEECH_CONTRACT_CASES = (
    SpeechContractCase(
        "narration",
        "storyboard",
        "segments",
        "E1S01",
        {
            "segment_id": "E1S01",
            "duration_seconds": 4,
            "novel_text": "风吹过旷野。",
            "video_prompt": {"dialogue": [{"speaker": "阿离", "line": "快走。"}]},
        },
        (("video_prompt", "dialogue", 0, "line"), ("novel_text",)),
    ),
    SpeechContractCase(
        "drama",
        "storyboard",
        "scenes",
        "E1S01",
        {
            "scene_id": "E1S01",
            "duration_seconds": 4,
            "utterances": [
                {"kind": "dialogue", "speaker": "阿离", "text": "快走。"},
                {"kind": "voiceover", "speaker": None, "text": "风吹过旷野。"},
            ],
        },
        (("utterances", 0, "text"), ("utterances", 1, "text")),
    ),
    SpeechContractCase(
        "ad",
        "storyboard",
        "shots",
        "E1S01",
        {
            "shot_id": "E1S01",
            "duration_seconds": 4,
            "voiceover_text": "风吹过旷野。",
            "video_prompt": {"dialogue": [{"speaker": "阿离", "line": "快走。"}]},
        },
        (("video_prompt", "dialogue", 0, "line"), ("voiceover_text",)),
    ),
    *(
        SpeechContractCase(
            content_mode,
            "reference_video",
            "video_units",
            "E1U1",
            {
                "unit_id": "E1U1",
                "duration_seconds": 4,
                "shots": [{"text": "@[阿离]：{快走。}\n{风吹过旷野。}"}],
                "references": [],
            },
            (("shots", 0, "text"), ("shots", 0, "text")),
        )
        for content_mode in ("narration", "drama", "ad")
    ),
)
