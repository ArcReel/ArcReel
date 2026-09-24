"""GeminiVideoBackend 单元测试 — mock genai SDK。"""

import urllib.error
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import errors as genai_errors

from lib.backends.gemini_shared import VERTEX_SCOPES
from lib.backends.video_backend_contract import (
    ResumeExpiredError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.billing.cost_calculator import cost_calculator
from tests.fakes import bounded_poll_clock


class _RecordingRateLimiter:
    """限流器替身：记录每次取配额时报的模型名，不做等待。

    「按型号取配额」这个契约落在 ``acquired`` 记录的模型名上，而不是替身的调用对象。
    """

    def __init__(self) -> None:
        self.acquired: list[str] = []

    def acquire(self, model: str) -> None:
        self.acquired.append(model)

    async def acquire_async(self, model: str) -> None:
        self.acquired.append(model)


@pytest.fixture
def mock_rate_limiter():
    return _RecordingRateLimiter()


@pytest.fixture
def gemini_backend(mock_rate_limiter):
    """创建 aistudio 模式的 GeminiVideoBackend（mock genai SDK）。"""
    with patch("google.genai") as genai_mock, patch("google.genai.types"):
        from lib.backends.video_backends.gemini import GeminiVideoBackend

        b = GeminiVideoBackend(
            backend_type="aistudio",
            api_key="test-key",
            rate_limiter=mock_rate_limiter,
        )
        # 构造期 genai.Client 的调用记录留在这里，_client 随后换成裸替身供各用例自行布置
        b._test_genai = genai_mock
        b._client = MagicMock()
        b._client.aio = MagicMock()
        yield b


# ── 属性测试 ──────────────────────────────────────────────


class TestGeminiVideoBackendProperties:
    def test_name(self, gemini_backend):
        assert gemini_backend.name == "gemini-aistudio"
        # aistudio 模式：api_key 原样交给 genai.Client，未给 base_url 则不带 http_options
        gemini_backend._test_genai.Client.assert_called_once_with(api_key="test-key", http_options=None)

    def test_video_capabilities_aistudio(self, gemini_backend):
        caps = gemini_backend.video_capabilities
        assert caps.last_frame is True
        assert caps.max_reference_images == 3
        # 未传 video_model 时落到默认视频型号
        assert gemini_backend.model == cost_calculator.DEFAULT_VIDEO_MODEL

    def test_vertex_backend_constructs_from_credentials_file(self, mock_rate_limiter, tmp_path):
        # 准备 mock vertex 凭证文件
        creds_file = tmp_path / "vertex_credentials.json"
        creds_file.write_text('{"project_id": "test-project"}')

        with (
            patch("google.genai") as genai_mock,
            patch("google.genai.types"),
            patch(
                "lib.backends.video_backends.gemini.resolve_vertex_credentials_path",
                return_value=creds_file,
            ),
            patch("google.oauth2.service_account.Credentials.from_service_account_file") as from_service_account_file,
        ):
            from lib.backends.video_backends.gemini import GeminiVideoBackend

            b = GeminiVideoBackend(
                backend_type="vertex",
                rate_limiter=mock_rate_limiter,
            )
            assert b.name == "gemini-vertex"
            # 凭证从服务账号文件按 Vertex scopes 加载；client 以 vertexai 模式 + 凭证文件里的 project_id 构造
            from_service_account_file.assert_called_once_with(str(creds_file), scopes=VERTEX_SCOPES)
            genai_mock.Client.assert_called_once_with(
                vertexai=True,
                project="test-project",
                location="global",
                credentials=from_service_account_file.return_value,
            )


# ── 生成测试 ──────────────────────────────────────────────


def _make_done_operation(video_uri="gs://bucket/video.mp4"):
    """构造一个已完成的 operation mock。"""
    mock_video = MagicMock()
    mock_video.uri = video_uri
    mock_video.video_bytes = b"fake-video-bytes"

    mock_generated = MagicMock()
    mock_generated.video = mock_video

    mock_response = MagicMock()
    mock_response.generated_videos = [mock_generated]

    mock_op = MagicMock()
    mock_op.done = True
    mock_op.response = mock_response
    mock_op.error = None
    return mock_op


class TestGeminiVideoBackendGenerate:
    async def test_generate_text_to_video(self, gemini_backend, tmp_path):
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="a cat walking",
            output_path=output,
            duration_seconds=8,
        )

        result = await gemini_backend.generate(request)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "gemini"
        assert result.model == gemini_backend.model
        assert result.video_uri == "gs://bucket/video.mp4"
        assert result.video_path == output
        assert result.duration_seconds == 8
        # aistudio 模式的成片恒带音轨
        assert result.generate_audio is True

        # 确认调用了 API
        gemini_backend._client.aio.models.generate_videos.assert_awaited_once()
        # 成片经 files.download 取回后落盘到 output_path
        video = mock_op.response.generated_videos[0].video
        gemini_backend._client.files.download.assert_called_once_with(file=video)
        video.save.assert_called_once_with(str(output))

    async def test_generate_image_to_video(self, gemini_backend, tmp_path):
        output = tmp_path / "out.mp4"
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"fake-png-data")

        mock_op = _make_done_operation(video_uri=None)
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="cat moves forward",
            output_path=output,
            start_image=frame,
        )

        result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        assert result.video_path == output

    async def test_generate_polls_until_done(self, gemini_backend, tmp_path):
        """测试轮询逻辑：先返回未完成，再返回已完成。"""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        done_op = _make_done_operation()

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=done_op)

        request = VideoGenerationRequest(
            prompt="a sunset",
            output_path=output,
        )

        with bounded_poll_clock():
            result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        # 轮询以 submit 返回的 operation 对象查询状态
        gemini_backend._client.aio.operations.get.assert_awaited_once_with(pending_op)

    async def test_generate_empty_result_raises(self, gemini_backend, tmp_path):
        """API 返回空结果时应抛出 RuntimeError。"""
        output = tmp_path / "out.mp4"

        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = MagicMock()
        mock_op.response.generated_videos = []
        mock_op.error = None

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        with pytest.raises(RuntimeError, match="API 返回空结果"):
            await gemini_backend.generate(request)

    async def test_generate_error_in_operation(self, gemini_backend, tmp_path):
        """operation 包含 error 时应抛出 RuntimeError。"""
        output = tmp_path / "out.mp4"

        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = None
        mock_op.error = "Something went wrong"

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        # 错误消息携带 operation.error 的内容，与空结果分支的文案区分开
        with pytest.raises(RuntimeError, match="视频生成失败: Something went wrong"):
            await gemini_backend.generate(request)

    async def test_rate_limiter_called(self, gemini_backend, mock_rate_limiter, tmp_path):
        """确认 generate 会调用限流器。"""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        await gemini_backend.generate(request)
        assert mock_rate_limiter.acquired == [gemini_backend._video_model]

    async def test_no_negative_prompt_in_config(self, gemini_backend, tmp_path):
        """negative_prompt 走 prompt 文本通道，GenerateVideosConfig 不带该字段。"""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        await gemini_backend.generate(request)

        config_call = gemini_backend._types.GenerateVideosConfig.call_args
        assert "negative_prompt" not in config_call.kwargs


