"""Upload-Post 客户端单元测试：respx 在 transport 层拦截，不打真实网络。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from lib.api_errors import BadGatewayError, ServiceUnavailableError, UnprocessableError
from lib.social_publish import UploadPostClient
from tests.http_capture import capture_http, only_request

_BASE_URL = "https://upload.example/api"


@pytest.fixture
async def upload_post_client() -> AsyncIterator[UploadPostClient]:
    async with httpx.AsyncClient() as http:
        yield UploadPostClient(api_key="secret-key", base_url=_BASE_URL, http_client=http)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "E1S01.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return path


async def test_list_profiles_maps_connected_accounts(upload_post_client: UploadPostClient) -> None:
    with capture_http() as router:
        route = router.get(f"{_BASE_URL}/uploadposts/users").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "profiles": [
                        {
                            "username": "studio",
                            "social_accounts": {
                                "tiktok": {"display_name": "Studio", "handle": "studio", "reauth_required": False},
                                "reddit": {"display_name": "r", "handle": "r", "reauth_required": True},
                                # 未连接的平台上游回空串而不是省略键
                                "youtube": "",
                            },
                        }
                    ],
                },
            )
        )

        profiles = await upload_post_client.list_profiles()

    assert only_request(route).headers["authorization"] == "Apikey secret-key"
    assert len(profiles) == 1
    assert profiles[0].username == "studio"
    assert [(a.platform, a.reauth_required) for a in profiles[0].accounts] == [("tiktok", False), ("reddit", True)]


async def test_publish_video_sends_multipart_with_repeated_platform_field(
    upload_post_client: UploadPostClient, video: Path
) -> None:
    with capture_http() as router:
        route = router.post(f"{_BASE_URL}/upload").mock(
            return_value=httpx.Response(
                200,
                json={"success": True, "request_id": "arcreel-1", "job_id": "job-9", "total_platforms": 2},
            )
        )

        submission = await upload_post_client.publish_video(
            profile="studio",
            platforms=("tiktok", "youtube"),
            video_path=video,
            title="第一章",
            request_id="arcreel-1",
            description="说明",
            external_id="demo:E1S01",
        )

    body = only_request(route).content.decode("utf-8", errors="replace")
    assert 'name="platform[]"\r\n\r\ntiktok' in body
    assert 'name="platform[]"\r\n\r\nyoutube' in body
    assert 'name="async_upload"\r\n\r\ntrue' in body
    assert 'name="is_ai_generated"\r\n\r\ntrue' in body
    assert 'name="external_id"\r\n\r\ndemo:E1S01' in body
    assert submission.request_id == "arcreel-1"
    assert submission.job_id == "job-9"
    assert submission.total_platforms == 2


async def test_publish_video_sends_idempotency_key_equal_to_request_id(
    upload_post_client: UploadPostClient, video: Path
) -> None:
    with capture_http() as router:
        route = router.post(f"{_BASE_URL}/upload").mock(
            return_value=httpx.Response(200, json={"success": True, "request_id": "arcreel-2"})
        )

        await upload_post_client.publish_video(
            profile="studio",
            platforms=("tiktok",),
            video_path=video,
            title="第一章",
            request_id="arcreel-2",
        )

    assert only_request(route).headers["idempotency-key"] == "arcreel-2"


async def test_publish_video_keeps_local_request_id_when_upstream_omits_it(
    upload_post_client: UploadPostClient, video: Path
) -> None:
    with capture_http() as router:
        router.post(f"{_BASE_URL}/upload").mock(return_value=httpx.Response(200, json={"success": True}))

        submission = await upload_post_client.publish_video(
            profile="studio",
            platforms=("tiktok",),
            video_path=video,
            title="第一章",
            request_id="arcreel-3",
        )

    assert submission.request_id == "arcreel-3"
    assert submission.total_platforms == 1


async def test_publish_video_ignores_a_request_id_the_upstream_rewrote(
    upload_post_client: UploadPostClient, video: Path
) -> None:
    """幂等键用的是本端 id；跟着上游改口会让后续轮询问的是另一次投递。"""
    with capture_http() as router:
        router.post(f"{_BASE_URL}/upload").mock(
            return_value=httpx.Response(200, json={"success": True, "request_id": "upstream-other"})
        )

        submission = await upload_post_client.publish_video(
            profile="studio",
            platforms=("tiktok",),
            video_path=video,
            title="第一章",
            request_id="arcreel-6",
        )

    assert submission.request_id == "arcreel-6"


async def test_fetch_progress_maps_platform_outcomes(upload_post_client: UploadPostClient) -> None:
    with capture_http() as router:
        route = router.get(f"{_BASE_URL}/uploadposts/status").mock(
            return_value=httpx.Response(
                200,
                json={
                    "request_id": "arcreel-1",
                    "status": "in_progress",
                    "completed": 1,
                    "total": 3,
                    "results": [
                        {"platform": "x", "status": "completed", "success": True, "url": "https://x.com/p/1"},
                        {"platform": "tiktok", "status": "failed", "success": False, "error": "token expired"},
                        {"platform": "youtube", "status": "skipped", "success": False, "skipped": True},
                    ],
                },
            )
        )

        progress = await upload_post_client.fetch_progress(request_id="arcreel-1")

    assert only_request(route).url.params["request_id"] == "arcreel-1"
    assert progress.status == "in_progress"
    assert progress.terminal is False
    assert [(o.platform, o.status, o.url, o.error) for o in progress.outcomes] == [
        ("x", "completed", "https://x.com/p/1", None),
        ("tiktok", "failed", None, "token expired"),
        ("youtube", "skipped", None, None),
    ]


async def test_fetch_progress_ignores_non_url_post_url_placeholder(upload_post_client: UploadPostClient) -> None:
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/status").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "completed",
                    "completed": 1,
                    "total": 1,
                    # TikTok 收件箱投递把说明文字写进 post_url，不是链接
                    "results": [
                        {
                            "platform": "tiktok",
                            "status": "completed",
                            "success": True,
                            "post_url": "Video sent to Inbox (No Public URL)",
                        }
                    ],
                },
            )
        )

        progress = await upload_post_client.fetch_progress(request_id="arcreel-1")

    assert progress.terminal is True
    assert progress.outcomes[0].url is None


@pytest.mark.parametrize("payload", [{"completed": 0, "total": 1}, {"status": "half_done"}])
async def test_fetch_progress_rejects_an_unknown_aggregate_status(
    upload_post_client: UploadPostClient, payload: dict[str, object]
) -> None:
    """未知或缺失的聚合状态曾按 pending 收下，调用方会拿着一个永不转终态的值一直轮询。"""
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/status").mock(return_value=httpx.Response(200, json=payload))

        with pytest.raises(BadGatewayError) as excinfo:
            await upload_post_client.fetch_progress(request_id="arcreel-1")

    assert excinfo.value.key == "social_publish_upstream_malformed"


@pytest.mark.parametrize(
    ("status_code", "expected_key"),
    [
        (401, "social_publish_credentials_rejected"),
        (403, "social_publish_credentials_rejected"),
        (400, "social_publish_rejected"),
        (404, "social_publish_rejected"),
    ],
)
async def test_client_errors_map_to_unprocessable_domain_errors(
    upload_post_client: UploadPostClient, status_code: int, expected_key: str
) -> None:
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/users").mock(
            return_value=httpx.Response(status_code, json={"success": False, "message": "Invalid or expired token"})
        )

        with pytest.raises(UnprocessableError) as excinfo:
            await upload_post_client.list_profiles()

    assert excinfo.value.key == expected_key
    assert excinfo.value.status_code == 422


async def test_rejection_carries_upstream_reason_as_diagnostic(
    upload_post_client: UploadPostClient, video: Path
) -> None:
    with capture_http() as router:
        router.post(f"{_BASE_URL}/upload").mock(
            return_value=httpx.Response(400, json={"success": False, "message": "Username required in form data"})
        )

        with pytest.raises(UnprocessableError) as excinfo:
            await upload_post_client.publish_video(
                profile="studio",
                platforms=("tiktok",),
                video_path=video,
                title="第一章",
                request_id="arcreel-4",
            )

    assert excinfo.value.diagnostic == "Username required in form data"


async def test_auth_rejection_does_not_leak_upstream_body(upload_post_client: UploadPostClient) -> None:
    """401/403 不带 diagnostic：认证失败的上游原文里可能回显请求头与凭证片段。"""
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/users").mock(
            return_value=httpx.Response(401, json={"success": False, "message": "Invalid Apikey secret-key"})
        )

        with pytest.raises(UnprocessableError) as excinfo:
            await upload_post_client.list_profiles()

    assert excinfo.value.diagnostic is None


async def test_quota_exceeded_maps_to_service_unavailable(upload_post_client: UploadPostClient) -> None:
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/users").mock(
            return_value=httpx.Response(429, json={"success": False, "message": "monthly limit"})
        )

        with pytest.raises(ServiceUnavailableError) as excinfo:
            await upload_post_client.list_profiles()

    assert excinfo.value.key == "social_publish_quota_exceeded"
    assert excinfo.value.diagnostic == "monthly limit"


async def test_upstream_5xx_maps_to_bad_gateway(upload_post_client: UploadPostClient) -> None:
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/users").mock(return_value=httpx.Response(503, text="upstream down"))

        with pytest.raises(BadGatewayError) as excinfo:
            await upload_post_client.list_profiles()

    assert excinfo.value.key == "social_publish_upstream_failed"
    assert excinfo.value.status_code == 502


async def test_network_failure_maps_to_bad_gateway(upload_post_client: UploadPostClient) -> None:
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/users").mock(side_effect=httpx.ConnectError("no route"))

        with pytest.raises(BadGatewayError) as excinfo:
            await upload_post_client.list_profiles()

    assert excinfo.value.key == "social_publish_upstream_unreachable"


async def test_non_json_success_body_is_rejected_as_malformed(upload_post_client: UploadPostClient) -> None:
    with capture_http() as router:
        router.get(f"{_BASE_URL}/uploadposts/users").mock(return_value=httpx.Response(200, text="<html>ok</html>"))

        with pytest.raises(BadGatewayError) as excinfo:
            await upload_post_client.list_profiles()

    assert excinfo.value.key == "social_publish_upstream_malformed"


async def test_base_url_trailing_slash_does_not_double_up(video: Path) -> None:
    async with httpx.AsyncClient() as http:
        trailing_slash_client = UploadPostClient(api_key="k", base_url=f"{_BASE_URL}/", http_client=http)
        with capture_http() as router:
            route = router.post(f"{_BASE_URL}/upload").mock(
                return_value=httpx.Response(200, json={"success": True, "request_id": "arcreel-5"})
            )

            await trailing_slash_client.publish_video(
                profile="studio",
                platforms=("tiktok",),
                video_path=video,
                title="第一章",
                request_id="arcreel-5",
            )

    assert str(only_request(route).url) == f"{_BASE_URL}/upload"
