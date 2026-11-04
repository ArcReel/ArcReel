"""端点清单自洽，最低应用版本的下限按清单推导。"""

from __future__ import annotations

import pytest

from arcreel_catalog_core import (
    BUILTIN_ENDPOINTS,
    BUILTIN_PROVIDER_ENDPOINTS,
    ENDPOINT_DECLARATIONS_SINCE,
    MEDIA_TYPES,
    BuiltinEndpoint,
    endpoint_version_floor,
)
from arcreel_catalog_core import endpoints as manifest


def test_provider_options_reference_declared_endpoints_of_their_media_type():
    for provider_id, by_media in BUILTIN_PROVIDER_ENDPOINTS.items():
        for media_type, options in by_media.items():
            assert media_type in MEDIA_TYPES, (provider_id, media_type)
            assert options.default in options.endpoints, (provider_id, media_type)
            assert len(set(options.endpoints)) == len(options.endpoints), (provider_id, media_type)
            for endpoint in options.endpoints:
                assert BUILTIN_ENDPOINTS[endpoint].media_type == media_type, (provider_id, endpoint)


def test_every_endpoint_is_selectable_somewhere_and_versions_are_well_formed():
    selectable = {
        endpoint
        for by_media in BUILTIN_PROVIDER_ENDPOINTS.values()
        for options in by_media.values()
        for endpoint in options.endpoints
    }
    versions = [endpoint.since for endpoint in BUILTIN_ENDPOINTS.values()] + [ENDPOINT_DECLARATIONS_SINCE]

    assert selectable == set(BUILTIN_ENDPOINTS)
    assert all(manifest.is_app_version(version) for version in versions if version is not None)


def test_baseline_endpoints_set_no_floor():
    assert endpoint_version_floor("ark", "video", "ark-seedance-2") is None


@pytest.mark.parametrize(
    ("provider_id", "media_type", "endpoint", "expected"),
    [
        pytest.param("ark", "video", "ark-seedance-3", "0.41.0", id="起始版本高于读取端点声明的版本"),
        pytest.param("ark", "video", "ark-seedance-2", "0.40.1", id="多端点的供应商计入读取端点声明的版本"),
        pytest.param("openai", "video", "openai-video", None, id="单端点的供应商不计入读取端点声明的版本"),
        pytest.param("ark", "video", "ark-seedance-4", "0.100.0", id="版本按数字比较"),
    ],
)
def test_floor_takes_highest_of_start_version_and_declaration_reading_version(
    monkeypatch: pytest.MonkeyPatch, provider_id: str, media_type: str, endpoint: str, expected: str | None
):
    monkeypatch.setattr(
        manifest,
        "BUILTIN_ENDPOINTS",
        {
            **BUILTIN_ENDPOINTS,
            "ark-seedance-3": BuiltinEndpoint("video", since="0.41.0"),
            "ark-seedance-4": BuiltinEndpoint("video", since="0.100.0"),
        },
    )
    monkeypatch.setattr(manifest, "ENDPOINT_DECLARATIONS_SINCE", "0.40.1")

    assert endpoint_version_floor(provider_id, media_type, endpoint) == expected
