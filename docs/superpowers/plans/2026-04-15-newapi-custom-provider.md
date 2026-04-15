# NewAPI 自定义供应商格式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 ArcReel 的自定义供应商体系新增 `api_format = "newapi"`，其中文本/图片复用 OpenAI delegate，视频通过直连 NewAPI 统一端点 `/v1/video/generations` 的新后端 `NewAPIVideoBackend` 实现。

**Architecture:** `api_format` 扩成三值枚举 `{openai, google, newapi}`。Factory 在 `newapi` 分支下：文本/图片走 `OpenAITextBackend`/`OpenAIImageBackend`（OpenAI 兼容），视频走新的 `NewAPIVideoBackend`（`httpx` 直连：`POST /v1/video/generations` → 轮询 `GET /v1/video/generations/{task_id}` → 下载 `url`）。模型发现与连接测试复用 OpenAI 路径。

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy async + `httpx.AsyncClient` + React 19 + TypeScript。

**参考：** 设计文档 `docs/superpowers/specs/2026-04-15-newapi-custom-provider-design.md`。

---

## File Map

**新建：**
- `lib/video_backends/newapi.py` — `NewAPIVideoBackend` 实现
- `tests/test_newapi_video_backend.py` — backend 单元测试（mock httpx）

**修改：**
- `lib/providers.py` — 增加 `PROVIDER_NEWAPI` 常量
- `lib/video_backends/__init__.py` — 注册 NewAPIVideoBackend
- `lib/custom_provider/factory.py` — 增加 `newapi` 分支 + `_create_newapi_backend`
- `lib/custom_provider/discovery.py` — `newapi` 复用 OpenAI 路径
- `lib/db/models/custom_provider.py` — 字段注释更新
- `server/routers/custom_providers.py` — `_run_connection_test` 支持 `newapi`，注释更新
- `lib/i18n/zh/errors.py` / `lib/i18n/en/errors.py` — 文案更新
- `frontend/src/types/custom-provider.ts` — 类型联合扩展
- `frontend/src/components/pages/settings/CustomProviderForm.tsx` — 下拉选项
- `tests/test_custom_provider_factory.py` — newapi 分支覆盖
- `tests/test_model_discovery.py` — newapi 分支覆盖
- `tests/test_custom_providers_api.py` — 入参校验扩展

---

## Task 1: 添加 PROVIDER_NEWAPI 常量

**Files:**
- Modify: `lib/providers.py:1-13`

- [ ] **Step 1: 追加常量**

编辑 `lib/providers.py`，在 `PROVIDER_OPENAI` 下方加一行：

```python
PROVIDER_OPENAI = "openai"
PROVIDER_NEWAPI = "newapi"
```

- [ ] **Step 2: 验证导入**

Run: `uv run python -c "from lib.providers import PROVIDER_NEWAPI; print(PROVIDER_NEWAPI)"`
Expected: `newapi`

- [ ] **Step 3: Commit**

```bash
git add lib/providers.py
git commit -m "feat(providers): add PROVIDER_NEWAPI constant"
```

---

## Task 2: NewAPIVideoBackend — 先写失败的文生视频测试

**Files:**
- Create: `tests/test_newapi_video_backend.py`

- [ ] **Step 1: 创建测试文件骨架 + 第一个测试**

