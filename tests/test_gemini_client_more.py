import asyncio
import io
import os
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from lib import gemini_client as gemini_module
from lib.gemini_client import GeminiClient, RateLimiter, get_shared_rate_limiter, with_retry, with_retry_async


class _FakeTypes:
    class Image:
        def __init__(self, image_bytes=None, mime_type=None):
            self.image_bytes = image_bytes
            self.mime_type = mime_type

    class Video:
        def __init__(self, uri=None, video_bytes=None, mime_type=None):
            self.uri = uri
            self.video_bytes = video_bytes
            self.mime_type = mime_type

        def save(self, path):
            Path(path).write_bytes(self.video_bytes or b"saved")

    class ImageConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class GenerateVideosConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class GenerateVideosSource:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


class _FakeRateLimiter:
    def __init__(self):
        self.sync_calls = []
        self.async_calls = []

    def acquire(self, model):
        self.sync_calls.append(model)

    async def acquire_async(self, model):
        self.async_calls.append(model)


class _FakeOperation:
    def __init__(self, done=True, response=None, error=None):
        self.done = done
        self.response = response
        self.error = error


class _FakeAioModels:
    def __init__(self, content_response=None, video_operation=None):
        self.content_response = content_response
        self.video_operation = video_operation
        self.content_calls = []
        self.video_calls = []

    async def generate_content(self, **kwargs):
        self.content_calls.append(kwargs)
        return self.content_response

    async def generate_videos(self, **kwargs):
        self.video_calls.append(kwargs)
        return self.video_operation


class _FakeModels:
    def __init__(self, content_response=None, video_operation=None):
        self.content_response = content_response
        self.video_operation = video_operation
        self.content_calls = []
        self.video_calls = []

    def generate_content(self, **kwargs):
        self.content_calls.append(kwargs)
        return self.content_response

    def generate_videos(self, **kwargs):
        self.video_calls.append(kwargs)
        return self.video_operation


class _FakeOperations:
    def __init__(self, next_operation):
        self.next_operation = next_operation
        self.calls = 0

    def get(self, operation):
        self.calls += 1
        return self.next_operation


class _FakeAioOperations:
    def __init__(self, next_operation):
        self.next_operation = next_operation
        self.calls = 0

    async def get(self, operation):
        self.calls += 1
        return self.next_operation


def _build_client(models=None, aio_models=None, operations=None, aio_operations=None):
    client = object.__new__(GeminiClient)
    client.types = _FakeTypes
    client.rate_limiter = _FakeRateLimiter()
    client.backend = "aistudio"
    client.credentials = None
    client.project_id = None
    client.gcs_bucket = None
    client.IMAGE_MODEL = "image-model"
    client.VIDEO_MODEL = "video-model"
    client.client = SimpleNamespace(
        models=models or _FakeModels(),
        aio=SimpleNamespace(
            models=aio_models or _FakeAioModels(),
            operations=aio_operations or _FakeAioOperations(_FakeOperation()),
        ),
        operations=operations or _FakeOperations(_FakeOperation()),
        files=SimpleNamespace(download=lambda file: None),
    )
    return client


def _image_response():
    img = Image.new("RGB", (4, 4), (255, 0, 0))

    class _Part:
        inline_data = object()

        @staticmethod
        def as_image():
            return img.copy()

    return SimpleNamespace(parts=[_Part()])


