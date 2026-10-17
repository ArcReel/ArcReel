"""PROVIDER_REGISTRY 字段与注册完整性单元测试；模型清单经目录查询读取。"""

from lib.catalog import query as catalog
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
    models = catalog.list_models("ark-agent-plan")
    defaults_by_media = {m.media_type: mid for mid, m in models.items() if m.default}
    assert defaults_by_media == {
        "text": "doubao-seed-2.0-lite",
        "image": "doubao-seedream-5.0-lite",
        "video": "doubao-seedance-2.0-mini",
    }
    for mid, m in models.items():
        if m.media_type == "video":
            assert m.supported_durations, f"{mid} missing supported_durations"
            assert m.resolutions, f"{mid} missing resolutions"


def test_ark_agent_plan_baseline_models_present() -> None:
    baseline = {
        "doubao-seed-2.0-mini",
        "doubao-seed-2.0-lite",
        "doubao-seed-2.0-pro",
        "doubao-seed-2.0-code",
        "doubao-seedream-5.0-lite",
        "doubao-seedance-2.0",
        "doubao-seedance-2.0-fast",
        "doubao-seedance-2.0-mini",
    }
    assert baseline.issubset(set(catalog.list_models("ark-agent-plan")))


def test_ark_agent_plan_fast_has_no_1080p() -> None:
    model = catalog.get_model("ark-agent-plan", "doubao-seedance-2.0-fast")
    assert model is not None
    assert model.resolutions == ["480p", "720p"]


def test_ark_agent_plan_model_id_format_differs_from_ark() -> None:
    ark_ids = set(catalog.list_models("ark"))
    agent_plan_ids = set(catalog.list_models("ark-agent-plan"))
    assert not (ark_ids & agent_plan_ids), "ark vs ark-agent-plan 模型 ID 命名不同，不应重叠"


def test_kling_credentials_and_base_url() -> None:
    """可灵三键 required/secret key（api_key 单键 / access_key+secret_key 双键二选一）+
    默认 base_url（国内域名已迁移至 api-beijing）。"""
    p = PROVIDER_REGISTRY["kling"]
    assert p.required_keys == ["api_key", "access_key", "secret_key"]
    assert p.secret_keys == ["api_key", "access_key", "secret_key"]
    assert "base_url" in p.optional_keys
    assert p.default_base_url == "https://api-beijing.klingai.com/v1"


def test_kling_credential_groups_api_key_or_dual_secret() -> None:
    """凭证二选一分组：api_key 单键，或 access_key+secret_key 双键。"""
    p = PROVIDER_REGISTRY["kling"]
    assert p.credential_groups == [["api_key"], ["access_key", "secret_key"]]


def test_provider_meta_credential_groups_default_empty() -> None:
    """未声明 credential_groups 的 provider（绝大多数）保持空列表默认，语义不变。"""
    assert PROVIDER_REGISTRY["gemini-aistudio"].credential_groups == []
    assert PROVIDER_REGISTRY["dashscope"].credential_groups == []


def test_kling_declares_image_and_video_max_workers() -> None:
    """可灵覆盖 image + video 两条 lane，optional_keys 须声明两者的并发上限字段。"""
    p = PROVIDER_REGISTRY["kling"]
    assert {"image", "video"} <= set(catalog.provider_media_types("kling"))
    assert "image_max_workers" in p.optional_keys
    assert "video_max_workers" in p.optional_keys


def test_kling_default_video_model_v2_5_turbo() -> None:
    """JWT 直连视频默认模型 kling-v2-5-turbo，能力声明齐备。"""
    assert catalog.default_model("kling", "video") == "kling-v2-5-turbo"
    turbo = catalog.get_model("kling", "kling-v2-5-turbo")
    assert turbo is not None
    assert turbo.media_type == "video"
    assert turbo.supported_durations == [5, 10]
    assert turbo.resolutions, "默认视频模型须声明 resolutions"
    price = catalog.provider_model_price("kling", "kling-v2-5-turbo", "video")
    assert price is not None
    assert price.price is not None


def test_kling_image_models() -> None:
    """图像模型：默认 kling-image-o1，v3-omni 的图像模型沿用别名键。"""
    assert catalog.default_model("kling", "image") == "kling-image-o1"
    o1 = catalog.get_model("kling", "kling-image-o1")
    assert o1 is not None
    assert o1.media_type == "image"
    assert o1.capabilities == ["text_to_image", "image_to_image"]
    assert o1.resolutions == ["1K", "2K"]
    # 普通模型：无别名，键名即 API 名。
    assert o1.api_model_name is None

    # 两栖模型：图像模型用别名键避开与视频模型撞键，api_model_name 回指真实 API 名。
    omni = catalog.get_model("kling", "kling-v3-omni-image")
    assert omni is not None
    assert omni.media_type == "image"
    assert omni.api_model_name == "kling-v3-omni"
    assert omni.resolutions == ["1K", "2K", "4K"]
    video_omni = catalog.get_model("kling", "kling-v3-omni")
    assert video_omni is None or video_omni.media_type == "video"


def test_kling_video_backend_registered() -> None:
    """可灵视频后端在 video registry 自注册（JWT 直连）。"""
    from lib.backends.video_backends.kling import KlingVideoBackend
    from lib.backends.video_backends.registry import _BACKEND_FACTORIES as video_reg

    assert video_reg["kling"] is KlingVideoBackend


def test_ark_agent_plan_backend_registered() -> None:
    """复用现有 ark backend 类支持 ark-agent-plan provider。"""
    from lib.backends.image_backends.ark import ArkImageBackend
    from lib.backends.image_backends.registry import _BACKEND_FACTORIES as image_reg
    from lib.backends.text_backends.ark import ArkTextBackend
    from lib.backends.text_backends.registry import _BACKEND_FACTORIES as text_reg
    from lib.backends.video_backends.ark import ArkVideoBackend
    from lib.backends.video_backends.registry import _BACKEND_FACTORIES as video_reg

    assert image_reg["ark-agent-plan"] is ArkImageBackend
    assert video_reg["ark-agent-plan"] is ArkVideoBackend
    assert text_reg["ark-agent-plan"] is ArkTextBackend
