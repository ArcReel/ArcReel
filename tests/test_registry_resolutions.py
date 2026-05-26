"""测试 ModelInfo.resolutions 字段与预置模型填充。"""

from lib.config.registry import PROVIDER_REGISTRY, ModelInfo


def test_model_info_has_resolutions_default_empty_list():
    info = ModelInfo(display_name="X", media_type="text", capabilities=[])
    assert info.resolutions == []


def test_all_image_video_models_have_resolutions_populated():
    missing: list[str] = []
    for pid, meta in PROVIDER_REGISTRY.items():
        for mid, minfo in meta.models.items():
            if minfo.media_type in ("image", "video"):
                if not minfo.resolutions:
                    missing.append(f"{pid}/{mid}")
    assert missing == [], f"以下 image/video 模型缺少 resolutions: {missing}"


def test_text_models_have_empty_resolutions():
    for pid, meta in PROVIDER_REGISTRY.items():
        for mid, minfo in meta.models.items():
            if minfo.media_type == "text":
                assert minfo.resolutions == [], f"{pid}/{mid}: text 模型不应声明 resolutions"


def test_ark_seedream_image_resolutions_include_4k():
    """Ark Seedream 图片模型声明 4K，供 UI 保存到 model_settings 后显式透传。"""
    for pid in ("ark", "ark-agent-plan"):
        meta = PROVIDER_REGISTRY[pid]
        for mid, minfo in meta.models.items():
            if minfo.media_type == "image":
                assert "4K" in minfo.resolutions, f"{pid}/{mid}: Ark Seedream 应支持 4K"