class TestGeminiRetryBehavior:
    """测试任务创建与轮询的重试分离行为。"""

    async def test_poll_transient_error_retries_without_recreating_task(self, gemini_backend, tmp_path):
        """轮询阶段瞬态错误应重试轮询，而不是重新创建任务。"""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        done_op = _make_done_operation()

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        # 第一次轮询抛 ConnectionError，第二次返回完成
        gemini_backend._client.aio.operations.get = AsyncMock(
            side_effect=[ConnectionError("connection reset"), done_op]
        )

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with bounded_poll_clock():
            result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        # 关键断言：任务只创建了一次
        gemini_backend._client.aio.models.generate_videos.assert_awaited_once()
        # 轮询调用了两次（一次失败 + 一次成功）
        assert gemini_backend._client.aio.operations.get.await_count == 2

    async def test_create_retries_on_transient_error(self, gemini_backend, tmp_path):
        """任务创建阶段的瞬态错误应由 @with_retry_async 重试。"""
        output = tmp_path / "out.mp4"

        done_op = _make_done_operation()
        # 第一次创建抛 ConnectionError，第二次成功
        gemini_backend._client.aio.models.generate_videos = AsyncMock(
            side_effect=[ConnectionError("connection reset"), done_op]
        )

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with (
            bounded_poll_clock(),
        ):
            result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        # 创建调用了两次（一次失败 + 一次成功）
        assert gemini_backend._client.aio.models.generate_videos.await_count == 2

    async def test_poll_non_retryable_error_propagates(self, gemini_backend, tmp_path):
        """轮询阶段不可重试的错误应直接抛出。"""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=ValueError("invalid response"))

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(ValueError, match="invalid response"), bounded_poll_clock():
            await gemini_backend.generate(request)

        # 创建只调用一次
        gemini_backend._client.aio.models.generate_videos.assert_awaited_once()
        # 轮询只尝试一次就抛出
        assert gemini_backend._client.aio.operations.get.await_count == 1


