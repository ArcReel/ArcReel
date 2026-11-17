"""目录数据格式包里的端点清单与应用代码一致，起始版本按 release-please 的版本号填写（docs/adr/0103）。"""

from __future__ import annotations

import json
from pathlib import Path

from packaging.version import Version

from arcreel_catalog_core import BUILTIN_ENDPOINTS, BUILTIN_PROVIDER_ENDPOINTS, ENDPOINT_DECLARATIONS_SINCE
from lib.backends.backend_assembly.specs import PROVIDER_ENDPOINTS, endpoint_media_type

REPO_ROOT = Path(__file__).resolve().parents[5]


def test_endpoint_manifest_matches_registry_and_provider_declarations():
    """清单列出的端点、媒体类型，以及每个内置供应商按媒体类型的可选端点与默认端点，都与代码相同。"""
    code_options = {
        (declared.provider_id, declared.media_type): (set(declared.endpoints), declared.default)
        for declared in PROVIDER_ENDPOINTS.values()
    }
    manifest_options = {
        (provider_id, media_type): (set(options.endpoints), options.default)
        for provider_id, by_media in BUILTIN_PROVIDER_ENDPOINTS.items()
        for media_type, options in by_media.items()
    }
    # 端点名的媒体类型取自端点注册表（与自定义供应商共用的端点）与内置专属端点表。
    code_endpoints = {
        endpoint: endpoint_media_type(endpoint)
        for declared in PROVIDER_ENDPOINTS.values()
        for endpoint in declared.endpoints
    }

    assert manifest_options == code_options
    assert {name: endpoint.media_type for name, endpoint in BUILTIN_ENDPOINTS.items()} == code_endpoints


def test_start_versions_do_not_exceed_next_release():
    """起始版本填合入 main 时 release-please 清单版本的下一个补丁号，不会高于它。"""
    released = Version(json.loads((REPO_ROOT / ".release-please-manifest.json").read_text())["."])
    next_patch = Version(f"{released.major}.{released.minor}.{released.micro + 1}")
    versions = [endpoint.since for endpoint in BUILTIN_ENDPOINTS.values()] + [ENDPOINT_DECLARATIONS_SINCE]

    assert [version for version in versions if version is not None and Version(version) > next_patch] == []
