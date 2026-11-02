"""控制面签名 V4：沿用官方示例请求，以虚构凭证核对固定签名向量。

示例来源：https://docs.volcengine.com/docs/6369/67269 （ListBill，signed headers 为 host;x-date）。

AK 与 SK 均使用 TEST- 前缀，避免样例命中 secret scanning；期望签名按 V4 标准 HMAC 推导。
"""

from __future__ import annotations

from datetime import UTC, datetime

from lib.asset_library.control_plane import sign_request


def test_signature_matches_fixed_example_with_test_credentials():
    headers = sign_request(
        method="POST",
        host="billing.volcengineapi.com",
        query={"Action": "ListBill", "Version": "2022-01-01"},
        headers={},
        body=b'{"Limit":10,"BillPeriod":"2023-08"}',
        access_key_id="TEST-ACCESS-KEY-ID",
        secret_access_key="TEST-SECRET-ACCESS-KEY",
        region="cn-beijing",
        service="billing",
        now=datetime(2025, 3, 29, 18, 9, 37, tzinfo=UTC),
    )

    assert headers["X-Date"] == "20250329T180937Z"
    assert headers["Authorization"] == (
        "HMAC-SHA256 Credential=TEST-ACCESS-KEY-ID/20250329/cn-beijing/billing/request, "
        "SignedHeaders=host;x-date, "
        "Signature=1f493887ca3634f6c76f32f974849b2a9e2566d818303112450508961e8d6288"
    )
