"""V2VideoGenerationsBackend 单测（纯函数 + respx 捕获的 HTTP 流程）。

请求体映射 / 状态归一 / 多路径提取走纯函数；submit → poll → 下载由 respx 在 transport 层拦截。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
import respx

from lib.video_backends.base import (
    AmbiguousSubmitError,
    ResumeExpiredError,
    VideoGenerationRequest,
    first_str_by_paths,
)
from lib.video_backends.v2_video_generations import (
    _LARGE_IMAGE_WARN_BYTES,
    _TASK_ID_PATHS,
    _VIDEO_URL_PATHS,
    PROVIDER_V2_VIDEO,
    _extract_failure,
    _log_fields,
    _normalize_root,
    build_request_body,
    normalize_status,
)
from lib.video_backends.v2_video_generations import (
    V2VideoGenerationsBackend as _V2Backend,
)
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

_ROOT = "https://api.aimlapi.com"
_GENERATIONS_URL = f"{_ROOT}/v2/video/generations"


class _V2Routes(NamedTuple):
    """V2 端点的三条出站流量：建任务、任务轮询（同 URL、GET）、成片下载。"""

    submit: respx.Route
    poll: respx.Route
    download: respx.Route


@contextmanager
def _v2_api() -> Iterator[_V2Routes]:
    with capture_http() as router:
        yield _V2Routes(
            submit=router.post(_GENERATIONS_URL),
            poll=router.get(_GENERATIONS_URL),
            download=router.get(url__regex=r"^https://cdn"),
        )


def _json(body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=body)


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
        assert first_str_by_paths(payload, _VIDEO_URL_PATHS) == expected

    def test_priority_video_url_wins_over_bare_url(self):
        payload = {"video": {"url": "https://primary/v.mp4"}, "url": "https://fallback/v.mp4"}
        assert first_str_by_paths(payload, _VIDEO_URL_PATHS) == "https://primary/v.mp4"

    def test_all_miss_returns_none(self):
        assert first_str_by_paths({"foo": "bar"}, _VIDEO_URL_PATHS) is None

    def test_empty_string_skipped(self):
        payload = {"video": {"url": "   "}, "url": "https://fallback/v.mp4"}
        assert first_str_by_paths(payload, _VIDEO_URL_PATHS) == "https://fallback/v.mp4"


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
        assert first_str_by_paths(payload, _TASK_ID_PATHS) == expected

    def test_priority_id_wins(self):
        assert first_str_by_paths({"id": "primary", "task_id": "secondary"}, _TASK_ID_PATHS) == "primary"

    def test_priority_generation_id_wins(self):
        # generation_id 是端点文档约定字段，优先级压过 id（表首）
        assert first_str_by_paths({"generation_id": "gen", "id": "fallback"}, _TASK_ID_PATHS) == "gen"


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

        img = tmp_path / "big.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * _LARGE_IMAGE_WARN_BYTES)

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
    """V2 backend HTTP 流程（submit → poll → 提取 → 下载 / resume）。"""

    @staticmethod
    def _backend() -> _V2Backend:
        return _V2Backend(api_key="sk-test", base_url=_ROOT, model="seedance-1.0")

    def test_name_model_and_video_capabilities(self):
        b = self._backend()
        assert b.name == PROVIDER_V2_VIDEO
        assert b.model == "seedance-1.0"
        caps = b.video_capabilities
        assert caps.first_frame and caps.last_frame
        assert caps.max_reference_images == 4

    def test_constructor_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            _V2Backend(api_key="", base_url="https://x", model="m")

    def test_constructor_requires_base_url(self):
        with pytest.raises(ValueError, match="base_url"):
            _V2Backend(api_key="k", base_url="", model="m")

    async def test_generate_happy_path(self, tmp_path: Path):
        with _v2_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"id": "gen-1", "status": "queued"}))
            routes.poll.mock(
                side_effect=[
                    _json({"id": "gen-1", "status": "generating"}),
                    _json({"id": "gen-1", "status": "completed", "video": {"url": "https://cdn/v.mp4"}}),
                ]
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await self._backend().generate(
                VideoGenerationRequest(prompt="a cat", output_path=tmp_path / "o.mp4", duration_seconds=5)
            )

            submitted = only_request(routes.submit)
            assert str(submitted.url) == _GENERATIONS_URL
            assert request_json(submitted)["model"] == "seedance-1.0"
            assert submitted.headers["Authorization"] == "Bearer sk-test"
            assert routes.poll.calls.last.request.url.params["generation_id"] == "gen-1"

        assert result.video_path.read_bytes() == b"mp4"
        assert result.provider == PROVIDER_V2_VIDEO
        assert result.model == "seedance-1.0"
        assert result.task_id == "gen-1"
        assert result.video_uri == "https://cdn/v.mp4"

    async def test_generate_persists_job_id_when_task_id_set(self, tmp_path: Path):
        with _v2_api() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_json({"id": "gen-9"}))
            routes.poll.mock(return_value=_json({"status": "completed", "video": {"url": "https://cdn/v.mp4"}}))

            await self._backend().generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5, task_id="task-77"
                )
            )

        assert [(r["task_id"], r["job_id"]) for r in persisted] == [("task-77", "gen-9")]

    async def test_generate_missing_task_id_raises(self, tmp_path: Path):
        with _v2_api() as routes:
            routes.submit.mock(return_value=_json({"status": "queued"}))

            with pytest.raises(RuntimeError, match="task_id"):
                await self._backend().generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
                )

            assert routes.poll.call_count == 0

    async def test_generate_missing_video_url_raises(self, tmp_path: Path):
        with _v2_api() as routes:
            routes.submit.mock(return_value=_json({"id": "g"}))
            routes.poll.mock(return_value=_json({"status": "completed"}))

            with pytest.raises(RuntimeError, match="视频 URL"):
                await self._backend().generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
                )

            assert routes.download.call_count == 0

    async def test_generate_failed_status_raises(self, tmp_path: Path):
        with _v2_api() as routes:
            routes.submit.mock(return_value=_json({"id": "g"}))
            routes.poll.mock(return_value=_json({"status": "error", "error": {"message": "boom"}}))

            with pytest.raises(RuntimeError, match="boom"):
                await self._backend().generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
                )

            assert routes.download.call_count == 0

    async def test_resume_video_poll_and_download(self, tmp_path: Path):
        with _v2_api() as routes:
            routes.poll.mock(return_value=_json({"status": "completed", "video": {"url": "https://cdn/r.mp4"}}))
            routes.download.mock(return_value=httpx.Response(200, content=b"r"))

            result = await self._backend().resume_video(
                "gen-resume",
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5),
            )

            # resume 只 poll，不再 submit
            assert routes.submit.call_count == 0

        assert result.task_id == "gen-resume"
        assert result.video_path.read_bytes() == b"r"

    async def test_resume_404_raises_resume_expired(self, tmp_path: Path):
        with _v2_api() as routes:
            routes.poll.mock(return_value=_json({}, status_code=404))

            with pytest.raises(ResumeExpiredError):
                await self._backend().resume_video(
                    "gen-expired",
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5),
                )

            assert routes.poll.call_count == 1

    async def test_create_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """创建任务遇确定性 4xx（400）应一次失败，不重试。"""
        with _v2_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"error": "bad request"}, status_code=400))

            with pytest.raises(httpx.HTTPStatusError):
                await self._backend().generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
                )

            assert routes.submit.call_count == 1, "确定性 4xx 不该被 retry"

    async def test_poll_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """轮询遇确定性 4xx（401，如 token 轮换失效）应一次失败，不重试到 max_wait 超时。"""
        with _v2_api() as routes:
            routes.submit.mock(return_value=_json({"id": "gen-401", "status": "queued"}))
            routes.poll.mock(return_value=_json({"error": "unauthorized"}, status_code=401))

            with pytest.raises(httpx.HTTPStatusError):
                await self._backend().generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
                )

            assert routes.poll.call_count == 1, "轮询确定性 4xx 应一击失败，不重试到超时"

    async def test_create_read_timeout_fails_fast_with_manual_retry_hint(self, tmp_path: Path):
        """create 阶段 ReadTimeout（请求可能已送达）→ 不重试、单次失败、错误信息含手动重试提示。"""
        with _v2_api() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=httpx.ReadTimeout("read timed out"))

            with pytest.raises(AmbiguousSubmitError, match="手动重试"):
                await self._backend().generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
                )

            assert routes.submit.call_count == 1, "歧义态不该被 retry"
            assert routes.poll.call_count == 0

    async def test_create_connect_error_retries(self, tmp_path: Path):
        """create 阶段 ConnectError（请求确定未送达）→ 重试，第三次成功。"""
        with _v2_api() as routes, bounded_poll_clock():
            routes.submit.mock(
                side_effect=[
                    httpx.ConnectError("refused"),
                    httpx.ConnectError("refused"),
                    _json({"id": "gen-ok", "status": "queued"}),
                ]
            )
            routes.poll.mock(return_value=_json({"status": "completed", "video": {"url": "https://cdn/v.mp4"}}))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await self._backend().generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            )

            assert routes.submit.call_count == 3, "ConnectError 请求确定未送达，应重试"

        assert result.task_id == "gen-ok"

    async def test_create_retries_on_5xx(self, tmp_path: Path):
        """create 阶段收到 503 响应（服务端明示创建失败）→ 维持重试（现状保持）。"""
        busy = _json({"error": "upstream busy"}, status_code=503)

        with _v2_api() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=[busy, busy, _json({"id": "gen-503", "status": "queued"})])
            routes.poll.mock(return_value=_json({"status": "completed", "video": {"url": "https://cdn/v.mp4"}}))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await self._backend().generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            )

            assert routes.submit.call_count == 3, "5xx 应维持重试"

        assert result.task_id == "gen-503"

    async def test_poll_read_timeout_retries(self, tmp_path: Path):
        """poll 阶段 ReadTimeout（幂等 GET）→ 重试，不回归。"""
        with _v2_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"id": "gen-p", "status": "queued"}))
            routes.poll.mock(
                side_effect=[
                    httpx.ReadTimeout("read timed out"),
                    _json({"status": "completed", "video": {"url": "https://cdn/v.mp4"}}),
                ]
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await self._backend().generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)
            )

            assert routes.poll.call_count == 2, "poll 网络超时应重试"

        assert result.task_id == "gen-p"