```python
"""NewAPIVideoBackend 单元测试（mock httpx）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.providers import PROVIDER_NEWAPI
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
)


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


class TestNewAPIVideoBackend:
    def test_name_and_model(self):
        from lib.video_backends.newapi import NewAPIVideoBackend

        backend = NewAPIVideoBackend(
            api_key="sk-test", base_url="https://example.com/v1", model="kling-v1"
        )
        assert backend.name == PROVIDER_NEWAPI
        assert backend.model == "kling-v1"

    def test_capabilities(self):
        from lib.video_backends.newapi import NewAPIVideoBackend

        backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://x/v1", model="m")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert backend.video_capabilities.reference_images is False
        assert backend.video_capabilities.max_reference_images == 0

    async def test_text_to_video_happy_path(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "task-42", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "task-42",
                "status": "completed",
                "url": "https://cdn.example.com/out.mp4",
                "format": "mp4",
                "metadata": {"duration": 5, "fps": 24, "width": 720, "height": 1280, "seed": 0},
            },
        )
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"mp4-bytes"
        download_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(side_effect=[poll_resp, download_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(
                api_key="sk-test", base_url="https://example.com/v1", model="kling-v1"
            )
            request = VideoGenerationRequest(
                prompt="A cat running",
                output_path=tmp_path / "out.mp4",
                aspect_ratio="9:16",
                resolution="720p",
                duration_seconds=5,
            )
            result = await backend.generate(request)

        assert result.video_path == tmp_path / "out.mp4"
        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.provider == PROVIDER_NEWAPI
        assert result.model == "kling-v1"
        assert result.duration_seconds == 5
        assert result.task_id == "task-42"

        post_call = mock_client.post.call_args
        assert post_call.args[0].endswith("/video/generations")
        assert post_call.kwargs["json"]["model"] == "kling-v1"
        assert post_call.kwargs["json"]["prompt"] == "A cat running"
        assert post_call.kwargs["json"]["width"] == 720
        assert post_call.kwargs["json"]["height"] == 1280
        assert post_call.kwargs["json"]["duration"] == 5
        assert post_call.kwargs["json"]["n"] == 1
        assert "image" not in post_call.kwargs["json"]
        assert post_call.kwargs["headers"]["Authorization"] == "Bearer sk-test"
```

- [ ] **Step 2: 运行测试（应该导入失败）**

Run: `uv run pytest tests/test_newapi_video_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.video_backends.newapi'`

---

## Task 3: NewAPIVideoBackend — 最小实现使测试通过

**Files:**
- Create: `lib/video_backends/newapi.py`

- [ ] **Step 1: 实现 backend**

