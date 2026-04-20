import pytest
from pydantic import ValidationError

from lib.script_models import (
    NovelInfo,
    ReferenceResource,
    ReferenceVideoScript,
    ReferenceVideoUnit,
    Shot,
)


def test_shot_valid():
    s = Shot(duration=5, text="中远景，主角推门进酒馆")
    assert s.duration == 5
    assert "酒馆" in s.text


def test_shot_duration_range():
    with pytest.raises(ValidationError):
        Shot(duration=0, text="x")
    with pytest.raises(ValidationError):
        Shot(duration=16, text="x")


def test_reference_resource_valid_types():
    for t in ("character", "scene", "prop"):
        r = ReferenceResource(type=t, name="张三")
        assert r.type == t


def test_reference_resource_rejects_clue():
    with pytest.raises(ValidationError):
        ReferenceResource(type="clue", name="张三")


def _make_unit(**overrides):
    defaults = dict(
        unit_id="E1U1",
        shots=[Shot(duration=3, text="Shot 1"), Shot(duration=5, text="Shot 2")],
        references=[ReferenceResource(type="character", name="张三")],
        duration_seconds=8,
    )
    defaults.update(overrides)
    return ReferenceVideoUnit(**defaults)


def test_reference_video_unit_minimal():
    u = _make_unit()
    assert u.unit_id == "E1U1"
    assert len(u.shots) == 2
    assert u.duration_seconds == 8
    assert u.duration_override is False
    assert u.transition_to_next == "cut"


def test_reference_video_unit_requires_at_least_one_shot():
    with pytest.raises(ValidationError):
        _make_unit(shots=[])


def test_reference_video_unit_duration_override_flag():
    u = _make_unit(duration_override=True)
    assert u.duration_override is True


def test_reference_video_unit_transition_enum():
    with pytest.raises(ValidationError):
        _make_unit(transition_to_next="wipe")


def test_reference_video_script_valid():
    script = ReferenceVideoScript(
        title="江湖夜话",
        content_mode="reference_video",
        duration_seconds=8,
        summary="主角闯江湖。",
        novel=NovelInfo(title="江湖行", chapter="第一回"),
        video_units=[_make_unit()],
    )
    assert script.content_mode == "reference_video"
    assert len(script.video_units) == 1


def test_reference_video_script_rejects_wrong_content_mode():
    with pytest.raises(ValidationError):
        ReferenceVideoScript(
            title="x",
            content_mode="narration",
            summary="x",
            novel=NovelInfo(title="x", chapter="x"),
            video_units=[_make_unit()],
        )


def test_reference_video_unit_rejects_more_than_four_shots():
    many_shots = [Shot(duration=1, text=f"s{i}") for i in range(5)]
    with pytest.raises(ValidationError):
        _make_unit(shots=many_shots)


def test_reference_video_unit_rejects_duration_mismatch():
    with pytest.raises(ValidationError):
        _make_unit(duration_seconds=99)  # shots 3+5=8, 99 ≠ 8


def test_reference_video_unit_allows_mismatch_with_override():
    u = _make_unit(duration_seconds=99, duration_override=True)
    assert u.duration_seconds == 99
