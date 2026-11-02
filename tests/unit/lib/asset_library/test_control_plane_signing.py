"""控制面签名 V4：用火山引擎官方文档的示例向量核对签名结果。

示例来源：https://docs.volcengine.com/docs/6369/67269 （ListBill，signed headers 为 host;x-date）。

Access Key ID 换成了虚构值：示例里的 AK 符合火山 AK 格式，会被 GitHub secret scanning 拦截推送。
V4 的 string-to-sign 与签名密钥都不含 AK，AK 只出现在 Authorization 的 Credential= 中，
所以签名仍应等于官方示例；Secret Access Key 参与派生签名密钥，必须保留官方示例值。
"""

from __future__ import annotations

from datetime import UTC, datetime

from lib.asset_library.control_plane import sign_request


def test_signature_matches_official_example():
    headers = sign_request(
        method="POST",
        host="billing.volcengineapi.com",
        query={"Action": "ListBill", "Version": "2022-01-01"},
        headers={},
        body=b'{"Limit":10,"BillPeriod":"2023-08"}',
        access_key_id="TEST-ACCESS-KEY-ID",
        secret_access_key="WkRZeE1EQmxPVGhsWWpWak5HVmtNbUUxTXpZeU9UVXlOMlE1TmpZeVlqTQ==",
        region="cn-beijing",
        service="billing",
        now=datetime(2025, 3, 29, 18, 9, 37, tzinfo=UTC),
    )

    assert headers["X-Date"] == "20250329T180937Z"
    assert headers["Authorization"] == (
        "HMAC-SHA256 Credential=TEST-ACCESS-KEY-ID/20250329/cn-beijing/billing/request, "
        "SignedHeaders=host;x-date, "
        "Signature=5e8480ceea12d0000a23c054151c50dd02c1a7dec835004057d19f13d53a7658"
    )