```python
"""NewAPIVideoBackend — NewAPI 统一视频生成端点后端。

对接 NewAPI 的 /v1/video/generations 接口，支持 Sora / Kling / 即梦 / Wan / Veo
等多家厂商模型，靠请求体的 model 字段分发。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx

from lib.providers import PROVIDER_NEWAPI
from lib.retry import (
    BASE_RETRYABLE_ERRORS,
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v1"

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 600
_POLL_TIMEOUT_PER_SECOND = 30

_SIZE_MAP: dict[tuple[str, str], tuple[int, int]] = {
    ("720p", "9:16"): (720, 1280),
    ("720p", "16:9"): (1280, 720),
    ("1080p", "9:16"): (1080, 1920),
    ("1080p", "16:9"): (1920, 1080),
}
_DEFAULT_SIZE: tuple[int, int] = (720, 1280)


def _resolve_size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    return _SIZE_MAP.get((resolution, aspect_ratio), _DEFAULT_SIZE)


def _encode_image_to_data_uri(path: Path) -> str:
    mime = IMAGE_MIME_TYPES.get(path.suffix.lower(), "image/png")
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


class NewAPIVideoBackend:
    """NewAPI 统一视频生成端点后端。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("NewAPIVideoBackend 需要 api_key")
        if not base_url:
            raise ValueError("NewAPIVideoBackend 需要 base_url")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_NEWAPI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(reference_images=False, max_reference_images=0)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        width, height = _resolve_size(request.resolution, request.aspect_ratio)
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "width": width,
            "height": height,
            "duration": request.duration_seconds,
            "n": 1,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.negative_prompt:
            payload.setdefault("metadata", {})["negative_prompt"] = request.negative_prompt
        if request.start_image and Path(request.start_image).exists():
            payload["image"] = _encode_image_to_data_uri(Path(request.start_image))
        if request.reference_images:
            logger.warning(
                "NewAPIVideoBackend 不支持多张参考图（reference_images=%d），已忽略",
                len(request.reference_images),
            )

        logger.info("NewAPI 视频生成开始: model=%s, duration=%s", self._model, request.duration_seconds)

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("NewAPI 任务创建: task_id=%s", task_id)

            final = await self._poll_until_done(
                client, task_id=task_id, max_wait=self._max_wait(request.duration_seconds)
            )
            video_url = final.get("url")
            if not video_url:
                raise RuntimeError(f"NewAPI 任务完成但缺少 url 字段: {final}")

            await self._download(client, video_url, request.output_path)

        meta = final.get("metadata") or {}
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_NEWAPI,
            model=self._model,
            duration_seconds=int(meta.get("duration") or request.duration_seconds),
            task_id=task_id,
            seed=meta.get("seed"),
        )

    # ------------------------------------------------------------------
    # HTTP helpers (each independently retried)
    # ------------------------------------------------------------------

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retryable_errors=BASE_RETRYABLE_ERRORS + (httpx.HTTPError,),
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        resp = await client.post(
            f"{self._base_url}/video/generations",
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        body = resp.json()
        task_id = body.get("task_id")
        if not task_id:
            raise RuntimeError(f"NewAPI 创建任务返回体缺少 task_id: {body}")
        return task_id

    async def _poll_until_done(
        self, client: httpx.AsyncClient, *, task_id: str, max_wait: float
    ) -> dict:
        elapsed = 0.0
        while True:
            if elapsed >= max_wait:
                raise TimeoutError(f"NewAPI 视频任务超时（{max_wait:.0f}秒）: task_id={task_id}")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS
            state = await self._poll_once(client, task_id)
            status = state.get("status")
            if status == "completed":
                return state
            if status == "failed":
                err = (state.get("error") or {}).get("message") or "unknown"
                raise RuntimeError(f"NewAPI 视频生成失败: {err}")

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retryable_errors=BASE_RETRYABLE_ERRORS + (httpx.HTTPError,),
    )
    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}/video/generations/{task_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retryable_errors=BASE_RETRYABLE_ERRORS + (httpx.HTTPError,),
    )
    async def _download(self, client: httpx.AsyncClient, url: str, output_path: Path) -> None:
        resp = await client.get(url, headers=self._headers())
        resp.raise_for_status()

        def _write():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)

        await asyncio.to_thread(_write)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
```

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/test_newapi_video_backend.py -v`
Expected: 三个测试都 PASS

- [ ] **Step 3: Commit**

```bash
git add lib/video_backends/newapi.py tests/test_newapi_video_backend.py
git commit -m "feat(video-backends): add NewAPIVideoBackend for unified /v1/video/generations"
```

---

## Task 4: 追加图生视频测试（Base64 编码）

**Files:**
- Modify: `tests/test_newapi_video_backend.py`（追加方法）

- [ ] **Step 1: 追加测试方法到 TestNewAPIVideoBackend 类**

```python
    async def test_image_to_video_encodes_base64(self, tmp_path: Path):
        img_path = tmp_path / "start.png"
        img_path.write_bytes(b"\x89PNG\r\nfake")

        create_resp = _make_response(200, {"task_id": "t1", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "t1",
                "status": "completed",
                "url": "https://cdn/x.mp4",
                "metadata": {"duration": 5},
            },
        )
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"v"
        download_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(side_effect=[poll_resp, download_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="kling-v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=img_path,
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        sent_image = mock_client.post.call_args.kwargs["json"]["image"]
        assert sent_image.startswith("data:image/png;base64,")
        assert "fake" not in sent_image  # 必须编码过
```

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/test_newapi_video_backend.py::TestNewAPIVideoBackend::test_image_to_video_encodes_base64 -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_newapi_video_backend.py
git commit -m "test(newapi-video): cover image-to-video base64 encoding"
```

---

## Task 5: 追加失败状态与轮询中间态测试

**Files:**
- Modify: `tests/test_newapi_video_backend.py`

- [ ] **Step 1: 追加失败态 + 轮询 in_progress 两轮成功的测试**

```python
    async def test_failed_status_raises(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t2", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "t2",
                "status": "failed",
                "error": {"code": 500, "message": "upstream down"},
            },
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(RuntimeError, match="upstream down"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        resolution="720p",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )

    async def test_polls_through_in_progress(self, tmp_path: Path):
        """多轮 in_progress 后再 completed，最终成功返回。"""
        create_resp = _make_response(200, {"task_id": "t3", "status": "queued"})
        in_progress = _make_response(200, {"task_id": "t3", "status": "in_progress"})
        completed = _make_response(
            200,
            {
                "task_id": "t3",
                "status": "completed",
                "url": "https://cdn/v.mp4",
                "metadata": {"duration": 5},
            },
        )
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"v"
        download_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(side_effect=[in_progress, in_progress, completed, download_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        assert result.task_id == "t3"
        # 3 次 poll + 1 次 download = 4 次 GET
        assert mock_client.get.call_count == 4
```

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/test_newapi_video_backend.py -v`
Expected: 所有测试 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_newapi_video_backend.py
git commit -m "test(newapi-video): cover failed status and polling progression"
```

---

## Task 6: 注册 NewAPIVideoBackend 到视频后端注册表

**Files:**
- Modify: `lib/video_backends/__init__.py`

- [ ] **Step 1: 在文件末尾追加注册**

在 `lib/video_backends/__init__.py` 最后（`register_backend(PROVIDER_OPENAI, OpenAIVideoBackend)` 之下）追加：

```python
# NewAPI 统一视频端点
from lib.providers import PROVIDER_NEWAPI
from lib.video_backends.newapi import NewAPIVideoBackend

register_backend(PROVIDER_NEWAPI, NewAPIVideoBackend)
```

- [ ] **Step 2: 更新 `__all__`**

把 `PROVIDER_NEWAPI` 加入 `__all__` 列表（保持字母顺序即可）。在现有 `"PROVIDER_OPENAI",` 之后添加：

```python
    "PROVIDER_NEWAPI",
```

同时更新顶部 `from lib.providers import ...` 合并导入（避免末尾再次导入）。最终顶部 import 如下：

```python
from lib.providers import PROVIDER_ARK, PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_NEWAPI, PROVIDER_OPENAI
```

并删除末尾的 `from lib.providers import PROVIDER_NEWAPI`。

- [ ] **Step 3: 验证注册**

Run:
```bash
uv run python -c "from lib.video_backends import get_registered_backends; print(get_registered_backends())"
```
Expected: 输出列表包含 `'newapi'`。

- [ ] **Step 4: 运行 registry 测试**

Run: `uv run pytest tests/test_video_backend_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/video_backends/__init__.py
git commit -m "feat(video-backends): register NewAPIVideoBackend"
```

---

## Task 7: 扩展 custom_provider factory 的 newapi 分支（先写测试）

**Files:**
- Modify: `tests/test_custom_provider_factory.py`

- [ ] **Step 1: 追加 NewAPI 格式测试类**

在 `tests/test_custom_provider_factory.py` 的 `TestErrors` 之前新增：

```python
# ---------------------------------------------------------------------------
# NewAPI format
# ---------------------------------------------------------------------------


class TestNewAPIFormat:
    @patch("lib.custom_provider.factory.OpenAITextBackend")
    def test_text_backend_uses_openai_delegate(self, mock_cls):
        provider = _make_provider(api_format="newapi", base_url="https://newapi.example.com")
        result = create_custom_backend(provider=provider, model_id="gpt-oss", media_type="text")

        assert isinstance(result, CustomTextBackend)
        assert result.model == "gpt-oss"
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://newapi.example.com/v1",
            model="gpt-oss",
        )

    @patch("lib.custom_provider.factory.OpenAIImageBackend")
    def test_image_backend_uses_openai_delegate(self, mock_cls):
        provider = _make_provider(api_format="newapi", base_url="https://newapi.example.com/v1")
        result = create_custom_backend(provider=provider, model_id="dall-e-3", media_type="image")

        assert isinstance(result, CustomImageBackend)
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://newapi.example.com/v1",
            model="dall-e-3",
        )

    @patch("lib.custom_provider.factory.NewAPIVideoBackend")
    def test_video_backend_uses_newapi_delegate(self, mock_cls):
        provider = _make_provider(api_format="newapi", base_url="https://newapi.example.com/v1")
        result = create_custom_backend(provider=provider, model_id="kling-v1", media_type="video")

        assert isinstance(result, CustomVideoBackend)
        assert result.model == "kling-v1"
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://newapi.example.com/v1",
            model="kling-v1",
        )
```

同时修改 `TestErrors.test_unknown_api_format`，把 `anthropic` 替换成 `"something-else"` 以免误判，保留原断言即可（代码已是 `anthropic`，无需改）。

- [ ] **Step 2: 运行测试（应失败）**

Run: `uv run pytest tests/test_custom_provider_factory.py::TestNewAPIFormat -v`
Expected: FAIL —
- `test_video_backend_uses_newapi_delegate` 会因 `factory.NewAPIVideoBackend` 不存在 AttributeError
- 另两个会因 `factory._VALID_API_FORMATS` 不含 `newapi` 抛 ValueError

---

## Task 8: 实现 factory newapi 分支

**Files:**
- Modify: `lib/custom_provider/factory.py`

- [ ] **Step 1: 更新文件**

在顶部 import 部分追加：

```python
from lib.video_backends.newapi import NewAPIVideoBackend
```

将 `_VALID_API_FORMATS` 改为：

```python
_VALID_API_FORMATS = {"openai", "google", "newapi"}
```

在 `create_custom_backend` 的分支 dispatch 末尾追加 newapi 分支：

```python
    if api_format == "openai":
        return _create_openai_backend(provider=provider, model_id=model_id, media_type=media_type)
    elif api_format == "google":
        return _create_google_backend(provider=provider, model_id=model_id, media_type=media_type)
    else:  # newapi
        return _create_newapi_backend(provider=provider, model_id=model_id, media_type=media_type)
```

在文件末尾追加新函数：

```python
def _create_newapi_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    media_type: str,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend:
    """创建 NewAPI 格式的后端：文本/图片复用 OpenAI delegate，视频走 NewAPIVideoBackend。"""
    pid = provider.provider_id
    base_url = ensure_openai_base_url(provider.base_url)
    if media_type == "text":
        delegate = OpenAITextBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
        return CustomTextBackend(provider_id=pid, delegate=delegate, model=model_id)
    elif media_type == "image":
        delegate = OpenAIImageBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
        return CustomImageBackend(provider_id=pid, delegate=delegate, model=model_id)
    else:  # video
        delegate = NewAPIVideoBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
        return CustomVideoBackend(provider_id=pid, delegate=delegate, model=model_id)
```

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/test_custom_provider_factory.py -v`
Expected: 所有测试 PASS（含原有 openai/google/errors 与新加 NewAPIFormat 三个）

- [ ] **Step 3: Commit**

```bash
git add lib/custom_provider/factory.py tests/test_custom_provider_factory.py
git commit -m "feat(custom-provider): support newapi api_format in factory"
```

---

## Task 9: 扩展模型发现 discovery 支持 newapi

**Files:**
- Modify: `tests/test_model_discovery.py`
- Modify: `lib/custom_provider/discovery.py`

- [ ] **Step 1: 查看现有 discovery 测试结构**

Run: `uv run pytest tests/test_model_discovery.py --collect-only -q`
Expected: 输出已有测试类/方法列表。

- [ ] **Step 2: 追加 newapi 测试**

在 `tests/test_model_discovery.py` 末尾追加：

```python
class TestDiscoverNewAPI:
    async def test_newapi_reuses_openai_path(self):
        """newapi 格式应走 OpenAI 兼容的 /v1/models 路径。"""
        from unittest.mock import AsyncMock, patch

        fake_models = [
            type("M", (), {"id": "gpt-4o"}),
            type("M", (), {"id": "kling-v1"}),
        ]

        with patch("lib.custom_provider.discovery.OpenAI") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.models.list.return_value = fake_models

            from lib.custom_provider.discovery import discover_models

            result = await discover_models(
                api_format="newapi", base_url="https://x/v1", api_key="sk"
            )

        ids = [m["model_id"] for m in result]
        assert "gpt-4o" in ids
        assert "kling-v1" in ids
        kling = next(m for m in result if m["model_id"] == "kling-v1")
        assert kling["media_type"] == "video"
```

- [ ] **Step 3: 运行测试（应失败）**

Run: `uv run pytest tests/test_model_discovery.py::TestDiscoverNewAPI -v`
Expected: FAIL — `ValueError: 不支持的 api_format: 'newapi'`

- [ ] **Step 4: 修改 discovery 支持 newapi**

在 `lib/custom_provider/discovery.py` 的 `discover_models` 中扩展分支：

```python
    if api_format == "openai" or api_format == "newapi":
        return await _discover_openai(base_url, api_key)
    elif api_format == "google":
        return await _discover_google(base_url, api_key)
    else:
        raise ValueError(f"不支持的 api_format: {api_format!r}，支持: 'openai', 'google', 'newapi'")
```

同时更新 `_VIDEO_PATTERN` 增加关键词以覆盖 NewAPI 聚合的更多厂商：

```python
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|minimax|hailuo|seedream|jimeng|runway",
    re.IGNORECASE,
)
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/test_model_discovery.py -v`
Expected: 所有测试 PASS

- [ ] **Step 6: Commit**

```bash
git add lib/custom_provider/discovery.py tests/test_model_discovery.py
git commit -m "feat(custom-provider): support newapi in model discovery"
```

---

## Task 10: 连接测试 endpoint 支持 newapi

**Files:**
- Modify: `server/routers/custom_providers.py`

- [ ] **Step 1: 更新 `_run_connection_test` 分支**

在 `server/routers/custom_providers.py:486-500` 把 `elif api_format == "google":` 分支之后、`else:` 兜底之前插入 newapi 分支：

```python
        elif api_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_google, base_url, api_key, _t),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        elif api_format == "newapi":
            # NewAPI 的 /v1/models 是 OpenAI 兼容
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_openai, base_url, api_key, _t),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        else:
```

- [ ] **Step 2: 更新 CreateProviderRequest 字段注释**

将 `server/routers/custom_providers.py:75` 的注释：

```python
    api_format: str  # "openai" or "google"
