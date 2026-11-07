import pytest
from pydantic import ValidationError

from lib.script.script_models import (
    NovelInfo,
    ReferenceResource,
    ReferenceVideoScript,
    ReferenceVideoUnit,
)


def test_reference_resource_valid_types():
    for t in ("character", "scene", "prop"):
        r = ReferenceResource(type=t, name="张三")
        assert r.type == t


def test_reference_resource_rejects_clue():
    with pytest.raises(ValidationError):
        ReferenceResource(type="clue", name="张三")


def _make_unit(**overrides):
    defaults = {
        "unit_id": "E1U1",
        "text": "镜头一\n镜头二",
        "duration_seconds": 8,
    }
    defaults.update(overrides)
    return ReferenceVideoUnit(**defaults)


def test_reference_video_unit_minimal():
    u = _make_unit()
    assert u.unit_id == "E1U1"
    assert u.text == "镜头一\n镜头二"
    assert u.duration_seconds == 8
    assert u.needs_replan is False


def test_reference_video_unit_rejects_legacy_shot_fields():
    """Bodies are the sole persisted source of truth; strict models reject written ``shots`` / ``references``."""
    with pytest.raises(ValidationError):
        _make_unit(shots=[{"text": "镜头一"}])
    with pytest.raises(ValidationError):
        _make_unit(references=[{"type": "character", "name": "张三"}])


def test_reference_video_unit_empty_text_only_allowed_as_replan_shell():
    """Empty bodies require needs_replan=True and 0 seconds, representing migration-leftover problem shells."""
    shell = _make_unit(text="", duration_seconds=0, needs_replan=True)
    assert shell.needs_replan is True
    assert shell.duration_seconds == 0

    with pytest.raises(ValidationError):
        _make_unit(text="")
    with pytest.raises(ValidationError):
        _make_unit(text="  \n ", duration_seconds=0, needs_replan=False)
    with pytest.raises(ValidationError):
        _make_unit(text="", duration_seconds=8, needs_replan=True)


def test_reference_video_unit_rejects_transition():
    with pytest.raises(ValidationError):
        _make_unit(transition_to_next="cut")


def test_reference_video_script_valid():
    script = ReferenceVideoScript(
        title="江湖夜话",
        content_mode="narration",
        novel=NovelInfo(title="江湖行", chapter="第一回"),
        video_units=[_make_unit()],
    )
    # Scripts carry only creative content type; generation mode is a project-level property, absent from scripts.
    assert script.content_mode == "narration"
    assert not hasattr(script, "generation_mode")
    assert len(script.video_units) == 1


def test_reference_video_script_accepts_drama_content_mode():
    script = ReferenceVideoScript(
        title="剧集",
        content_mode="drama",
        novel=NovelInfo(title="x", chapter="x"),
        video_units=[_make_unit()],
    )
    assert script.content_mode == "drama"


def test_reference_video_script_rejects_legacy_reference_video_content_mode():
    """content_mode no longer accepts reference_video, which belongs to project-level generation_mode."""
    with pytest.raises(ValidationError):
        ReferenceVideoScript(
            title="x",
            content_mode="reference_video",
            novel=NovelInfo(title="x", chapter="x"),
            video_units=[_make_unit()],
        )


def test_reference_video_unit_duration_is_independent_of_text_length():
    """Unit duration is the sole source of truth, independent of body length and constrained only by structural bounds."""
    assert _make_unit(duration_seconds=12).duration_seconds == 12
    assert _make_unit(text="一行", duration_seconds=120).duration_seconds == 120


def test_reference_video_unit_rejects_duration_out_of_structural_range():
    with pytest.raises(ValidationError):
        _make_unit(duration_seconds=0)
    with pytest.raises(ValidationError):
        _make_unit(duration_seconds=9999)
