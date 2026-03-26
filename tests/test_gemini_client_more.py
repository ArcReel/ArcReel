import asyncio
from collections import deque
from types import SimpleNamespace

import pytest
from PIL import Image

from lib import gemini_client as gemini_module
from lib.gemini_client import GeminiClient, RateLimiter, get_shared_rate_limiter, with_retry, with_retry_async


class _FakeRateLimiter:
    def __init__(self):
        self.sync_calls = []
        self.async_calls = []

    def acquire(self, model):
        self.sync_calls.append(model)

    async def acquire_async(self, model):
        self.async_calls.append(model)


class _FakeAioModels:
    def __init__(self, content_response=None):
        self.content_response = content_response
        self.content_calls = []

    async def generate_content(self, **kwargs):
        self.content_calls.append(kwargs)
        return self.content_response


class _FakeModels:
    def __init__(self, content_response=None):
        self.content_response = content_response
        self.content_calls = []

    def generate_content(self, **kwargs):
        self.content_calls.append(kwargs)
        return self.content_response


def _build_client(models=None, aio_models=None):
    client = object.__new__(GeminiClient)
    client.rate_limiter = _FakeRateLimiter()
    client.backend = "aistudio"
    client.credentials = None
    client.project_id = None
    client.gcs_bucket = None
    client.client = SimpleNamespace(
        models=models or _FakeModels(),
        aio=SimpleNamespace(
            models=aio_models or _FakeAioModels(),
        ),
    )
    return client


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

    def test_text_config_helpers(self):
        client = _build_client()

        assert client._prepare_text_config(None) is None
        schema_cfg = client._prepare_text_config({"type": "object"})
        assert schema_cfg["response_mime_type"] == "application/json"
        assert client._process_text_response(SimpleNamespace(text="hello")) == "hello"

    @pytest.mark.asyncio
    async def test_text_generation_paths(self, tmp_path):
        text_response = SimpleNamespace(text="text-output")

        models = _FakeModels(content_response=text_response)
        aio_models = _FakeAioModels(content_response=text_response)
        client = _build_client(models=models, aio_models=aio_models)

        assert client.generate_text("hello", response_schema={"type": "object"}) == "text-output"
        assert await client.generate_text_async("hello-async") == "text-output"

    def test_analyze_style_image(self):
        style_response = SimpleNamespace(text=" cinematic ")
        style_client = _build_client(models=_FakeModels(content_response=style_response))
        image = Image.new("RGB", (3, 3), (100, 100, 100))
        assert style_client.analyze_style_image(image) == "cinematic"
