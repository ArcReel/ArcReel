from __future__ import annotations

import pytest
from pydantic import ValidationError

from lib.reference_video.keyframes import materialize_keyframes
from lib.reference_video.request_projection import unit_reference_declarations
from lib.script_models import ReferenceVideoUnit
from server.services.image_model_selection import ImageModelSelection
from server.services.reference_keyframe_tasks import reference_keyframe_task_specs

pytestmark = pytest.mark.unit


def test_materialize_keyframes_assigns_stable_ids_and_inline_mentions_in_order() -> None:
    text, keyframes = materialize_keyframes(
        "E2U04",
        "开场 [[关键分镜1]]，转场 [[关键分镜2]]。",
        ["第一处核心场景的静态首帧", "第二处核心场景的静态首帧"],
    )

    assert text == "开场 @[关键分镜 E2U04K01]，转场 @[关键分镜 E2U04K02]。"
    assert [item["keyframe_id"] for item in keyframes] == ["E2U04K01", "E2U04K02"]


def test_reference_video_unit_rejects_more_than_five_keyframes() -> None:
    with pytest.raises(ValidationError):
        ReferenceVideoUnit.model_validate(
            {
                "unit_id": "E1U01",
                "text": "正文",
                "duration_seconds": 5,
                "keyframes": [
                    {"keyframe_id": f"E1U01K{index:02d}", "description": str(index)}
                    for index in range(1, 7)
                ],
            }
        )


def test_keyframe_reference_declarations_follow_manuscript_order() -> None:
    project = {
        "characters": {"鳄鱼爸爸": {"character_sheet": "characters/鳄鱼爸爸.png"}},
        "scenes": {},
        "props": {},
        "products": {},
    }
    unit = {
        "unit_id": "E1U01",
        "text": "@[鳄鱼爸爸] 走入画面。@[关键分镜 E1U01K01] 随后切换。",
        "keyframes": [
            {
                "keyframe_id": "E1U01K01",
                "description": "核心场景的第一帧",
                "image_path": "keyframes/E1U01K01.png",
            }
        ],
    }

    assert [(ref.type, ref.name) for ref in unit_reference_declarations(project, unit)] == [
        ("character", "鳄鱼爸爸"),
        ("keyframe", "E1U01K01"),
    ]


def test_reference_keyframe_specs_keep_request_scoped_model_override() -> None:
    script = {
        "video_units": [
            {
                "unit_id": "E1U01",
                "keyframes": [
                    {
                        "keyframe_id": "E1U01K01",
                        "description": "核心场景的第一帧",
                        "image_path": None,
                    }
                ],
            }
        ]
    }

    specs = reference_keyframe_task_specs(
        script,
        "episode_1.json",
        missing_only=True,
        image_override={"image_provider": "openai", "image_model": "gpt-image-1"},
    )

    assert len(specs) == 1
    assert specs[0].payload["image_provider"] == "openai"
    assert specs[0].payload["image_model"] == "gpt-image-1"


def test_image_model_selection_requires_provider_and_model_as_a_pair() -> None:
    assert ImageModelSelection().image_override_payload() == {}
    assert ImageModelSelection(
        image_provider="openai", image_model="gpt-image-1"
    ).image_override_payload() == {
        "image_provider": "openai",
        "image_model": "gpt-image-1",
    }
    with pytest.raises(ValidationError):
        ImageModelSelection(image_provider="openai")
