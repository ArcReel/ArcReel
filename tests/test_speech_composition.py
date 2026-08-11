from copy import deepcopy

import pytest

from lib.speech_composition import (
    SpeechComposition,
    SpeechFieldLocation,
    SpeechMode,
    SpeechOwner,
    SpeechProblemAction,
    SpeechProblemCode,
    SpeechProblemReason,
    adapt_ad_shot,
    adapt_drama_scene,
    adapt_narration_segment,
    adapt_video_unit,
)

pytestmark = pytest.mark.unit


def test_narration_novel_text_is_materialized_as_narrator_voiceover() -> None:
    source = {"segment_id": "E1S01", "novel_text": "“别走。”她仍在心里重复。", "video_prompt": {}}

    result = SpeechComposition.prepare(adapt_narration_segment(source))

    assert result.mode is SpeechMode.NARRATOR_VOICEOVER
    assert [(entry.owner, entry.speaker, entry.text) for entry in result.utterances] == [
        (SpeechOwner.NARRATOR, None, "“别走。”她仍在心里重复。")
    ]
    assert result.problems == ()
    assert source == {"segment_id": "E1S01", "novel_text": "“别走。”她仍在心里重复。", "video_prompt": {}}


def test_narration_dialogue_and_novel_text_are_reported_as_mixed_speech() -> None:
    result = SpeechComposition.prepare(
        adapt_narration_segment(
            {
                "segment_id": "E1S02",
                "novel_text": "雨夜里，她想起了那句警告。",
                "video_prompt": {"dialogue": [{"speaker": "阿离", "line": "别回头。"}]},
            }
        )
    )

    assert result.mode is None
    assert [(entry.owner, entry.speaker, entry.text) for entry in result.utterances] == [
        (SpeechOwner.CHARACTER, "阿离", "别回头。"),
        (SpeechOwner.NARRATOR, None, "雨夜里，她想起了那句警告。"),
    ]
    assert [problem.code for problem in result.problems] == [SpeechProblemCode.MIXED_SPEECH]


@pytest.mark.parametrize(
    ("adapter", "source"),
    [
        (adapt_narration_segment, {"segment_id": "E1S01", "novel_text": "风吹过旷野。", "video_prompt": {}}),
        (
            adapt_drama_scene,
            {
                "scene_id": "E1S01",
                "utterances": [{"kind": "voiceover", "speaker": None, "text": "风吹过旷野。"}],
            },
        ),
        (adapt_ad_shot, {"shot_id": "E1S01", "voiceover_text": "风吹过旷野。", "video_prompt": {}}),
        (adapt_video_unit, {"unit_id": "E1S01", "shots": [{"text": "{风吹过旷野。}"}]}),
    ],
)
def test_narrator_voiceover_has_the_same_result_through_every_skeleton(adapter, source) -> None:
    result = SpeechComposition.prepare(adapter(source))

    assert result.mode is SpeechMode.NARRATOR_VOICEOVER
    assert [(entry.owner, entry.speaker, entry.text) for entry in result.utterances] == [
        (SpeechOwner.NARRATOR, None, "风吹过旷野。")
    ]
    assert result.problems == ()


@pytest.mark.parametrize(
    ("adapter", "source"),
    [
        (
            adapt_drama_scene,
            {
                "scene_id": "E2S03",
                "utterances": [
                    {"kind": "dialogue", "speaker": "阿离", "text": "跟紧我。"},
                    {"kind": "dialogue", "speaker": "阿离", "text": "不能让他看出害怕。"},
                ],
            },
        ),
        (
            adapt_ad_shot,
            {
                "shot_id": "E2S03",
                "video_prompt": {
                    "dialogue": [
                        {"speaker": "阿离", "line": "跟紧我。"},
                        {"speaker": "阿离", "line": "不能让他看出害怕。"},
                    ]
                },
                "voiceover_text": "",
            },
        ),
        (
            adapt_video_unit,
            {
                "unit_id": "E2S03",
                "shots": [{"text": "@[阿离]：{跟紧我。}\n@[阿离]：{不能让他看出害怕。}"}],
            },
        ),
    ],
)
def test_character_speech_keeps_the_same_ownership_and_order_through_applicable_skeletons(adapter, source) -> None:
    result = SpeechComposition.prepare(adapter(source))

    assert result.mode is SpeechMode.CHARACTER_SPEECH
    assert [(entry.owner, entry.speaker, entry.text) for entry in result.utterances] == [
        (SpeechOwner.CHARACTER, "阿离", "跟紧我。"),
        (SpeechOwner.CHARACTER, "阿离", "不能让他看出害怕。"),
    ]
    assert result.problems == ()


