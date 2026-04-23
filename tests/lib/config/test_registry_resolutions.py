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
                if not minfo.resolutions and not (pid == "ark" and mid.startswith("doubao-seedream")):
                    missing.append(f"{pid}/{mid}")
    assert missing == [], f"以下 image/video 模型缺少 resolutions: {missing}"


def test_text_models_have_empty_resolutions():
    for pid, meta in PROVIDER_REGISTRY.items():
        for mid, minfo in meta.models.items():
            if minfo.media_type == "text":
                assert minfo.resolutions == [], f"{pid}/{mid}: text 模型不应声明 resolutions"


def test_ark_seedream_image_resolutions_empty():
    """Ark Seedream 当前不传分辨率，留空 → UI 不展示下拉。"""
    ark = PROVIDER_REGISTRY["ark"]
    for mid, minfo in ark.models.items():
        if minfo.media_type == "image":
            assert minfo.resolutions == [], f"{mid}: Ark Seedream 应留空"