# ── _prepare_image_param 测试 ─────────────────────────────


class TestPrepareImageParam:
    def test_none_returns_none(self, gemini_backend):
        assert gemini_backend._prepare_image_param(None) is None

    def test_path_reads_file(self, gemini_backend, tmp_path):
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic

        result = gemini_backend._prepare_image_param(img_file)
        # 文件原始字节与按 .jpg 后缀映射的 mime 交给 types.Image
        assert result is gemini_backend._types.Image.return_value
        gemini_backend._types.Image.assert_called_once_with(image_bytes=b"\xff\xd8\xff\xe0", mime_type="image/jpeg")

    def test_pil_image(self, gemini_backend):
        from io import BytesIO

        from PIL import Image as PILImage

        img = PILImage.new("RGB", (10, 10), color="red")
        result = gemini_backend._prepare_image_param(img)
        # PIL 图像编码为 PNG 字节，mime 为 image/png
        assert result is gemini_backend._types.Image.return_value
        image_kwargs = gemini_backend._types.Image.call_args.kwargs
        assert image_kwargs["mime_type"] == "image/png"
        assert PILImage.open(BytesIO(image_kwargs["image_bytes"])).format == "PNG"


# ── _download_video 测试 ──────────────────────────────────


class _AiStudioFileRef:
    """AI Studio 文件引用替身：先 download 取到字节，save 才能把它落到给定路径。

    这一支的契约是「下载后落盘到 output_path」，断言落在真实文件内容上；顺序颠倒
    （未下载先落盘）在替身里直接 fail-loud。
    """

    def __init__(self, content: bytes = b"aistudio-bytes") -> None:
        self._content = content
        self._downloaded = False

    def download(self) -> None:
        self._downloaded = True

    def save(self, path: str) -> None:
        if not self._downloaded:
            raise RuntimeError("save 前须先 files.download 取到字节")
        Path(path).write_bytes(self._content)