@pytest.mark.parametrize(
    ("adapter", "source", "expected_locations"),
    [
        (
            adapt_drama_scene,
            {
                "scene_id": "E1S04",
                "utterances": [
                    {"kind": "dialogue", "speaker": "阿离", "text": "快走。"},
                    {"kind": "voiceover", "speaker": None, "text": "大门在她身后合拢。"},
                ],
            },
            (
                SpeechFieldLocation(("utterances", 0, "text")),
                SpeechFieldLocation(("utterances", 1, "text")),
            ),
        ),
        (
            adapt_ad_shot,
            {
                "shot_id": "E1S04",
                "video_prompt": {"dialogue": [{"speaker": "阿离", "line": "快走。"}]},
                "voiceover_text": "大门在她身后合拢。",
            },
            (
                SpeechFieldLocation(("video_prompt", "dialogue", 0, "line")),
                SpeechFieldLocation(("voiceover_text",)),
            ),
        ),
        (
            adapt_video_unit,
            {"unit_id": "E1S04", "shots": [{"text": "@[阿离]：{快走。}\n{大门在她身后合拢。}"}]},
            (
                SpeechFieldLocation(("shots", 0, "text"), line=0),
                SpeechFieldLocation(("shots", 0, "text"), line=1),
            ),
        ),
    ],
)
def test_mixed_speech_returns_a_closed_located_problem_without_rewriting_content(
    adapter, source, expected_locations
) -> None:
    original = deepcopy(source)
    result = SpeechComposition.prepare(adapter(source))

    assert result.mode is None
    assert [(entry.owner, entry.text) for entry in result.utterances] == [
        (SpeechOwner.CHARACTER, "快走。"),
        (SpeechOwner.NARRATOR, "大门在她身后合拢。"),
    ]
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.code is SpeechProblemCode.MIXED_SPEECH
    assert problem.unit_id == "E1S04"
    assert problem.locations == expected_locations
    assert problem.reason is SpeechProblemReason.CHARACTER_AND_NARRATOR_MIXED
    assert problem.action is SpeechProblemAction.REPLAN_UNIT
    assert source == original


@pytest.mark.parametrize(
    ("snapshot", "expected_location"),
    [
        (
            adapt_drama_scene(
                {
                    "scene_id": "E1S02",
                    "utterances": [{"kind": "dialogue", "speaker": "  ", "text": "快走。"}],
                }
            ),
            SpeechFieldLocation(("utterances", 0, "speaker")),
        ),
        (
            adapt_ad_shot(
                {
                    "shot_id": "E1S02",
                    "video_prompt": {"dialogue": [{"speaker": None, "line": "快走。"}]},
                    "voiceover_text": "",
                }
            ),
            SpeechFieldLocation(("video_prompt", "dialogue", 0, "speaker")),
        ),
        (
            adapt_video_unit({"unit_id": "E1S02", "shots": [{"text": "空镜。\n@[ ]：{快走。}"}]}),
            SpeechFieldLocation(("shots", 0, "text"), line=1),
        ),
    ],
)
def test_empty_character_speaker_is_a_structured_blocker(snapshot, expected_location) -> None:
    result = SpeechComposition.prepare(snapshot)

    assert result.mode is None
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.code is SpeechProblemCode.EMPTY_SPEAKER
    assert problem.unit_id == "E1S02"
    assert problem.locations == (expected_location,)
    assert problem.reason is SpeechProblemReason.CHARACTER_SPEAKER_EMPTY
    assert problem.action is SpeechProblemAction.ASSIGN_SPEAKER


@pytest.mark.parametrize(
    ("snapshot", "expected_location"),
    [
        (
            adapt_narration_segment({"segment_id": "E1S03", "novel_text": 42, "video_prompt": {}}),
            SpeechFieldLocation(("novel_text",)),
        ),
        (
            adapt_drama_scene(
                {
                    "scene_id": "E1S03",
                    "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": 42}],
                }
            ),
            SpeechFieldLocation(("utterances", 0, "text")),
        ),
        (
            adapt_ad_shot({"shot_id": "E1S03", "video_prompt": {"dialogue": "坏结构"}, "voiceover_text": ""}),
            SpeechFieldLocation(("video_prompt", "dialogue")),
        ),
        (
            adapt_video_unit({"unit_id": "E1S03", "shots": [{"text": "空镜。\n@[阿离]：{快走。"}]}),
            SpeechFieldLocation(("shots", 0, "text"), line=1),
        ),
    ],
)
def test_damaged_input_returns_a_located_parse_problem(snapshot, expected_location) -> None:
    result = SpeechComposition.prepare(snapshot)

    assert result.mode is None
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.code is SpeechProblemCode.PARSE_FAILED
    assert problem.unit_id == "E1S03"
    assert problem.locations == (expected_location,)
    assert problem.reason is SpeechProblemReason.SPEECH_INPUT_UNPARSEABLE
    assert problem.action is SpeechProblemAction.FIX_INPUT


