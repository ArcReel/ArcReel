"""Tests for apply_unit_video_assets."""

from __future__ import annotations

import pytest


def test_apply_unit_video_assets_distinguishes_failures():
    """结构损坏与 unit 不存在抛不同异常：还原侧据此区分「脏脚本告警」与「正常跳过」。

    结构损坏的两类异常会经 upload_unit_video 路由回传终端用户，故须带具体 i18n key
    （默认兜底 key 会让 en/vi 用户只看到无信息的通用句）。
    """
    from lib.script_editor import ScriptEditError
    from server.services.reference_video_tasks import apply_unit_video_assets

    with pytest.raises(ScriptEditError) as unit_lists_broken:
        apply_unit_video_assets({"video_units": "broken"}, "E1U1", video_uri=None, thumb_rel=None)
    assert unit_lists_broken.value.key == "script_edit_unit_lists_invalid"
    with pytest.raises(ScriptEditError) as unit_lists_missing:
        apply_unit_video_assets({}, "E1U1", video_uri=None, thumb_rel=None)
    assert unit_lists_missing.value.key == "script_edit_unit_lists_invalid"
    with pytest.raises(ScriptEditError) as assets_broken:
        apply_unit_video_assets(
            {"video_units": [{"unit_id": "E1U1", "generated_assets": "broken"}]},
            "E1U1",
            video_uri=None,
            thumb_rel=None,
        )
    assert assets_broken.value.key == "script_edit_generated_assets_invalid"
    with pytest.raises(KeyError):
        apply_unit_video_assets({"video_units": []}, "E1U1", video_uri=None, thumb_rel=None)

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {"video_uri": "https://old"}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel="reference_videos/thumbnails/E1U1.jpg")
    ga = script["video_units"][0]["generated_assets"]
    assert ga["video_clip"] == "reference_videos/E1U1.mp4"
    assert "video_uri" not in ga
    assert ga["video_thumbnail"] == "reference_videos/thumbnails/E1U1.jpg"
    assert ga["status"] == "completed"


def test_apply_unit_video_assets_stamps_video_generated_at():
    """每次写回 video_clip 都机械戳 video_generated_at（存量过渡横幅的计数依据）。"""
    from server.services.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    first_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(first_stamp, str) and first_stamp

    # 重新生成（第二次写回）必须刷新时间戳，不能沿用旧值
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    second_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(second_stamp, str) and second_stamp


def test_apply_unit_video_assets_preserves_legacy_source_signature_without_reading_it():
    """遗留来源签名只是历史资产键；生成写回应原样保留，不能再新增、比较或清理它。"""
    from server.services.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {"source_signature": "legacy"}}]}
    written = apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    assert written is None
    assert script["video_units"][0]["generated_assets"]["source_signature"] == "legacy"


def test_apply_unit_video_assets_honors_explicit_generated_at():
    """版本还原传入被还原版本的原始入库时间，不把旧内容洗成「刚生成」。"""
    from server.services.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)

    restored_at = "2020-01-01T00:00:00+00:00"
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None, generated_at=restored_at)
    assert script["video_units"][0]["generated_assets"]["video_generated_at"] == restored_at