class TestGeminiClientMore:
    def test_retry_wrappers(self, monkeypatch):
        sleep_calls = []
        monkeypatch.setattr(gemini_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
        monkeypatch.setattr(gemini_module.random, "uniform", lambda a, b: 0.0)

        state = {"count": 0}

        @with_retry(max_attempts=3, backoff_seconds=(0, 0, 0), retryable_errors=(RuntimeError,))
        def _fn(output_path=None):
            state["count"] += 1
            if state["count"] < 3:
                raise RuntimeError("503 temporary")
            return "ok"

        assert _fn(output_path="x.txt") == "ok"
        assert state["count"] == 3
        assert len(sleep_calls) == 2

        @with_retry(max_attempts=2, backoff_seconds=(0,), retryable_errors=(RuntimeError,))
        def _bad():
            raise ValueError("fatal")

        with pytest.raises(ValueError):
            _bad()

    @pytest.mark.asyncio
    async def test_retry_async_wrapper(self, monkeypatch):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(gemini_module.asyncio, "sleep", _fake_sleep)
        monkeypatch.setattr(gemini_module.random, "uniform", lambda a, b: 0.0)

        state = {"count": 0}

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), retryable_errors=(RuntimeError,))
        async def _fn_async(output_path=None):
            state["count"] += 1
            if state["count"] < 3:
                raise RuntimeError("429 retry")
            return "ok"

        assert await _fn_async(output_path="y.txt") == "ok"
        assert state["count"] == 3
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_rate_limiter_and_shared_limiter(self, monkeypatch):
        limiter = RateLimiter({"m": 1}, request_gap=0)
        time_values = iter([0.0, 0.0, 61.0, 61.0])
        monkeypatch.setattr(gemini_module.time, "time", lambda: next(time_values))
        monkeypatch.setattr(gemini_module.time, "sleep", lambda _s: None)
        limiter.acquire("m")
        limiter.acquire("m")
        assert len(limiter.request_logs["m"]) == 1

        limiter2 = RateLimiter({"m": 2}, request_gap=1)
        limiter2.request_logs["m"] = deque([0.0])
        async_time_values = iter([0.2, 1.2])
        monkeypatch.setattr(gemini_module.time, "time", lambda: next(async_time_values))

        async_waits = []

        async def _fake_sleep(seconds):
            async_waits.append(seconds)

        monkeypatch.setattr(gemini_module.asyncio, "sleep", _fake_sleep)
        await limiter2.acquire_async("m")
        assert async_waits and async_waits[0] > 0

        gemini_module._shared_rate_limiter = None
        shared_1 = get_shared_rate_limiter(image_rpm=12, video_rpm=8)
        shared_2 = get_shared_rate_limiter()
        assert shared_1 is shared_2
        assert shared_1.limits[gemini_module._SHARED_IMAGE_MODEL_NAME] == 12
        assert shared_1.limits[gemini_module._SHARED_VIDEO_MODEL_NAME] == 8

        # When no params passed, defaults are used (15 for image)
        gemini_module._shared_rate_limiter = None
        shared_3 = get_shared_rate_limiter()
        assert shared_3.limits[gemini_module._SHARED_IMAGE_MODEL_NAME] == 15

    def test_prepare_param_and_config_helpers(self, tmp_path):
        client = _build_client()

        jpg = tmp_path / "ref.jpg"
        Image.new("RGB", (6, 6), (0, 0, 255)).save(jpg)
        image_param = client._prepare_image_param(jpg)
        assert image_param.mime_type == "image/jpeg"

        pil_image = Image.new("RGB", (6, 6), (0, 255, 0))
        image_param2 = client._prepare_image_param(pil_image)
        assert image_param2.mime_type == "image/png"
        assert isinstance(image_param2.image_bytes, bytes)

        raw_obj = object()
        assert client._prepare_image_param(raw_obj) is raw_obj
        assert client._prepare_image_param(None) is None

        mp4 = tmp_path / "clip.mp4"
        mp4.write_bytes(b"video-bytes")
        video_param, video_bytes = client._prepare_video_param(mp4)
        assert video_param.mime_type == "video/mp4"
        assert video_bytes == b"video-bytes"

        uri_video, uri_bytes = client._prepare_video_param("gs://bucket/file.mp4")
        assert uri_video.uri == "gs://bucket/file.mp4"
        assert uri_bytes is None

        direct_video = _FakeTypes.Video(video_bytes=b"abc", mime_type="video/mp4")
        direct_param, direct_bytes = client._prepare_video_param(direct_video)
        assert direct_param is direct_video
        assert direct_bytes == b"abc"

        with pytest.raises(ValueError):
            client._prepare_video_param("not-found.mp4")

        image_cfg = client._prepare_image_config("9:16", "2K")
        assert image_cfg.kwargs["response_modalities"] == ["IMAGE"]

        video_gen_cfg = client._prepare_video_generate_config("9:16", "1080p", "8", "none")
        assert video_gen_cfg.kwargs["resolution"] == "1080p"
        video_ext_cfg = client._prepare_video_extend_config("gs://bucket/out.mp4")
        assert video_ext_cfg.kwargs["output_gcs_uri"] == "gs://bucket/out.mp4"

        assert client._prepare_text_config(None) is None
        schema_cfg = client._prepare_text_config({"type": "object"})
        assert schema_cfg["response_mime_type"] == "application/json"
        assert client._process_text_response(SimpleNamespace(text="hello")) == "hello"

        assert client._extract_name_from_path("characters/Alice.png") == "Alice"
        assert client._extract_name_from_path("storyboards/scene_001.png") is None
        assert client._extract_name_from_path(Image.new("RGB", (1, 1))) is None

        contents = client._build_contents_with_labeled_refs(
            "prompt",
            reference_images=[
                jpg,
                {
                    "image": pil_image,
                    "label": "上一分镜图（镜头衔接参考）",
                    "description": "仅用于延续前一镜头的构图与色调。",
                },
            ],
        )
        assert contents[-1] == "prompt"
        assert any(item == "ref" for item in contents if isinstance(item, str))
        assert any(
            "上一分镜图（镜头衔接参考）" in item
            for item in contents
            if isinstance(item, str)
        )

    def test_process_image_and_download_video(self, tmp_path, monkeypatch):
        client = _build_client(models=_FakeModels(content_response=_image_response()))
        output = tmp_path / "out.png"
        image = client._process_image_response(_image_response(), output)
        assert image.size == (4, 4)
        assert output.exists()

        with pytest.raises(RuntimeError):
            client._process_image_response(SimpleNamespace(parts=[SimpleNamespace(inline_data=None)]))

        # AI Studio path
        video_ref = _FakeTypes.Video(video_bytes=b"v")
        client._download_video(video_ref, tmp_path / "a.mp4")

        # Vertex bytes path
        client.backend = "vertex"
        v_out = tmp_path / "vertex-bytes.mp4"
        client._download_video(_FakeTypes.Video(video_bytes=b"vertex"), v_out)
        assert v_out.read_bytes() == b"vertex"

        # Vertex URI path
        downloaded = {}

        def _fake_urlretrieve(uri, output_path):
            downloaded["uri"] = uri
            Path(output_path).write_bytes(b"uri-video")

        monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
        uri_out = tmp_path / "vertex-uri.mp4"
        client._download_video(_FakeTypes.Video(uri="https://example.com/video.mp4"), uri_out)
        assert downloaded["uri"] == "https://example.com/video.mp4"
        assert uri_out.exists()

        with pytest.raises(RuntimeError):
            client._download_video(SimpleNamespace(), tmp_path / "bad.mp4")

    @pytest.mark.asyncio
    async def test_text_and_video_generation_paths(self, tmp_path, monkeypatch):
        text_response = SimpleNamespace(text="text-output")
        video_ref = _FakeTypes.Video(uri="https://video", video_bytes=b"bin", mime_type="video/mp4")
        generated = SimpleNamespace(video=video_ref)
        video_response = SimpleNamespace(generated_videos=[generated])
        done_operation = _FakeOperation(done=True, response=video_response)

        models = _FakeModels(content_response=text_response, video_operation=done_operation)
        aio_models = _FakeAioModels(content_response=text_response, video_operation=done_operation)
        client = _build_client(
            models=models,
            aio_models=aio_models,
            operations=_FakeOperations(done_operation),
            aio_operations=_FakeAioOperations(done_operation),
        )

        assert client.generate_text("hello", response_schema={"type": "object"}) == "text-output"
        assert await client.generate_text_async("hello-async") == "text-output"

        img = tmp_path / "start.png"
        Image.new("RGB", (5, 5), (1, 2, 3)).save(img)

        # sync generate mode
        downloaded = {"count": 0}

        def _fake_download(_video, _path):
            downloaded["count"] += 1

        client._download_video = _fake_download  # type: ignore[method-assign]
        out_path = tmp_path / "sync-video.mp4"
        sync_out, sync_ref, sync_uri = client.generate_video(
            prompt="gen video",
            start_image=img,
            output_path=out_path,
            poll_interval=0,
            max_wait_time=10,
        )
        assert sync_out == out_path
        assert sync_ref is video_ref
        assert sync_uri == "https://video"
        assert downloaded["count"] == 1

        # async generate mode
        async_out, async_ref, async_uri = await client.generate_video_async(
            prompt="async gen",
            start_image=img,
            output_path=None,
            poll_interval=0,
            max_wait_time=10,
        )
        assert async_out is None
        assert async_ref is video_ref
        assert async_uri == "https://video"

        # timeout branch (sync)
        never_done = _FakeOperation(done=False, response=video_response)
        client.client.models.generate_videos = lambda **kwargs: never_done
        with pytest.raises(TimeoutError):
            client.generate_video(
                prompt="timeout",
                start_image=img,
                poll_interval=0,
                max_wait_time=0,
            )

        # async vertex extend validation branch
        client.backend = "vertex"
        client.gcs_bucket = None
        with pytest.raises(ValueError):
            await client.generate_video_async(
                prompt="extend",
                video=tmp_path / "input.mp4",
                output_path=None,
                poll_interval=0,
                max_wait_time=10,
            )

    def test_process_video_result_and_style_image(self, tmp_path):
        video_ref = _FakeTypes.Video(uri="gs://bucket/out.mp4", video_bytes=b"v")
        generated = SimpleNamespace(video=video_ref)
        ok_operation = _FakeOperation(done=True, response=SimpleNamespace(generated_videos=[generated]))
        client = _build_client()

        saved = {"called": False}
        client._download_video = lambda ref, output: saved.__setitem__("called", True)  # type: ignore[method-assign]
        out, ref, uri = client._process_video_result(ok_operation, tmp_path / "v.mp4", is_extend_mode=False)
        assert out == tmp_path / "v.mp4"
        assert ref is video_ref
        assert uri == "gs://bucket/out.mp4"
        assert saved["called"] is True

        with pytest.raises(RuntimeError):
            client._process_video_result(_FakeOperation(done=True, response=None), None, is_extend_mode=False)

        with pytest.raises(RuntimeError):
            client._process_video_result(
                _FakeOperation(done=True, response=SimpleNamespace(generated_videos=[]), error="E"),
                None,
                is_extend_mode=True,
            )

        style_response = SimpleNamespace(text=" cinematic ")
        style_client = _build_client(models=_FakeModels(content_response=style_response))
        image = Image.new("RGB", (3, 3), (100, 100, 100))
        assert style_client.analyze_style_image(image) == "cinematic"
