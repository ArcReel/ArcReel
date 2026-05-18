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
