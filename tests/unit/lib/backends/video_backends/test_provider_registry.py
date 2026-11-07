"""Unit tests for PROVIDER_REGISTRY fields and registration completeness; model lists are read through catalog queries."""

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
        "video": "doubao-seedance-2.0-mini",
    }
    for mid, m in models.items():
        if m.media_type == "video":
            assert m.supported_durations, f"{mid} missing supported_durations"
            assert m.resolutions, f"{mid} missing resolutions"


def test_ark_agent_plan_baseline_models_present() -> None:
    baseline = {
        "doubao-seed-2.0-mini",
        "doubao-seed-2.1-lite",
        "doubao-seedream-5-0-pro",
        "doubao-seedream-5.0-flash",
        "doubao-seedance-2.5",
        "doubao-seedance-2.0",
        "doubao-seedance-2.0-fast",
        "doubao-seedance-2.0-mini",
    }
    assert baseline.issubset(set(catalog.list_models("ark-agent-plan")))


def test_ark_agent_plan_fast_has_no_1080p() -> None:
    model = catalog.get_model("ark-agent-plan", "doubao-seedance-2.0-fast")
    assert model is not None
    assert model.resolutions == ["480p", "720p"]


def test_kling_credentials_and_base_url() -> None:
    """Kling three-key required/secret key declarations (choose api_key alone or access_key+secret_key together), plus
    default base_url (the domestic host migrated to api-beijing)."""
    p = PROVIDER_REGISTRY["kling"]
    assert p.required_keys == ["api_key", "access_key", "secret_key"]
    assert p.secret_keys == ["api_key", "access_key", "secret_key"]
    assert "base_url" in p.optional_keys
    assert p.default_base_url == "https://api-beijing.klingai.com/v1"


def test_kling_credential_groups_api_key_or_dual_secret() -> None:
    """Alternative credential groups: api_key alone, or access_key+secret_key together."""
    p = PROVIDER_REGISTRY["kling"]
    assert p.credential_groups == [["api_key"], ["access_key", "secret_key"]]


def test_provider_meta_credential_groups_default_empty() -> None:
    """Providers without credential_groups (the vast majority) retain the empty-list default with unchanged semantics."""
    assert PROVIDER_REGISTRY["gemini-aistudio"].credential_groups == []
    assert PROVIDER_REGISTRY["dashscope"].credential_groups == []


def test_kling_declares_image_and_video_max_workers() -> None:
    """Kling covers image and video lanes; optional_keys must declare both concurrency-limit fields."""
    p = PROVIDER_REGISTRY["kling"]
    assert {"image", "video"} <= set(catalog.provider_media_types("kling"))
    assert "image_max_workers" in p.optional_keys
    assert "video_max_workers" in p.optional_keys


def test_kling_default_video_model_v2_5_turbo() -> None:
    """JWT direct-video default model kling-v2-5-turbo has complete capability declarations."""
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
    """Image models: default kling-image-o1; the v3-omni image model retains an alias key."""
    assert catalog.default_model("kling", "image") == "kling-image-o1"
    o1 = catalog.get_model("kling", "kling-image-o1")
    assert o1 is not None
    assert o1.media_type == "image"
    assert o1.capabilities == ["text_to_image", "image_to_image"]
    assert o1.resolutions == ["1K", "2K"]
    # Ordinary model: no alias; the key is the API name.
    assert o1.api_model_name is None

    # Dual-modality model: an image alias avoids colliding with the video key; api_model_name points to the real API name.
    omni = catalog.get_model("kling", "kling-v3-omni-image")
    assert omni is not None
    assert omni.media_type == "image"
    assert omni.api_model_name == "kling-v3-omni"
    assert omni.resolutions == ["1K", "2K", "4K"]
    video_omni = catalog.get_model("kling", "kling-v3-omni")
    assert video_omni is None or video_omni.media_type == "video"


def test_kling_video_backend_registered() -> None:
    """The Kling video backend self-registers in the video registry (JWT direct connection)."""
    from lib.backends.video_backends.kling import KlingVideoBackend
    from lib.backends.video_backends.registry import _BACKEND_FACTORIES as video_reg

    assert video_reg["kling"] is KlingVideoBackend


def test_ark_agent_plan_backend_registered() -> None:
    """Reuse the Ark backend class for ark-agent-plan; video registers by Seedance endpoint, shared by both Ark providers."""
    from lib.backends.image_backends.ark import ArkImageBackend
    from lib.backends.image_backends.registry import _BACKEND_FACTORIES as image_reg
    from lib.backends.text_backends.ark import ArkTextBackend
    from lib.backends.text_backends.registry import _BACKEND_FACTORIES as text_reg
    from lib.backends.video_backends.ark import SEEDANCE_BACKENDS
    from lib.backends.video_backends.registry import _BACKEND_FACTORIES as video_reg

    assert image_reg["ark-agent-plan"] is ArkImageBackend
    assert {name: video_reg[name] for name in SEEDANCE_BACKENDS} == dict(SEEDANCE_BACKENDS)
    assert text_reg["ark-agent-plan"] is ArkTextBackend