def test_needs_replan_is_a_closed_problem_even_when_speech_is_otherwise_valid() -> None:
    snapshot = adapt_video_unit(
        {
            "unit_id": "E1U04",
            "needs_replan": True,
            "shots": [{"text": "门缓缓打开。\n{多年以后，他仍记得这一幕。}"}],
        }
    )

    result = SpeechComposition.prepare(snapshot)

    assert result.mode is None
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.code is SpeechProblemCode.NEEDS_REPLAN
    assert problem.unit_id == "E1U04"
    assert problem.locations == (SpeechFieldLocation(("needs_replan",)),)
    assert problem.reason is SpeechProblemReason.UNIT_MARKED_NEEDS_REPLAN
    assert problem.action is SpeechProblemAction.REPLAN_UNIT


def test_persisted_speech_mode_cannot_override_mechanical_derivation() -> None:
    snapshot = adapt_video_unit(
        {
            "unit_id": "E1U05",
            "speech_mode": "silent",
            "shots": [{"text": "@[阿离] 站在门边。\n@[阿离]：{我回来了。}"}],
        }
    )

    result = SpeechComposition.prepare(snapshot)

    assert result.mode is SpeechMode.CHARACTER_SPEECH
    assert result.problems == ()


@pytest.mark.parametrize(
    "snapshot",
    [
        adapt_drama_scene({"scene_id": "E1S06", "utterances": []}),
        adapt_ad_shot({"shot_id": "E1S06", "voiceover_text": "", "video_prompt": {"dialogue": []}}),
        adapt_video_unit({"unit_id": "E1S06", "shots": [{"text": "空镜，风吹过树梢。"}, {"text": "门缓缓合上。"}]}),
    ],
)
def test_units_without_spoken_content_are_silent(snapshot) -> None:
    result = SpeechComposition.prepare(snapshot)

    assert result.mode is SpeechMode.SILENT
    assert result.utterances == ()
    assert result.problems == ()


def test_drama_voiceover_with_speaker_is_a_parse_blocker() -> None:
    result = SpeechComposition.prepare(
        adapt_drama_scene(
            {
                "scene_id": "E1S06",
                "utterances": [{"kind": "voiceover", "speaker": "阿离", "text": "我不能让他发现。"}],
            }
        )
    )

    assert result.mode is None
    assert result.utterances == ()
    assert [(problem.code, problem.locations) for problem in result.problems] == [
        (SpeechProblemCode.PARSE_FAILED, (SpeechFieldLocation(("utterances", 0, "speaker")),))
    ]


def test_reference_video_adapter_preserves_cross_shot_utterance_order() -> None:
    snapshot = adapt_video_unit(
        {
            "unit_id": "E1U07",
            "shots": [
                {"text": "@[阿离] 推门。\n@[阿离]：{有人吗？}\n@[阿离]：{我进来了。}"},
                {"text": "@[守卫] 抬头。\n@[守卫]：{站住。}"},
            ],
        }
    )

    result = SpeechComposition.prepare(snapshot)

    assert [entry.text for entry in result.utterances] == ["有人吗？", "我进来了。", "站住。"]
    assert [entry.location for entry in result.utterances] == [
        SpeechFieldLocation(("shots", 0, "text"), line=1),
        SpeechFieldLocation(("shots", 0, "text"), line=2),
        SpeechFieldLocation(("shots", 1, "text"), line=1),
    ]


def test_reference_video_adapter_allows_asset_headings_without_guessing_their_type() -> None:
    snapshot = adapt_video_unit(
        {
            "unit_id": "E1U08",
            "shots": [{"text": "@[酒馆]：木门被风吹开。"}],
            "references": [{"type": "scene", "name": "酒馆"}],
        }
    )

    result = SpeechComposition.prepare(snapshot)

    assert result.mode is SpeechMode.SILENT
    assert result.problems == ()


