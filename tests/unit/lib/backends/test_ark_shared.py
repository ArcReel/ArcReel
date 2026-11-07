"""Pure lib.backends.ark_shared tests, without real HTTP."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lib.backends.ark_shared import ARK_BASE_URL, ark_base_url, create_ark_client, resolve_ark_api_key


class TestBaseUrlNormalization:
    def test_default_base(self):
        assert ark_base_url(None) == ARK_BASE_URL

    def test_trailing_slash_stripped(self):
        assert ark_base_url("https://ark.cn-beijing.volces.com/api/v3/") == ARK_BASE_URL

    def test_multiple_trailing_slashes_stripped(self):
        assert ark_base_url("https://ark.cn-beijing.volces.com/api/v3//") == ARK_BASE_URL

    def test_no_trailing_slash_is_idempotent(self):
        assert ark_base_url(ARK_BASE_URL) == ARK_BASE_URL

    def test_whitespace_falls_back_to_default(self):
        # Truthy whitespace base_url strips before default fallback.
        assert ark_base_url("   ") == ARK_BASE_URL

    def test_surrounding_whitespace_stripped(self):
        assert ark_base_url("  https://ark.cn-beijing.volces.com/api/v3/  ") == ARK_BASE_URL

    def test_non_standard_path_preserved(self):
        # ark-agent-plan uses nonstandard /api/plan/v3; trim trailing slashes without rebuilding known suffixes,
        # avoiding duplicated .../api/plan/v3/api/v3 URLs.
        custom = "https://ark.cn-beijing.volces.com/api/plan/v3"
        assert ark_base_url(custom) == custom
        assert ark_base_url(custom + "/") == custom


class TestApiKeyResolution:
    def test_strips_and_returns(self):
        assert resolve_ark_api_key("  sk-abc  ") == "sk-abc"

    def test_missing_raises(self):
        with pytest.raises(ValueError, match=r"请到系统配置页填写 Ark API Key"):
            resolve_ark_api_key(None)

    def test_blank_raises(self):
        with pytest.raises(ValueError, match=r"请到系统配置页填写 Ark API Key"):
            resolve_ark_api_key("   ")


@contextmanager
def _recorded_ark_sdk() -> Generator[list[dict[str, Any]]]:
    """Ark SDK constructor recorder captures arguments and returns empty substitutes."""
    created: list[dict[str, Any]] = []

    def _create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return MagicMock()

    with patch("volcenginesdkarkruntime.Ark", _create):
        yield created


class TestCreateArkClient:
    def test_trailing_slash_normalized_before_client_construction(self):
        with _recorded_ark_sdk() as created:
            create_ark_client(api_key="k", base_url="https://ark.cn-beijing.volces.com/api/v3/")
        assert created == [{"base_url": ARK_BASE_URL, "api_key": "k"}]

    def test_default_base_url_when_omitted(self):
        with _recorded_ark_sdk() as created:
            create_ark_client(api_key="k")
        assert created == [{"base_url": ARK_BASE_URL, "api_key": "k"}]
