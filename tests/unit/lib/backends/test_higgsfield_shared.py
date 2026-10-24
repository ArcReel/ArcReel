"""lib.backends.higgsfield_shared 单元测试 — 凭证解析、素材上传两步、状态判定与连接测试白名单。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.backends import higgsfield_shared
from lib.backends.higgsfield_shared import (
    create_higgsfield_client,
    extract_image_urls,
    extract_video_url,
    is_request_done,
    request_failure_reason,
    safe_body_for_log,
    upload_image,
)
from tests.http_capture import capture_http, only_request, request_json


class TestCredentials:
    def test_both_keys_required(self):
        with pytest.raises(ValueError, match="Key ID"):
            higgsfield_shared.resolve_higgsfield_credentials("id", None)
        with pytest.raises(ValueError, match="Key ID"):
            higgsfield_shared.resolve_higgsfield_credentials(" ", "secret")

    def test_client_sends_key_header(self):
        client = create_higgsfield_client(access_key=" id ", secret_key="secret")
        assert client.headers["Authorization"] == "Key id:secret"
        assert str(client.base_url).rstrip("/") == "https://api.higgsfield.ai"

    def test_connection_test_resolves_credentials_before_http(self):
        with capture_http() as router:
            with pytest.raises(ValueError, match="Key ID"):
                higgsfield_shared.test_higgsfield_connection({"access_key": "id"})
            assert router.calls.call_count == 0


class TestConnectionTest:
    @pytest.mark.parametrize(("status", "ok"), [(404, True), (401, False), (403, False), (500, False)])
    def test_status_whitelist(self, status: int, ok: bool):
        with capture_http() as router:
            route = router.get(url__regex=r".*/requests/[0-9a-f-]+/status").mock(
                return_value=httpx.Response(status, json={"detail": "x"})
            )
            config = {"access_key": "id", "secret_key": "secret"}
            if ok:
                higgsfield_shared.test_higgsfield_connection(config)
            else:
                with pytest.raises(RuntimeError):
                    higgsfield_shared.test_higgsfield_connection(config)
            assert only_request(route).headers["Authorization"] == "Key id:secret"


class TestConnectionTestDestination:
    def test_metadata_base_url_is_refused_before_request(self):
        from lib.backends.artifact_download_guard import ArtifactDestinationRejectedError

        with capture_http() as router:
            with pytest.raises(ArtifactDestinationRejectedError):
                higgsfield_shared.test_higgsfield_connection(
                    {"access_key": "id", "secret_key": "secret", "base_url": "http://169.254.169.254"}
                )
            assert router.calls.call_count == 0


class TestUpload:
    async def test_two_step_upload_returns_public_url(self, tmp_path: Path):
        image = tmp_path / "ref.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        with capture_http() as router:
            ticket = router.post("https://api.higgsfield.ai/files/generate-upload-url").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "public_url": "https://cdn.example.com/a.png",
                        "upload_url": "https://storage.example.com/presigned?sig=1",
                        "content_type": "image/png",
                        "upload_headers": {"Content-Type": "image/png", "x-amz-tagging": "t=1"},
                    },
                )
            )
            put = router.put("https://storage.example.com/presigned").mock(return_value=httpx.Response(200))
            async with create_higgsfield_client(access_key="id", secret_key="secret") as client:
                url = await upload_image(client, image)

        assert url == "https://cdn.example.com/a.png"
        assert request_json(only_request(ticket)) == {"content_type": "image/png"}
        sent = only_request(put)
        assert sent.headers["x-amz-tagging"] == "t=1"
        assert sent.headers["Content-Type"] == "image/png"
        # 预签名存储地址不得携带 API 凭证
        assert "Authorization" not in sent.headers
        assert sent.content == image.read_bytes()

    async def test_missing_ticket_fields_fail_loud(self, tmp_path: Path):
        image = tmp_path / "ref.jpg"
        image.write_bytes(b"\xff\xd8\xff" + b"0" * 8)
        with capture_http() as router:
            router.post("https://api.higgsfield.ai/files/generate-upload-url").mock(
                return_value=httpx.Response(200, json={"upload_url": "https://storage.example.com/x"})
            )
            async with create_higgsfield_client(access_key="id", secret_key="secret") as client:
                with pytest.raises(RuntimeError, match="public_url"):
                    await upload_image(client, image)


class TestStatusHelpers:
    def test_done_and_failed(self):
        assert is_request_done({"status": "completed"})
        assert not is_request_done({"status": "in_progress"})
        assert request_failure_reason({"status": "queued"}) is None
        assert "nsfw" in (request_failure_reason({"status": "nsfw"}) or "")
        assert "boom" in (request_failure_reason({"status": "failed", "error": "boom"}) or "")

    def test_extractors(self):
        assert extract_image_urls({"images": [{"url": "u1"}, {"nope": 1}, {"url": "u2"}]}) == ["u1", "u2"]
        assert extract_image_urls({}) == []
        assert extract_video_url({"video": {"url": "v"}}) == "v"
        with pytest.raises(RuntimeError, match=r"video\.url"):
            extract_video_url({"request_id": "r", "status": "completed"})

    def test_safe_body_hides_urls_and_truncates_prompt(self):
        view = safe_body_for_log(
            {
                "prompt": "p" * 200,
                "image_urls": ["https://x/1", "https://x/2"],
                "image_url": "https://x/first",
                "duration": 5,
                "sound": "on",
            }
        )
        assert view["image_urls"] == "<2 url>"
        assert view["image_url"] == "<url>"
        assert view["prompt"].endswith("…")
        assert len(view["prompt"]) == 121
        assert view["duration"] == 5
        assert view["sound"] == "on"
