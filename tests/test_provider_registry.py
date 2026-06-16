"""PROVIDER_REGISTRY 字段与注册完整性单元测试。"""

from lib.config.registry import PROVIDER_REGISTRY


def test_ark_has_default_base_url() -> None:
    ark = PROVIDER_REGISTRY["ark"]
    assert ark.default_base_url == "https://ark.cn-beijing.volces.com/api/v3"


def test_provider_meta_default_base_url_optional() -> None:
    gemini = PROVIDER_REGISTRY["gemini-aistudio"]
    assert gemini.default_base_url is None


def test_ark_agent_plan_registered() -> None:
    p = PROVIDER_REGISTRY["ark-agent-plan"]
    assert p.default_base_url == "https://ark.cn-beijing.volces.com/api/plan/v3"
    assert "api_key" in p.required_keys
    defaults_by_media = {m.media_type: mid for mid, m in p.models.items() if m.default}
    assert defaults_by_media == {
        "text": "doubao-seed-2.0-lite",
        "image": "doubao-seedream-5.0-lite",
        "video": "doubao-seedance-2.0-fast",
    }
    for mid, m in p.models.items():
        if m.media_type == "video":
            assert m.supported_durations, f"{mid} missing supported_durations"
            assert m.resolutions, f"{mid} missing resolutions"


def test_ark_agent_plan_baseline_models_present() -> None:
    p = PROVIDER_REGISTRY["ark-agent-plan"]
    baseline = {
        "doubao-seed-2.0-mini",
        "doubao-seed-2.0-lite",
        "doubao-seed-2.0-pro",
        "doubao-seed-2.0-code",
        "doubao-seedream-5.0-lite",
        "doubao-seedance-1.5-pro",
        "doubao-seedance-2.0",
        "doubao-seedance-2.0-fast",
    }
    assert baseline.issubset(set(p.models.keys()))


def test_ark_agent_plan_model_id_format_differs_from_ark() -> None:
    ark_ids = set(PROVIDER_REGISTRY["ark"].models.keys())
    agent_plan_ids = set(PROVIDER_REGISTRY["ark-agent-plan"].models.keys())
    assert not (ark_ids & agent_plan_ids), "ark vs ark-agent-plan 模型 ID 命名不同，不应重叠"


def test_kling_credentials_and_base_url() -> None:
    """可灵双 secret required/secret key + 默认 base_url（JWT 直连，见 ADR 0037）。"""
    p = PROVIDER_REGISTRY["kling"]
    assert p.required_keys == ["access_key", "secret_key"]
    assert p.secret_keys == ["access_key", "secret_key"]
    assert p.default_base_url == "https://api.klingai.com/v1"


def test_kling_default_video_model_v2_5_turbo() -> None:
    """JWT 直连视频默认模型 kling-v2-5-turbo，能力声明齐备。"""
    p = PROVIDER_REGISTRY["kling"]
    assert "kling-v2-5-turbo" in p.models
    turbo = p.models["kling-v2-5-turbo"]
    assert turbo.media_type == "video"
    assert turbo.default is True
    assert turbo.supported_durations == [5, 10]
    assert turbo.resolutions, "默认视频模型须声明 resolutions"
    assert turbo.pricing is not None


def test_kling_image_models() -> None:
    """图像模型：默认 kling-image-o1（按张 flat ¥0.2），v3-omni 别名键按分辨率（4K ¥0.4）。"""
    from lib.pricing.types import PerImageByResolution, PerImageFlat

    p = PROVIDER_REGISTRY["kling"]
    assert "image" in p.media_types

    o1 = p.models["kling-image-o1"]
    assert o1.media_type == "image"
    assert o1.default is True
    assert o1.capabilities == ["text_to_image", "image_to_image"]
    assert o1.resolutions == ["1K", "2K"]
    # 普通模型：无别名，键名即 API 名。
    assert o1.api_model_name is None
    assert isinstance(o1.pricing, PerImageFlat)
    assert o1.pricing.currency == "CNY"
    assert o1.pricing.rates["kling-image-o1"] == 0.2

    # 两栖模型：图像条目用别名键避开与视频条目撞主键，api_model_name 回指真实 API 名。
    omni = p.models["kling-v3-omni-image"]
    assert omni.media_type == "image"
    assert omni.api_model_name == "kling-v3-omni"
    assert omni.resolutions == ["1K", "2K", "4K"]
    assert isinstance(omni.pricing, PerImageByResolution)
    # 计费查表键 = registry 键名（result.model），非 API 名。
    assert omni.pricing.rates["kling-v3-omni-image"] == {"1K": 0.2, "2K": 0.2, "4K": 0.4}
    # 视频主键 kling-v3-omni 不归本片（图像片不注册视频条目）。
    video_omni = p.models.get("kling-v3-omni")
    assert video_omni is None or video_omni.media_type == "video"


def test_kling_video_backend_registered() -> None:
    """可灵视频后端在 video registry 自注册（JWT 直连）。"""
    import lib.video_backends  # noqa: F401  触发自注册
    from lib.video_backends.kling import KlingVideoBackend
    from lib.video_backends.registry import _BACKEND_FACTORIES as video_reg

    assert video_reg["kling"] is KlingVideoBackend


def test_ark_agent_plan_backend_registered() -> None:
    """复用现有 ark backend 类支持 ark-agent-plan provider。"""
    import lib.image_backends  # noqa: F401  触发自注册
    import lib.text_backends  # noqa: F401
    import lib.video_backends  # noqa: F401
    from lib.image_backends.ark import ArkImageBackend
    from lib.image_backends.registry import _BACKEND_FACTORIES as image_reg
    from lib.text_backends.ark import ArkTextBackend
    from lib.text_backends.registry import _BACKEND_FACTORIES as text_reg
    from lib.video_backends.ark import ArkVideoBackend
    from lib.video_backends.registry import _BACKEND_FACTORIES as video_reg

    assert image_reg["ark-agent-plan"] is ArkImageBackend
    assert video_reg["ark-agent-plan"] is ArkVideoBackend
    assert text_reg["ark-agent-plan"] is ArkTextBackend
