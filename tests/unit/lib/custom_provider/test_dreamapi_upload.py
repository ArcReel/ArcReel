from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.custom_provider.dreamapi_upload import upload_asset
from tests.http_capture import capture_http, only_request, request_json


async def test_upload_asset_uses_dreamapi_policy_oss_and_finish_flow(tmp_path: Path):
    asset = tmp_path / "reference.png"
    asset.write_bytes(b"png-bytes")

    with capture_http() as router:
        policy = router.post("https://api.newportai.com/api/file/v1/get_policy").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "host": "https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com",
                        "key": "tmp/reference.png",
                        "OSSAccessKeyId": "upload-access",
                        "policy": "upload-policy",
                        "signature": "upload-signature",
                        "callback": "upload-callback",
                    },
                },
            )
        )
        upload = router.post("https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"reqId": "request-42"}})
        )
        finish = router.post("https://api.newportai.com/api/file/v1/policy_upload_finish").mock(
            return_value=httpx.Response(
                200, json={"code": 0, "data": {"url": "https://cdn.newportai.com/reference.png"}}
            )
        )

        async with httpx.AsyncClient() as client:
            url = await upload_asset(
                client,
                base_url="https://api.newportai.com",
                api_key="dreamapi-user-key",
                file_path=asset,
            )

    assert url == "https://cdn.newportai.com/reference.png"
    assert request_json(only_request(policy)) == {"scene": "Dream-CN"}
    assert only_request(policy).headers["Authorization"] == "Bearer dreamapi-user-key"
    uploaded = only_request(upload)
    assert "dreamapi-user-key" not in uploaded.headers.get("Authorization", "")
    assert b"upload-policy" in uploaded.content
    assert b"upload-access" in uploaded.content
    assert b"upload-signature" in uploaded.content
    assert b"tmp/reference.png" in uploaded.content
    assert b"png-bytes" in uploaded.content
    assert finish.calls.last.request.headers["Authorization"] == "Bearer dreamapi-user-key"
    assert request_json(only_request(finish)) == {"reqId": "request-42"}


@pytest.mark.parametrize(
    "host",
    [
        "https://127.0.0.1/steal",
        "https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com:8443",
        "https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com:not-a-port",
        "https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com/upload",
        "https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com/?redirect=evil",
        "https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com/#fragment",
    ],
)
async def test_upload_asset_rejects_an_untrusted_policy_host_without_uploading(tmp_path: Path, host: str):
    asset = tmp_path / "reference.png"
    asset.write_bytes(b"png-bytes")

    with capture_http() as router:
        policy = router.post("https://api.newportai.com/api/file/v1/get_policy").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "host": host,
                        "key": "tmp/reference.png",
                        "OSSAccessKeyId": "upload-access",
                        "policy": "upload-policy",
                        "signature": "upload-signature",
                        "callback": "upload-callback",
                    },
                },
            )
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="DreamAPI storage host"):
                await upload_asset(
                    client,
                    base_url="https://api.newportai.com",
                    api_key="dreamapi-user-key",
                    file_path=asset,
                )

    assert policy.call_count == 1


async def test_upload_asset_does_not_follow_oss_redirects_with_temporary_credentials(tmp_path: Path):
    asset = tmp_path / "reference.png"
    asset.write_bytes(b"png-bytes")

    with capture_http() as router:
        router.post("https://api.newportai.com/api/file/v1/get_policy").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "host": "https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com",
                        "key": "tmp/reference.png",
                        "OSSAccessKeyId": "upload-access",
                        "policy": "upload-policy",
                        "signature": "upload-signature",
                        "callback": "upload-callback",
                    },
                },
            )
        )
        router.post("https://dreamapi-oss.oss-cn-hongkong.aliyuncs.com").mock(
            return_value=httpx.Response(307, headers={"Location": "https://attacker.example/collect"})
        )
        attacker = router.post("https://attacker.example/collect").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"reqId": "stolen"}})
        )

        async with httpx.AsyncClient(follow_redirects=True) as client:
            with pytest.raises(RuntimeError, match="file upload request failed with HTTP 307"):
                await upload_asset(
                    client,
                    base_url="https://api.newportai.com",
                    api_key="dreamapi-user-key",
                    file_path=asset,
                )

    assert attacker.call_count == 0
