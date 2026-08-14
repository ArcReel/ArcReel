"""Shared six-route speech contract cases for planning and generation entry tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SpeechContractCase:
    creation_type: str
    generation_mode: str
    kind: str
    unit_id: str
    mixed_unit: dict[str, Any]
    expected_locations: tuple[tuple[str | int, ...], ...]

    @property
    def route_id(self) -> str:
        return f"{self.creation_type}-{self.generation_mode}"

    def unit(self) -> dict[str, Any]:
        return deepcopy(self.mixed_unit)

    def script(self) -> dict[str, Any]:
        return {"creation_type": self.creation_type, "episode": 1, self.kind: [self.unit()]}


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
            creation_type,
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
        for creation_type in ("narration", "drama", "ad")
    ),
)