def test_reference_video_adapter_rejects_malformed_dialogue_for_a_declared_character() -> None:
    snapshot = adapt_video_unit(
        {
            "unit_id": "E1U09",
            "shots": [{"text": "@[阿离]：快走。"}],
            "references": [{"type": "character", "name": "阿离"}],
        }
    )

    result = SpeechComposition.prepare(snapshot)

    assert result.mode is None
    assert [(problem.code, problem.locations) for problem in result.problems] == [
        (SpeechProblemCode.PARSE_FAILED, (SpeechFieldLocation(("shots", 0, "text"), line=0),))
    ]


def test_reference_video_adapter_normalizes_character_names_before_matching() -> None:
    snapshot = adapt_video_unit(
        {
            "unit_id": "E1U10",
            "shots": [{"text": "@[Caf\u00e9]：hello"}],
            "references": [{"type": "character", "name": "Cafe\u0301"}],
        }
    )

    result = SpeechComposition.prepare(snapshot)

    assert result.mode is None
    assert [(problem.code, problem.locations) for problem in result.problems] == [
        (SpeechProblemCode.PARSE_FAILED, (SpeechFieldLocation(("shots", 0, "text"), line=0),))
    ]


def test_damaged_unit_identity_and_container_are_reported_together() -> None:
    result = SpeechComposition.prepare(adapt_video_unit({"unit_id": " ", "shots": "not-a-list"}))

    assert result.mode is None
    assert [(problem.code, problem.locations) for problem in result.problems] == [
        (SpeechProblemCode.PARSE_FAILED, (SpeechFieldLocation(("unit_id",)),)),
        (SpeechProblemCode.PARSE_FAILED, (SpeechFieldLocation(("shots",)),)),
    ]


@pytest.mark.parametrize(
    ("snapshot", "expected_location"),
    [
        (
            adapt_drama_scene(
                {
                    "scene_id": "E1S08",
                    "utterances": [{"kind": "dialogue", "speaker": 7, "text": "快走。"}],
                }
            ),
            SpeechFieldLocation(("utterances", 0, "speaker")),
        ),
        (
            adapt_ad_shot({"shot_id": "E1S08", "video_prompt": "bad", "voiceover_text": ""}),
            SpeechFieldLocation(("video_prompt",)),
        ),
        (
            adapt_video_unit({"unit_id": "E1U08", "shots": []}),
            SpeechFieldLocation(("shots",)),
        ),
    ],
)
def test_unusable_speech_shapes_never_degrade_to_a_valid_mode(snapshot, expected_location) -> None:
    result = SpeechComposition.prepare(snapshot)

    assert result.mode is None
    assert [(problem.code, problem.locations) for problem in result.problems] == [
        (SpeechProblemCode.PARSE_FAILED, (expected_location,))
    ]


@pytest.mark.parametrize(
    ("snapshot", "expected_location"),
    [
        (
            adapt_drama_scene({"scene_id": "E1S09"}),
            SpeechFieldLocation(("utterances",)),
        ),
        (
            adapt_drama_scene({"scene_id": "E1S09", "utterances": [7]}),
            SpeechFieldLocation(("utterances", 0)),
        ),
        (
            adapt_drama_scene(
                {"scene_id": "E1S09", "utterances": [{"kind": "unknown", "speaker": None, "text": "风声。"}]}
            ),
            SpeechFieldLocation(("utterances", 0, "kind")),
        ),
        (
            adapt_ad_shot(
                {
                    "shot_id": "E1S09",
                    "video_prompt": {"dialogue": [{"speaker": 7, "line": "快走。"}]},
                    "voiceover_text": "",
                }
            ),
            SpeechFieldLocation(("video_prompt", "dialogue", 0, "speaker")),
        ),
        (
            adapt_ad_shot({"shot_id": "E1S09", "voiceover_text": "风声。"}),
            SpeechFieldLocation(("video_prompt",)),
        ),
        (
            adapt_ad_shot({"shot_id": "E1S09", "video_prompt": {"dialogue": []}}),
            SpeechFieldLocation(("voiceover_text",)),
        ),
    ],
)
def test_damaged_structured_speech_fields_are_parse_blockers(snapshot, expected_location) -> None:
    result = SpeechComposition.prepare(snapshot)

    assert result.mode is None
    assert [(problem.code, problem.locations) for problem in result.problems] == [
        (SpeechProblemCode.PARSE_FAILED, (expected_location,))
    ]
