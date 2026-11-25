"""目录数据格式包里的端点清单与应用代码一致，起始版本按 release-please 的版本号填写（docs/adr/0103）。"""

from __future__ import annotations

import json
from pathlib import Path

from packaging.version import Version

from arcreel_catalog_core import BUILTIN_ENDPOINTS, BUILTIN_PROVIDER_ENDPOINTS, ENDPOINT_DECLARATIONS_SINCE
from lib.backends.backend_assembly.specs import PROVIDER_ENDPOINT_SPECS
from lib.backends.builtin_endpoints import PROVIDER_ONLY_ENDPOINTS
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[5]


def test_endpoint_manifest_matches_assembly_and_custom_endpoints():
    manifest_specs = {
        (provider_id, media_type, endpoint)
        for provider_id, by_media in BUILTIN_PROVIDER_ENDPOINTS.items()
        for media_type, options in by_media.items()
        for endpoint in options.endpoints
    }
    assembly_specs = {(spec.provider_id, spec.media_type, spec.endpoint) for spec in PROVIDER_ENDPOINT_SPECS.values()}
    manifest_media = {endpoint: spec.media_type for endpoint, spec in BUILTIN_ENDPOINTS.items()}
    shared_endpoints = manifest_media.keys() - PROVIDER_ONLY_ENDPOINTS.keys()

    assert manifest_specs == assembly_specs
    assert manifest_media.keys() == {endpoint for _, _, endpoint in assembly_specs}
    assert all(manifest_media[endpoint] == media_type for _, media_type, endpoint in assembly_specs)
    assert {endpoint: manifest_media[endpoint] for endpoint in shared_endpoints} == {
        endpoint: ENDPOINT_REGISTRY[endpoint].media_type
        for endpoint in shared_endpoints
        if ENDPOINT_REGISTRY[endpoint].source == "builtin"
    }
    assert not (PROVIDER_ONLY_ENDPOINTS.keys() & ENDPOINT_REGISTRY.keys())


def test_start_versions_do_not_exceed_next_release():
    """起始版本填合入 main 时 release-please 清单版本的下一个补丁号，不会高于它。"""
    released = Version(json.loads((REPO_ROOT / ".release-please-manifest.json").read_text())["."])
    next_patch = Version(f"{released.major}.{released.minor}.{released.micro + 1}")
    versions = [endpoint.since for endpoint in BUILTIN_ENDPOINTS.values()] + [ENDPOINT_DECLARATIONS_SINCE]

    assert [version for version in versions if version is not None and Version(version) > next_patch] == []
