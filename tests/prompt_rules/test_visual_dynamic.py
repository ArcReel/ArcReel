from lib.prompt_rules.visual_dynamic import (
    IMAGE_DYNAMIC_PATCH,
    VIDEO_DYNAMIC_PATCH,
)


def test_image_patch_keywords() -> None:
    assert "微表情" in IMAGE_DYNAMIC_PATCH
    assert "物理飘动" in IMAGE_DYNAMIC_PATCH
    assert "环境必须是活的" in IMAGE_DYNAMIC_PATCH
    assert "内容融合" in IMAGE_DYNAMIC_PATCH
    assert "200 字以内" in IMAGE_DYNAMIC_PATCH


def test_video_patch_keywords() -> None:
    assert "肢体位移" in VIDEO_DYNAMIC_PATCH
    assert "微表情转换" in VIDEO_DYNAMIC_PATCH
    assert "物理环境互动" in VIDEO_DYNAMIC_PATCH
    assert "拒绝静态描写" in VIDEO_DYNAMIC_PATCH
    assert "150 字以内" in VIDEO_DYNAMIC_PATCH
