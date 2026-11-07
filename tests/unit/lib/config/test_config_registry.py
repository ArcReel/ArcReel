import pytest

from lib.catalog import query as catalog
from lib.config.registry import (
    PROVIDER_REGISTRY,
    ProviderMeta,
)


def test_all_providers_registered():
    assert set(PROVIDER_REGISTRY.keys()) == {
        "gemini-aistudio",
        "gemini-vertex",
        "ark",
        "ark-agent-plan",
        "grok",
        "openai",
        "vidu",
        "dashscope",
        "minimax",
        "kling",
        "agnes",
    }


def test_provider_meta_fields():
    meta = PROVIDER_REGISTRY["gemini-aistudio"]
    assert isinstance(meta, ProviderMeta)
    assert meta.display_name == "AI Studio"
    assert "api_key" in meta.required_keys
    assert "api_key" in meta.secret_keys


def test_required_keys_are_subset_of_all_keys():
    for name, meta in PROVIDER_REGISTRY.items():
        all_keys = set(meta.required_keys) | set(meta.optional_keys)
        for rk in meta.required_keys:
            assert rk in all_keys, f"{name}: required key {rk} not in all keys"


def test_secret_keys_are_subset_of_required_or_optional():
    for name, meta in PROVIDER_REGISTRY.items():
        all_keys = set(meta.required_keys) | set(meta.optional_keys)
        for sk in meta.secret_keys:
            assert sk in all_keys, f"{name}: secret key {sk} not in all keys"


# Media lanes → optional concurrency keys: each lane covered by provider catalog models must be configurable.
_LANE_WORKER_KEY = {
    "image": "image_max_workers",
    "video": "video_max_workers",
    "audio": "audio_max_workers",
}


def test_optional_keys_cover_every_supported_lane_worker():
    """Every supported provider lane declares corresponding *_max_workers in optional_keys."""
    for name, meta in PROVIDER_REGISTRY.items():
        optional = set(meta.optional_keys)
        for media_type in catalog.provider_media_types(name):
            worker_key = _LANE_WORKER_KEY.get(media_type)
            if worker_key is None:
                continue
            assert worker_key in optional, f"{name}: 支持 {media_type} lane 但 optional_keys 未声明 {worker_key}"


def test_optional_keys_have_no_worker_for_unsupported_lane():
    """Unsupported lanes must not declare *_max_workers, avoiding useless settings fields."""
    for name, meta in PROVIDER_REGISTRY.items():
        supported_worker_keys = {
            _LANE_WORKER_KEY[m] for m in catalog.provider_media_types(name) if m in _LANE_WORKER_KEY
        }
        for key in meta.optional_keys:
            if key in _LANE_WORKER_KEY.values():
                assert key in supported_worker_keys, f"{name}: optional_keys 含 {key} 但 provider 不支持对应 lane"


class TestCredentialGroups:
    """Fail-fast validation for mutually exclusive credential groups."""

    def test_default_empty(self):
        meta = ProviderMeta(display_name="t", required_keys=["api_key"], secret_keys=["api_key"])
        assert meta.credential_groups == []

    def test_group_keys_must_be_subset_of_required_and_secret(self):
        with pytest.raises(ValueError, match="credential_groups"):
            ProviderMeta(
                display_name="t",
                required_keys=["api_key"],
                secret_keys=["api_key"],
                credential_groups=[["api_key"], ["access_key"]],
            )

    def test_valid_groups_accepted(self):
        meta = ProviderMeta(
            display_name="t",
            required_keys=["api_key", "access_key", "secret_key"],
            secret_keys=["api_key", "access_key", "secret_key"],
            credential_groups=[["api_key"], ["access_key", "secret_key"]],
        )
        assert meta.credential_groups == [["api_key"], ["access_key", "secret_key"]]

    def test_empty_group_rejected(self):
        with pytest.raises(ValueError, match="空分组"):
            ProviderMeta(
                display_name="t",
                required_keys=["api_key", "access_key", "secret_key"],
                secret_keys=["api_key", "access_key", "secret_key"],
                credential_groups=[["api_key"], []],
            )

    def test_uncovered_key_rejected(self):
        with pytest.raises(ValueError, match="未覆盖"):
            ProviderMeta(
                display_name="t",
                required_keys=["api_key", "access_key", "secret_key"],
                secret_keys=["api_key", "access_key", "secret_key"],
                credential_groups=[["api_key"]],
            )


class TestFullyCoveredCredentialGroups:
    """ProviderMeta.fully_covered_credential_groups truth table for group switching."""

    def _kling_meta(self) -> ProviderMeta:
        return ProviderMeta(
            display_name="t",
            required_keys=["api_key", "access_key", "secret_key"],
            secret_keys=["api_key", "access_key", "secret_key"],
            credential_groups=[["api_key"], ["access_key", "secret_key"]],
        )

    def test_no_groups_declared_always_empty(self):
        meta = ProviderMeta(display_name="t", required_keys=["api_key"], secret_keys=["api_key"])
        assert meta.fully_covered_credential_groups({"api_key": "k"}) == []

    def test_single_group_fully_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": "k"}) == [["api_key"]]

    def test_dual_key_group_fully_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"access_key": "ak", "secret_key": "sk"}) == [
            ["access_key", "secret_key"]
        ]

    def test_dual_key_group_partially_submitted_not_matched(self):
        """Supplying only one group key, such as secret_key rotation, does not fully cover the group."""
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"secret_key": "sk"}) == []

    def test_both_groups_fully_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": "k", "access_key": "ak", "secret_key": "sk"}) == [
            ["api_key"],
            ["access_key", "secret_key"],
        ]

    def test_empty_string_not_counted_as_covering(self):
        """Empty strings count as unsupplied, not group coverage."""
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": ""}) == []

    def test_none_not_counted_as_covering(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": None}) == []

    def test_nothing_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({}) == []


class TestDefaultConcurrency:
    def test_valid_declaration(self):
        meta = ProviderMeta(
            display_name="T",
            required_keys=[],
            default_concurrency={"video": 1, "image": 5},
        )
        assert meta.default_concurrency == {"video": 1, "image": 5}

    def test_lane_the_provider_does_not_cover_is_allowed(self):
        # Valid lane names are harmless even without provider models (zeroed at projection), so do not reject.
        meta = ProviderMeta(display_name="T", required_keys=[], default_concurrency={"image": 2})
        assert meta.default_concurrency == {"image": 2}

    def test_unknown_lane_raises(self):
        with pytest.raises(ValueError, match="未知 lane"):
            ProviderMeta(
                display_name="T",
                required_keys=[],
                default_concurrency={"vidoe": 1},
            )

    # True subclasses int and equals 1; explicitly reject configuration mistakes like default_concurrency={"video": True}
    # instead of silently treating them as concurrency 1.
    @pytest.mark.parametrize("bad_limit", [0, -1, "1", 3.0, True])
    def test_non_positive_int_raises(self, bad_limit):
        with pytest.raises(ValueError, match=">=1 的整数"):
            ProviderMeta(
                display_name="T",
                required_keys=[],
                default_concurrency={"video": bad_limit},
            )


def test_dashscope_declares_wan3_base_url_key():
    # wan3’s dedicated maas domain has its own key; merging into base_url would redirect other models too.
    meta = PROVIDER_REGISTRY["dashscope"]
    assert "wan3_base_url" in meta.optional_keys
    assert "base_url" in meta.optional_keys