class TestDownloadVideo:
    def test_aistudio_download(self, gemini_backend, tmp_path):
        output = tmp_path / "video.mp4"
        ref = _AiStudioFileRef()
        gemini_backend._client = SimpleNamespace(files=SimpleNamespace(download=lambda file: file.download()))

        gemini_backend._download_video(ref, output)

        assert output.read_bytes() == b"aistudio-bytes"

    def test_vertex_download_from_bytes(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"

        mock_ref = MagicMock()
        mock_ref.video_bytes = b"video-data"

        gemini_backend._download_video(mock_ref, output)

        assert output.read_bytes() == b"video-data"

    def test_vertex_no_data_raises(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"

        mock_ref = MagicMock(spec=[])  # no attributes

        with pytest.raises(RuntimeError, match="无法获取视频数据"):
            gemini_backend._download_video(mock_ref, output)


def _sdk_error(code: int, status: str, message: str) -> genai_errors.ClientError:
    """按 SDK 真实形态构造 4xx 响应异常：code 取自 HTTP 状态码，响应体是 Google 标准 error 包。"""
    return genai_errors.ClientError(code, {"error": {"code": code, "message": message, "status": status}})


class TestGeminiResumeVideo:
    """resume_video 路径：只有按 job_id 查询 operation 得到的 404 归 ResumeExpiredError。"""

    async def test_mid_poll_not_found_classified_as_resume_expired(self, gemini_backend, tmp_path):
        # 初次 operations.get 返回 pending 让 poll 进入循环；poll_fn 中抛 NOT_FOUND
        pending_op = MagicMock()
        pending_op.done = False
        get_calls = {"n": 0}

        async def _fake_get(_op):
            get_calls["n"] += 1
            if get_calls["n"] == 1:
                return pending_op
            raise _sdk_error(404, "NOT_FOUND", "Operation not found")

        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=_fake_get)
        # GenerateVideosOperation.model_validate 用 MagicMock，返回任意对象即可
        gemini_backend._types.GenerateVideosOperation.model_validate = MagicMock(return_value=pending_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with bounded_poll_clock(), pytest.raises(ResumeExpiredError) as ei:
            await gemini_backend.resume_video("op-xyz", request)
        assert ei.value.job_id == "op-xyz"

    async def test_mid_poll_not_found_is_not_retried_when_message_carries_status_digits(self, gemini_backend, tmp_path):
        # SDK 异常文本带整份响应，operation 名里的数字串可能含 "500" / "429"：轮询重试按状态码判定，
        # 404 不因这些子串被当成瞬态错误重试
        pending_op = MagicMock()
        pending_op.done = False
        get_calls = {"n": 0}

        async def _fake_get(_op):
            get_calls["n"] += 1
            if get_calls["n"] == 1:
                return pending_op
            raise _sdk_error(404, "NOT_FOUND", "Operation projects/5001/operations/4290 not found")

        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=_fake_get)
        gemini_backend._types.GenerateVideosOperation.model_validate = MagicMock(return_value=pending_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with bounded_poll_clock(), pytest.raises(ResumeExpiredError):
            await gemini_backend.resume_video("op-digits", request)
        assert get_calls["n"] == 2

    async def test_initial_get_not_found_classified_as_resume_expired(self, gemini_backend, tmp_path):
        gemini_backend._client.aio.operations.get = AsyncMock(
            side_effect=_sdk_error(404, "NOT_FOUND", "Operation not found")
        )
        rebuilt_op = MagicMock()
        gemini_backend._types.GenerateVideosOperation.model_validate = MagicMock(return_value=rebuilt_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(ResumeExpiredError) as ei:
            await gemini_backend.resume_video("op-not-found", request)

        assert ei.value.provider == "gemini"
        # 用 job_id 重建未完成的 operation，再以重建对象向远端查询
        gemini_backend._types.GenerateVideosOperation.model_validate.assert_called_once_with(
            {"name": "op-not-found", "done": False}
        )
        gemini_backend._client.aio.operations.get.assert_awaited_once_with(rebuilt_op)

    @pytest.mark.parametrize(
        "error",
        [
            # INVALID_ARGUMENT 表达入参不合法（如非法 operation 格式），不是 operation 不存在
            _sdk_error(400, "INVALID_ARGUMENT", "malformed operation name"),
            # 非 SDK 状态码的异常，文本里出现 "not found" / "expired" 也不代表 operation 已被回收
            RuntimeError("operation not found"),
            RuntimeError("resource expired after 24h"),
        ],
        ids=["invalid-argument", "not-found-text", "expired-text"],
    )
    async def test_query_error_without_not_found_status_propagates(self, gemini_backend, tmp_path, error):
        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=error)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(type(error)) as ei:
            await gemini_backend.resume_video("op-xyz", request)

        assert ei.value is error

    async def test_completed_operation_error_mentioning_not_found_is_not_expired(self, gemini_backend, tmp_path):
        # operation 已查到且完成，只是生成失败：失败原因文本与 operation 存续无关
        failed_op = MagicMock()
        failed_op.done = True
        failed_op.response = None
        failed_op.error = {"code": 5, "message": "Requested entity was not found."}
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=failed_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(RuntimeError, match="视频生成失败") as ei:
            await gemini_backend.resume_video("op-xyz", request)

        assert not isinstance(ei.value, ResumeExpiredError)

    async def test_vertex_artifact_404_propagates_as_download_failure(self, gemini_backend, tmp_path):
        # 远端任务已成功（operation 完成且带成片 URI），取件时产物 URL 返回 404
        gemini_backend._backend_type = "vertex"
        video_uri = "https://storage.googleapis.com/bucket/video.mp4"
        done_op = _make_done_operation(video_uri=video_uri)
        done_op.response.generated_videos[0].video.video_bytes = None
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=done_op)
        not_found = urllib.error.HTTPError(video_uri, 404, "Not Found", Message(), None)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with (
            patch("urllib.request.urlretrieve", side_effect=not_found),
            pytest.raises(urllib.error.HTTPError) as ei,
        ):
            await gemini_backend.resume_video("op-xyz", request)

        assert ei.value is not_found
        assert not request.output_path.exists()

    async def test_aistudio_artifact_404_propagates_as_download_failure(self, gemini_backend, tmp_path):
        # 远端任务已成功，取件时 SDK 的 files.download 返回 404：取件失败不是 operation 过期
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=_make_done_operation())
        not_found = _sdk_error(404, "NOT_FOUND", "File not found")
        gemini_backend._client.files.download.side_effect = not_found

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(genai_errors.ClientError) as ei:
            await gemini_backend.resume_video("op-xyz", request)

        assert ei.value is not_found