```

改为：

```python
    api_format: str  # "openai" | "google" | "newapi"
```

- [ ] **Step 3: 运行 API 测试**

Run: `uv run pytest tests/test_custom_providers_api.py -v`
Expected: 原有测试保持 PASS（未新增断言）。

- [ ] **Step 4: Commit**

```bash
git add server/routers/custom_providers.py
git commit -m "feat(custom-providers): accept newapi in connection test endpoint"
```

---

## Task 11: 数据库模型注释更新

**Files:**
- Modify: `lib/db/models/custom_provider.py:18`

- [ ] **Step 1: 更新字段注释**

将 `lib/db/models/custom_provider.py:18`：

```python
    api_format: Mapped[str] = mapped_column(String(32), nullable=False)  # "openai" | "google"
```

改为：

```python
    api_format: Mapped[str] = mapped_column(String(32), nullable=False)  # "openai" | "google" | "newapi"
```

- [ ] **Step 2: 运行 repo/model 测试**

Run: `uv run pytest tests/test_custom_provider_models.py tests/test_custom_provider_repo.py -v`
Expected: PASS（无功能变化）

- [ ] **Step 3: Commit**

```bash
git add lib/db/models/custom_provider.py
git commit -m "chore(custom-provider): document newapi in api_format comment"
```

---

## Task 12: 前端类型联合扩展

**Files:**
- Modify: `frontend/src/types/custom-provider.ts`

- [ ] **Step 1: 把所有 `"openai" | "google"` 替换为三值联合**

编辑 `frontend/src/types/custom-provider.ts`，把所有 `api_format: "openai" | "google"` 改为 `api_format: "openai" | "google" | "newapi"`。共 2 处：

```typescript
export interface CustomProviderInfo {
  ...
  api_format: "openai" | "google" | "newapi";
  ...
}

