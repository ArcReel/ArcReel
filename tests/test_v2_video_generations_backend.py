"""V2VideoGenerationsBackend 纯函数单测（请求体映射 / 状态归一 / 多路径提取）。

只测外部可观察行为与纯函数，不跑真实 HTTP。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.video_backends.base import (
    AmbiguousSubmitError,
    ResumeExpiredError,
    VideoCapability,
    VideoGenerationRequest,
)
from lib.video_backends.v2_video_generations import (
    _TASK_ID_PATHS,
    _VIDEO_URL_PATHS,
    PROVIDER_V2_VIDEO,
    _dig,
    _extract_failure,
    _first_str_by_paths,
    _log_fields,
    _normalize_root,
    build_request_body,
    normalize_status,
)
from lib.video_backends.v2_video_generations import (
    V2VideoGenerationsBackend as _V2Backend,
)


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    # 真字符串而非 MagicMock：submit_post 在 >=400 时记 resp.text[:500]，让该日志切片走真实 str 路径。
    resp.text = str(json_body)
    resp.raise_for_status = MagicMock()
    return resp


def _make_http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x/v2/video/generations")
    response = httpx.Response(status_code, request=request, text=message)
    return httpx.HTTPStatusError(f"error '{status_code}'", request=request, response=response)


def _fake_download_factory(payload: bytes = b"mp4-bytes"):
    async def _fake(url: str, output_path: Path, *, timeout: int = 120) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)

    return _fake


def _mock_client(*, post=None, get=None) -> AsyncMock:
    client = AsyncMock()
    if post is not None:
        client.post = AsyncMock(return_value=post) if not isinstance(post, list) else AsyncMock(side_effect=post)
    if get is not None:
        client.get = AsyncMock(return_value=get) if not isinstance(get, list) else AsyncMock(side_effect=get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _req(tmp_path: Path, **kwargs) -> VideoGenerationRequest:
    base = {"prompt": "a cat", "output_path": tmp_path / "out.mp4"}
    base.update(kwargs)
    return VideoGenerationRequest(**base)


def _write_img(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n fake bytes")
    return p


class TestNormalizeStatus:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # aimlapi 官方枚举
            ("queued", "queued"),
            ("generating", "running"),
            ("completed", "succeeded"),
            ("error", "failed"),
            # 跨厂商同义词（流派 C 路由到多家时底层串可能透传）
            ("succeed", "succeeded"),  # Kling
            ("Success", "succeeded"),  # MiniMax 首字母大写
            ("Fail", "failed"),
            ("expired", "failed"),
            ("canceled", "failed"),
            ("in_progress", "running"),  # Sora
            ("Processing", "running"),  # Kling
            ("PENDING", "queued"),  # DashScope 全大写
            ("Queueing", "queued"),  # MiniMax
            ("submitted", "queued"),  # Kling
            ("  COMPLETED  ", "succeeded"),  # 大小写 + 空白
            # 未知 / 非字符串 → 当 running 继续轮询
            ("weird-status", "running"),
            (None, "running"),
            (99, "running"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_status(raw) == expected


class TestDig:
    def test_walks_dict_and_list_index(self):
        payload = {"data": {"task_result": {"videos": [{"url": "u0"}, {"url": "u1"}]}}}
        assert _dig(payload, ("data", "task_result", "videos", 0, "url")) == "u0"

    def test_missing_key_returns_none(self):
        assert _dig({"a": 1}, ("a", "b")) is None

    def test_list_index_out_of_range_returns_none(self):
        assert _dig({"v": []}, ("v", 0)) is None

    def test_type_mismatch_returns_none(self):
        assert _dig({"v": "str"}, ("v", 0)) is None  # 期望 list 实为 str


class TestVideoUrlExtraction:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"id": "g", "status": "completed", "video": {"url": "https://cdn/v.mp4"}}, "https://cdn/v.mp4"),
            ({"assets": {"video": "https://a/v.mp4"}}, "https://a/v.mp4"),
            ({"output": {"video_url": "https://w/v.mp4"}}, "https://w/v.mp4"),
            ({"content": {"video_url": "https://s/v.mp4"}}, "https://s/v.mp4"),
            ({"data": {"task_result": {"videos": [{"url": "https://k/v.mp4"}]}}}, "https://k/v.mp4"),
            ({"url": "https://n/v.mp4"}, "https://n/v.mp4"),
        ],
    )
    def test_extracts_first_match(self, payload, expected):
        assert _first_str_by_paths(payload, _VIDEO_URL_PATHS) == expected

    def test_priority_video_url_wins_over_bare_url(self):
        payload = {"video": {"url": "https://primary/v.mp4"}, "url": "https://fallback/v.mp4"}
        assert _first_str_by_paths(payload, _VIDEO_URL_PATHS) == "https://primary/v.mp4"

    def test_all_miss_returns_none(self):
        assert _first_str_by_paths({"foo": "bar"}, _VIDEO_URL_PATHS) is None

    def test_empty_string_skipped(self):
        payload = {"video": {"url": "   "}, "url": "https://fallback/v.mp4"}
        assert _first_str_by_paths(payload, _VIDEO_URL_PATHS) == "https://fallback/v.mp4"


class TestTaskIdExtraction:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"generation_id": "vg_xxx"}, "vg_xxx"),  # 流派 C 文档约定字段（CometAPI 等）
            ({"id": "gen_1"}, "gen_1"),
            ({"task_id": "t1"}, "t1"),
            ({"data": {"task_id": "d1"}}, "d1"),
            ({"request_id": "r1"}, "r1"),
            ({"data": {"taskId": "dt1"}}, "dt1"),
            ({"id": 123}, "123"),  # int 容忍并 str 化
        ],
    )
    def test_extracts(self, payload, expected):
        assert _first_str_by_paths(payload, _TASK_ID_PATHS) == expected

    def test_priority_id_wins(self):
        assert _first_str_by_paths({"id": "primary", "task_id": "secondary"}, _TASK_ID_PATHS) == "primary"

    def test_priority_generation_id_wins(self):
        # generation_id 是端点文档约定字段，优先级压过 id（表首）
        assert _first_str_by_paths({"generation_id": "gen", "id": "fallback"}, _TASK_ID_PATHS) == "gen"


class TestBuildRequestBody:
    def test_text_to_video_minimal(self, tmp_path):
        # aspect_ratio 恒透传（默认 9:16），表达项目朝向
        body = build_request_body("kling-v2", _req(tmp_path, duration_seconds=8))
        assert body == {"model": "kling-v2", "prompt": "a cat", "duration": 8, "aspect_ratio": "9:16"}

    def test_aspect_ratio_passed_through(self, tmp_path):
        body = build_request_body("m", _req(tmp_path, aspect_ratio="16:9"))
        assert body["aspect_ratio"] == "16:9"

    def test_includes_seed_and_resolution(self, tmp_path):
        body = build_request_body("m", _req(tmp_path, seed=42, resolution="720p"))
        assert body["seed"] == 42
        assert body["resolution"] == "720p"

    def test_start_image_to_image_url(self, tmp_path):
        img = _write_img(tmp_path, "start.png")
        body = build_request_body("m", _req(tmp_path, start_image=img))
        assert body["image_url"].startswith("data:image/png;base64,")

    def test_end_image_to_last_image_url(self, tmp_path):
        start = _write_img(tmp_path, "start.png")
        end = _write_img(tmp_path, "end.png")
        body = build_request_body("m", _req(tmp_path, start_image=start, end_image=end))
        assert body["last_image_url"].startswith("data:image/png;base64,")

    def test_reference_images_to_image_urls(self, tmp_path):
        refs = [_write_img(tmp_path, "r1.png"), _write_img(tmp_path, "r2.png")]
        body = build_request_body("m", _req(tmp_path, reference_images=refs))
        assert isinstance(body["image_urls"], list)
        assert len(body["image_urls"]) == 2
        assert all(u.startswith("data:image/png;base64,") for u in body["image_urls"])

    def test_missing_image_file_omitted(self, tmp_path):
        body = build_request_body("m", _req(tmp_path, start_image=tmp_path / "nope.png"))
        assert "image_url" not in body


class TestExtractFailure:
    def test_succeeded_returns_none(self):
        assert _extract_failure({"status": "completed", "video": {"url": "u"}}) is None

    def test_running_returns_none(self):
        assert _extract_failure({"status": "generating"}) is None

    def test_error_dict_message(self):
        msg = _extract_failure({"status": "error", "error": {"message": "boom", "name": "E"}})
        assert msg is not None and "boom" in msg

    def test_error_string(self):
        msg = _extract_failure({"status": "failed", "error": "explicit reason"})
        assert msg is not None and "explicit reason" in msg

    def test_error_without_detail(self):
        msg = _extract_failure({"status": "error"})
        assert msg is not None and "unknown" in msg


class TestNormalizeRoot:
    @pytest.mark.parametrize(
        "base_url,expected",
        [
            ("https://api.aimlapi.com", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/v1", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/v2", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/v1beta", "https://api.aimlapi.com"),
            # 带小版本号的版本段（/v1.1、/v1.0）也归一化
            ("https://api.aimlapi.com/v1.1", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/v1.0", "https://api.aimlapi.com"),
            # 无 scheme 的纯域名补 https://（否则 httpx 拒收相对 URL）
            ("api.aimlapi.com", "https://api.aimlapi.com"),
            ("api.aimlapi.com/v1", "https://api.aimlapi.com"),
        ],
    )
    def test_strips_version_suffix(self, base_url, expected):
        assert _normalize_root(base_url) == expected


class TestLogFields:
    def test_summary_built_from_request_never_base64(self, tmp_path):
        start = _write_img(tmp_path, "s.png")
        refs = [_write_img(tmp_path, "r1.png"), _write_img(tmp_path, "r2.png")]
        fields = _log_fields(
            "seedance-1.0",
            _req(tmp_path, start_image=start, reference_images=refs, resolution="720p", seed=7, aspect_ratio="16:9"),
        )
        assert fields["model"] == "seedance-1.0"
        assert fields["prompt"] == "a cat"
        assert fields["resolution"] == "720p"
        assert fields["aspect_ratio"] == "16:9"
        assert fields["seed"] == 7
        # 图片只记有无/数量，绝不出现 base64 data URI
        assert fields["start_image"] is True
        assert fields["end_image"] is False
        assert fields["reference_images"] == 2
        assert not any("base64" in str(v) for v in fields.values())

    def test_no_images(self, tmp_path):
        fields = _log_fields("m", _req(tmp_path))
        assert fields["start_image"] is False
        assert fields["end_image"] is False
        assert fields["reference_images"] == 0

    def test_long_prompt_truncated(self, tmp_path):
        long_prompt = "x" * 1000
        fields = _log_fields("m", _req(tmp_path, prompt=long_prompt))
        assert len(fields["prompt"]) < len(long_prompt)
        assert "1000 chars" in fields["prompt"]


class TestBuildRequestBodyBranches:
    def test_large_image_warns(self, tmp_path, caplog):
        import logging

        img = _write_img(tmp_path, "big.png")
        with patch("lib.video_backends.v2_video_generations._LARGE_IMAGE_WARN_BYTES", 0):
            with caplog.at_level(logging.WARNING, logger="lib.video_backends.v2_video_generations"):
                body = build_request_body("m", _req(tmp_path, start_image=img))
        assert body["image_url"].startswith("data:image/png;base64,")
        assert any("图片较大" in r.message for r in caplog.records)

    def test_missing_reference_image_skipped(self, tmp_path):
        present = _write_img(tmp_path, "r1.png")
        body = build_request_body("m", _req(tmp_path, reference_images=[present, tmp_path / "missing.png"]))
        assert len(body["image_urls"]) == 1

    def test_missing_end_image_omitted(self, tmp_path):
        start = _write_img(tmp_path, "start.png")
        body = build_request_body("m", _req(tmp_path, start_image=start, end_image=tmp_path / "no_end.png"))
        assert "image_url" in body
        assert "last_image_url" not in body

    def test_all_reference_images_missing_omits_key(self, tmp_path):
        body = build_request_body("m", _req(tmp_path, reference_images=[tmp_path / "nope.png"]))
        assert "image_urls" not in body


class TestV2BackendHttp:
    """V2 backend HTTP 流程（submit → poll → 提取 → 下载 / resume），全程 mock httpx，不跑真实网络。"""

    @staticmethod
    def _backend() -> _V2Backend:
        return _V2Backend(api_key="sk-test", base_url="https://api.aimlapi.com", model="seedance-1.0")

    def test_name_model_capabilities(self):
        b = self._backend()
        assert b.name == PROVIDER_V2_VIDEO
        assert b.model == "seedance-1.0"
        assert VideoCapability.TEXT_TO_VIDEO in b.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in b.capabilities
        caps = b.video_capabilities
        assert caps.first_frame and caps.last_frame and caps.reference_images
        assert caps.max_reference_images == 4

    def test_constructor_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            _V2Backend(api_key="", base_url="https://x", model="m")

    def test_constructor_requires_base_url(self):
        with pytest.raises(ValueError, match="base_url"):
            _V2Backend(api_key="k", base_url="", model="m")

    @pytest.mark.asyncio
    async def test_generate_happy_path(self, tmp_path: Path):
        client = _mock_client(
            post=_make_response(200, {"id": "gen-1", "status": "queued"}),
            get=[
                _make_response(200, {"id": "gen-1", "status": "generating"}),
                _make_response(200, {"id": "gen-1", "status": "completed", "video": {"url": "https://cdn/v.mp4"}}),
            ],
        )
        fake_dl = AsyncMock(side_effect=_fake_download_factory(b"mp4"))
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.v2_video_generations.download_video", fake_dl),
        ):
            req = VideoGenerationRequest(prompt="a cat", output_path=tmp_path / "o.mp4", duration_seconds=5)
            result = await self._backend().generate(req)
        assert result.video_path.read_bytes() == b"mp4"
        assert result.provider == PROVIDER_V2_VIDEO
        assert result.model == "seedance-1.0"
        assert result.task_id == "gen-1"
        assert result.video_uri == "https://cdn/v.mp4"
        post_call = client.post.call_args
        assert post_call.args[0] == "https://api.aimlapi.com/v2/video/generations"
        assert post_call.kwargs["json"]["model"] == "seedance-1.0"
        assert post_call.kwargs["headers"]["Authorization"] == "Bearer sk-test"
        assert client.get.call_args.kwargs["params"] == {"generation_id": "gen-1"}
        fake_dl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_persists_job_id_when_task_id_set(self, tmp_path: Path):
        client = _mock_client(
            post=_make_response(200, {"id": "gen-9"}),
            get=_make_response(200, {"status": "completed", "video": {"url": "https://cdn/v.mp4"}}),
        )
        persist = AsyncMock()
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch(
                "lib.video_backends.v2_video_generations.download_video",
                AsyncMock(side_effect=_fake_download_factory()),
            ),
            patch("lib.video_backends.v2_video_generations.persist_provider_job_id", persist),
        ):
            req = VideoGenerationRequest(
                prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5, task_id="task-77"
            )
            await self._backend().generate(req)
        persist.assert_awaited_once()
        call = persist.await_args
        assert call is not None
        assert call.args[0] == "task-77"
        assert call.args[1] == "gen-9"

    @pytest.mark.asyncio
    async def test_generate_missing_task_id_raises(self, tmp_path: Path):
        client = _mock_client(post=_make_response(200, {"status": "queued"}))
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations.download_video", AsyncMock()),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            with pytest.raises(RuntimeError, match="task_id"):
                await self._backend().generate(req)

    @pytest.mark.asyncio
    async def test_generate_missing_video_url_raises(self, tmp_path: Path):
        client = _mock_client(
            post=_make_response(200, {"id": "g"}),
            get=_make_response(200, {"status": "completed"}),
        )
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.v2_video_generations.download_video", AsyncMock()),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            with pytest.raises(RuntimeError, match="视频 URL"):
                await self._backend().generate(req)

    @pytest.mark.asyncio
    async def test_generate_failed_status_raises(self, tmp_path: Path):
        client = _mock_client(
            post=_make_response(200, {"id": "g"}),
            get=_make_response(200, {"status": "error", "error": {"message": "boom"}}),
        )
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.v2_video_generations.download_video", AsyncMock()),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            with pytest.raises(RuntimeError, match="boom"):
                await self._backend().generate(req)

    @pytest.mark.asyncio
    async def test_resume_video_poll_and_download(self, tmp_path: Path):
        client = _mock_client(get=_make_response(200, {"status": "completed", "video": {"url": "https://cdn/r.mp4"}}))
        fake_dl = AsyncMock(side_effect=_fake_download_factory(b"r"))
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.v2_video_generations.download_video", fake_dl),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            result = await self._backend().resume_video("gen-resume", req)
        assert result.task_id == "gen-resume"
        assert result.video_path.read_bytes() == b"r"
        # resume 只 poll，不再 submit
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_404_raises_resume_expired(self, tmp_path: Path):
        resp404 = _make_response(404, {})
        resp404.raise_for_status = MagicMock(side_effect=_make_http_error(404, "not found"))
        client = _mock_client(get=resp404)
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            with pytest.raises(ResumeExpiredError):
                await self._backend().resume_video("gen-expired", req)

    @pytest.mark.asyncio
    async def test_create_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """创建任务遇确定性 4xx（400）应一次失败，不重试。"""
        resp400 = _make_response(400, {"error": "bad request"})
        resp400.raise_for_status = MagicMock(side_effect=_make_http_error(400, "bad request"))
        client = _mock_client(post=resp400)
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            with pytest.raises(httpx.HTTPStatusError):
                await self._backend().generate(req)
        assert client.post.call_count == 1, "确定性 4xx 不该被 retry"

    @pytest.mark.asyncio
    async def test_poll_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """轮询遇确定性 4xx（401，如 token 轮换失效）应一次失败，不重试到 max_wait 超时。"""
        resp401 = _make_response(401, {"error": "unauthorized"})
        resp401.raise_for_status = MagicMock(side_effect=_make_http_error(401, "unauthorized"))
        client = _mock_client(post=_make_response(200, {"id": "gen-401", "status": "queued"}), get=resp401)
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            with pytest.raises(httpx.HTTPStatusError):
                await self._backend().generate(req)
        assert client.get.call_count == 1, "轮询确定性 4xx 应一击失败，不重试到超时"

    @pytest.mark.asyncio
    async def test_create_read_timeout_fails_fast_with_manual_retry_hint(self, tmp_path: Path):
        """create 阶段 ReadTimeout（请求可能已送达）→ 不重试、单次失败、错误信息含手动重试提示。"""
        # list 形式 → side_effect，AsyncMock 会抛出该异常（单值形式会被当 return_value 返回）。
        client = _mock_client(post=[httpx.ReadTimeout("read timed out")])
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            with pytest.raises(AmbiguousSubmitError, match="手动重试"):
                await self._backend().generate(req)
        assert client.post.call_count == 1, "歧义态不该被 retry"
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_connect_error_retries(self, tmp_path: Path):
        """create 阶段 ConnectError（请求确定未送达）→ 重试，第三次成功。"""
        client = _mock_client(
            post=[
                httpx.ConnectError("refused"),
                httpx.ConnectError("refused"),
                _make_response(200, {"id": "gen-ok", "status": "queued"}),
            ],
            get=_make_response(200, {"status": "completed", "video": {"url": "https://cdn/v.mp4"}}),
        )
        fake_dl = AsyncMock(side_effect=_fake_download_factory(b"mp4"))
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.v2_video_generations.download_video", fake_dl),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            result = await self._backend().generate(req)
        assert result.task_id == "gen-ok"
        assert client.post.call_count == 3, "ConnectError 请求确定未送达，应重试"

    @pytest.mark.asyncio
    async def test_create_retries_on_5xx(self, tmp_path: Path):
        """create 阶段收到 503 响应（服务端明示创建失败）→ 维持重试（现状保持）。"""
        resp503 = _make_response(503, {"error": "upstream busy"})
        resp503.raise_for_status = MagicMock(side_effect=_make_http_error(503, "upstream busy"))
        client = _mock_client(
            post=[resp503, resp503, _make_response(200, {"id": "gen-503", "status": "queued"})],
            get=_make_response(200, {"status": "completed", "video": {"url": "https://cdn/v.mp4"}}),
        )
        fake_dl = AsyncMock(side_effect=_fake_download_factory(b"mp4"))
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.v2_video_generations.download_video", fake_dl),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            result = await self._backend().generate(req)
        assert result.task_id == "gen-503"
        assert client.post.call_count == 3, "5xx 应维持重试"

    @pytest.mark.asyncio
    async def test_poll_read_timeout_retries(self, tmp_path: Path):
        """poll 阶段 ReadTimeout（幂等 GET）→ 重试，不回归。"""
        client = _mock_client(
            post=_make_response(200, {"id": "gen-p", "status": "queued"}),
            get=[
                httpx.ReadTimeout("read timed out"),
                _make_response(200, {"status": "completed", "video": {"url": "https://cdn/v.mp4"}}),
            ],
        )
        fake_dl = AsyncMock(side_effect=_fake_download_factory(b"mp4"))
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.v2_video_generations._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.v2_video_generations.download_video", fake_dl),
        ):
            req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            result = await self._backend().generate(req)
        assert result.task_id == "gen-p"
        assert client.get.call_count == 2, "poll 网络超时应重试"
