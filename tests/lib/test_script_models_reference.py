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
    s = Shot(text="中远景，主角推门进酒馆")
    assert "酒馆" in s.text


def test_shot_rejects_duration_field():
    """时长收编到 unit 级：镜头不再承载时长，写入即被 strict 模型拒绝。"""
    with pytest.raises(ValidationError):
        Shot(duration=5, text="x")  # pyright: ignore[reportCallIssue]


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
        shots=[Shot(text="镜头一"), Shot(text="镜头二")],
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
    assert u.transition_to_next == "cut"


def test_reference_video_unit_requires_at_least_one_shot():
    with pytest.raises(ValidationError):
        _make_unit(shots=[])


def test_reference_video_unit_transition_enum():
    with pytest.raises(ValidationError):
        _make_unit(transition_to_next="wipe")


def test_reference_video_script_valid():
    script = ReferenceVideoScript(
        title="江湖夜话",
        content_mode="narration",
        duration_seconds=8,
        novel=NovelInfo(title="江湖行", chapter="第一回"),
        video_units=[_make_unit()],
    )
    # 参考视频脚本由 content_mode（narration/drama）+ generation_mode 两条维度表达
    assert script.content_mode == "narration"
    assert script.generation_mode == "reference_video"
    assert len(script.video_units) == 1


def test_reference_video_script_accepts_drama_content_mode():
    script = ReferenceVideoScript(
        title="剧集",
        content_mode="drama",
        novel=NovelInfo(title="x", chapter="x"),
        video_units=[_make_unit()],
    )
    assert script.content_mode == "drama"
    assert script.generation_mode == "reference_video"


def test_reference_video_script_rejects_legacy_reference_video_content_mode():
    """content_mode 不再允许 reference_video（它属于 generation_mode 维度）。"""
    with pytest.raises(ValidationError):
        ReferenceVideoScript(
            title="x",
            content_mode="reference_video",
            novel=NovelInfo(title="x", chapter="x"),
            video_units=[_make_unit()],
        )


def test_reference_video_unit_rejects_more_than_four_shots():
    many_shots = [Shot(text=f"s{i}") for i in range(5)]
    with pytest.raises(ValidationError):
        _make_unit(shots=many_shots)


def test_reference_video_unit_duration_is_independent_of_shots():
    """unit 时长是唯一真相：不再与镜头数 / 镜头内容挂钩，取值只受结构区间约束。"""
    assert _make_unit(duration_seconds=12).duration_seconds == 12


def test_reference_video_unit_rejects_duration_out_of_structural_range():
    with pytest.raises(ValidationError):
        _make_unit(duration_seconds=0)
    with pytest.raises(ValidationError):
        _make_unit(duration_seconds=61)