export interface CustomProviderCreateRequest {
  ...
  api_format: "openai" | "google" | "newapi";
  ...
}
```

- [ ] **Step 2: 验证 typecheck**

Run: `cd frontend && pnpm check`
Expected: 无 TypeScript 错误（会在下一任务引入 Form 修改时再一次验证）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/custom-provider.ts
git commit -m "chore(frontend): extend ApiFormat type with newapi"
```

---

## Task 13: 前端 Form 加 NewAPI 选项

**Files:**
- Modify: `frontend/src/components/pages/settings/CustomProviderForm.tsx:17-23`

- [ ] **Step 1: 更新 ApiFormat 类型与下拉选项**

把 `frontend/src/components/pages/settings/CustomProviderForm.tsx:17`：

```typescript
type ApiFormat = "openai" | "google";
```

改为：

```typescript
type ApiFormat = "openai" | "google" | "newapi";
```

把 `frontend/src/components/pages/settings/CustomProviderForm.tsx:20-23`：

```typescript
const API_FORMAT_OPTIONS: { value: ApiFormat; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
];
```

改为：

```typescript
const API_FORMAT_OPTIONS: { value: ApiFormat; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "newapi", label: "NewAPI" },
];
```

- [ ] **Step 2: 前端构建 + typecheck**

