import pytest
from pydantic import ValidationError

from lib.script_models import (
    NovelInfo,
    ReferenceResource,
    ReferenceVideoScript,
    ReferenceVideoUnit,
)


@pytest.mark.unit
def test_reference_resource_valid_types():
    for t in ("character", "scene", "prop"):
        r = ReferenceResource(type=t, name="张三")
        assert r.type == t


@pytest.mark.unit
def test_reference_resource_rejects_clue():
    with pytest.raises(ValidationError):
        ReferenceResource(type="clue", name="张三")


def _make_unit(**overrides):
    defaults = dict(
        unit_id="E1U1",
        text="镜头一\n镜头二",
        duration_seconds=8,
    )
    defaults.update(overrides)
    return ReferenceVideoUnit(**defaults)


@pytest.mark.unit
def test_reference_video_unit_minimal():
    u = _make_unit()
    assert u.unit_id == "E1U1"
    assert u.text == "镜头一\n镜头二"
    assert u.duration_seconds == 8
    assert u.transition_to_next == "cut"
    assert u.needs_replan is False


@pytest.mark.unit
def test_reference_video_unit_rejects_legacy_shot_fields():
    """正文是唯一持久化真相：``shots`` / ``references`` 写入即被 strict 模型拒绝。"""
    with pytest.raises(ValidationError):
        _make_unit(shots=[{"text": "镜头一"}])
    with pytest.raises(ValidationError):
        _make_unit(references=[{"type": "character", "name": "张三"}])


@pytest.mark.unit
def test_reference_video_unit_empty_text_only_allowed_as_replan_shell():
    """空正文只允许配 needs_replan=True 且 0 秒（迁移遗留的问题壳）。"""
    shell = _make_unit(text="", duration_seconds=0, needs_replan=True)
    assert shell.needs_replan is True
    assert shell.duration_seconds == 0

    with pytest.raises(ValidationError):
        _make_unit(text="")
    with pytest.raises(ValidationError):
        _make_unit(text="  \n ", duration_seconds=0, needs_replan=False)
    with pytest.raises(ValidationError):
        _make_unit(text="", duration_seconds=8, needs_replan=True)


@pytest.mark.unit
def test_reference_video_unit_transition_enum():
    with pytest.raises(ValidationError):
        _make_unit(transition_to_next="wipe")


@pytest.mark.unit
def test_reference_video_script_valid():
    script = ReferenceVideoScript(
        title="江湖夜话",
        content_mode="narration",
        novel=NovelInfo(title="江湖行", chapter="第一回"),
        video_units=[_make_unit()],
    )
    # 剧本只承载"内容类型"维度；"视频来源"是项目级路线，剧本不携带
    assert script.content_mode == "narration"
    assert not hasattr(script, "generation_mode")
    assert len(script.video_units) == 1


@pytest.mark.unit
def test_reference_video_script_accepts_drama_content_mode():
    script = ReferenceVideoScript(
        title="剧集",
        content_mode="drama",
        novel=NovelInfo(title="x", chapter="x"),
        video_units=[_make_unit()],
    )
    assert script.content_mode == "drama"


@pytest.mark.unit
def test_reference_video_script_rejects_legacy_reference_video_content_mode():
    """content_mode 不再允许 reference_video（它属于项目级 generation_mode 维度）。"""
    with pytest.raises(ValidationError):
        ReferenceVideoScript(
            title="x",
            content_mode="reference_video",
            novel=NovelInfo(title="x", chapter="x"),
            video_units=[_make_unit()],
        )


@pytest.mark.unit
def test_reference_video_unit_duration_is_independent_of_text_length():
    """unit 时长是唯一真相：不与正文长度挂钩，取值只受结构区间约束。"""
    assert _make_unit(duration_seconds=12).duration_seconds == 12
    assert _make_unit(text="一行", duration_seconds=120).duration_seconds == 120


@pytest.mark.unit
def test_reference_video_unit_rejects_duration_out_of_structural_range():
    with pytest.raises(ValidationError):
        _make_unit(duration_seconds=0)
    with pytest.raises(ValidationError):
        _make_unit(duration_seconds=9999)