Run: `cd frontend && pnpm check`
Expected: PASS

- [ ] **Step 3: 可视检查**

启动后端 + 前端，打开 `/settings` → 自定义供应商 → 新建，确认协议下拉有 "NewAPI" 选项可选。保存 provider 后刷新页面，确认持久化。

> 若环境不便启动 dev server，跳过此步，在 PR 描述中注明未做 UI 手测。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/pages/settings/CustomProviderForm.tsx
git commit -m "feat(frontend): add NewAPI option to custom provider form"
```

---

## Task 14: 整体回归测试 + ruff

**Files:** 全部修改过的文件

- [ ] **Step 1: ruff check + format**

Run:
```bash
uv run ruff check lib/video_backends/newapi.py lib/custom_provider/factory.py lib/custom_provider/discovery.py server/routers/custom_providers.py lib/db/models/custom_provider.py lib/providers.py lib/video_backends/__init__.py tests/test_newapi_video_backend.py tests/test_custom_provider_factory.py tests/test_model_discovery.py \
  && uv run ruff format lib/video_backends/newapi.py lib/custom_provider/factory.py lib/custom_provider/discovery.py server/routers/custom_providers.py lib/db/models/custom_provider.py lib/providers.py lib/video_backends/__init__.py tests/test_newapi_video_backend.py tests/test_custom_provider_factory.py tests/test_model_discovery.py
```
Expected: 无 error。若 `format` 对已提交文件做了改动，运行 `git add -u && git commit -m "style: ruff format"`。

- [ ] **Step 2: 后端全量测试**

Run: `uv run pytest -q`
Expected: 全部 PASS，覆盖率不低于之前水平（`--cov`）

- [ ] **Step 3: 前端全量检查**

Run: `cd frontend && pnpm check && pnpm build`
Expected: 两者均 PASS

- [ ] **Step 4: 最终 commit（仅限有格式化残余时）**

```bash
git status
# 若有 ruff format 后的残余改动：
git add -u
git commit -m "style: ruff format newapi-related files"
```

---

## 完成验收

- [ ] `uv run pytest -q` 全绿
- [ ] `cd frontend && pnpm check` 全绿
- [ ] 创建一个 `api_format=newapi` 的自定义供应商，文本/图片/视频三种模型至少各 smoke test 一次（可在单独 follow-up 手测阶段完成，不在此 plan 内）
