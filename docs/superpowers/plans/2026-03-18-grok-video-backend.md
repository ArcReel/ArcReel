# GrokVideoBackend 接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 xAI Grok 的 grok-imagine-video 作为视频生成备选后端

**Architecture:** 新增 `GrokVideoBackend` 实现 `VideoBackend` 协议，通过 `xai_sdk.AsyncClient` 调用 Grok API。分辨率改为模型级子配置（`video_model_settings.{model}.resolution`）。沿用现有注册、计费、配置管理模式。

**Tech Stack:** xai_sdk（Python SDK）、httpx（视频下载）、pytest（测试）

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lib/video_backends/grok.py` | **新增** — GrokVideoBackend 实现 |
| `lib/video_backends/base.py` | 新增 `PROVIDER_GROK` 常量 |
| `lib/video_backends/__init__.py` | 注册 Grok 后端 + 导出常量 |
| `lib/cost_calculator.py` | 新增 Grok 计费规则 |
| `lib/db/repositories/usage_repo.py` | `finish_call()` 新增 Grok 分支 |
| `lib/system_config.py` | `_ENV_KEYS` + `_apply_to_env` 新增 XAI_API_KEY |
| `server/services/generation_tasks.py` | 工厂方法新增 Grok 分支 + 分辨率注入 |
| `pyproject.toml` | 新增 `xai-sdk` 依赖 |
| `tests/test_grok_video_backend.py` | **新增** — Grok 后端单元测试 |
| `tests/test_cost_calculator.py` | 新增 Grok 计费用例 |

---

### Task 1: 添加 xai-sdk 依赖

**Files:**
- Modify: `pyproject.toml:7-29` (dependencies 列表)

- [ ] **Step 1: 添加 xai-sdk 到 pyproject.toml**

在 `pyproject.toml` 的 `dependencies` 列表末尾添加：

```toml
    "xai-sdk>=0.1.0",
```

- [ ] **Step 2: 安装依赖**

Run: `uv sync`
Expected: 成功安装 xai-sdk 及其依赖

- [ ] **Step 3: 验证可导入**

Run: `uv run python -c "import xai_sdk; print(xai_sdk.__version__)"`
Expected: 打印版本号，无 ImportError

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: 添加 xai-sdk 依赖"
```

---

### Task 2: 新增 PROVIDER_GROK 常量并注册

**Files:**
- Modify: `lib/video_backends/base.py:7` (常量区)
- Modify: `lib/video_backends/__init__.py`

- [ ] **Step 1: 在 base.py 新增常量**

在 `lib/video_backends/base.py` 的 `PROVIDER_SEEDANCE = "seedance"` 下方添加：

```python
PROVIDER_GROK = "grok"
```

- [ ] **Step 2: 验证导入无误**

Run: `uv run python -c "from lib.video_backends.base import PROVIDER_GROK; print(PROVIDER_GROK)"`
Expected: `grok`

- [ ] **Step 3: Commit**

```bash
git add lib/video_backends/base.py
git commit -m "feat: 新增 PROVIDER_GROK 常量"
```

---

### Task 3: 实现 GrokVideoBackend — 测试先行

**Files:**
- Create: `tests/test_grok_video_backend.py`
- Create: `lib/video_backends/grok.py`

- [ ] **Step 1: 编写失败测试 — text-to-video**

创建 `tests/test_grok_video_backend.py`：

```python
"""GrokVideoBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.video_backends.base import (
    PROVIDER_GROK,
    VideoCapability,
    VideoGenerationRequest,
)


@pytest.fixture
def output_path(tmp_path: Path) -> Path:
    return tmp_path / "output.mp4"


class TestGrokVideoBackend:
    @patch("lib.video_backends.grok.xai_sdk")
    def test_name_and_model(self, mock_sdk):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert backend.name == PROVIDER_GROK
        assert backend.model == "grok-imagine-video"

    @patch("lib.video_backends.grok.xai_sdk")
    def test_capabilities(self, mock_sdk):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert VideoCapability.GENERATE_AUDIO not in backend.capabilities
        assert VideoCapability.NEGATIVE_PROMPT not in backend.capabilities
        assert VideoCapability.SEED_CONTROL not in backend.capabilities

    @patch("lib.video_backends.grok.xai_sdk")
    def test_custom_model(self, mock_sdk):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-2")
        assert backend.model == "grok-imagine-video-2"

    def test_missing_api_key_raises(self):
        from lib.video_backends.grok import GrokVideoBackend

        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokVideoBackend(api_key=None)

    async def test_text_to_video(self, output_path: Path):
        from lib.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.video_backends.grok.xai_sdk") as mock_sdk:
            mock_sdk.AsyncClient.return_value = mock_client

            backend = GrokVideoBackend(api_key="test-key")

            # Mock httpx download
            mock_http_response = AsyncMock()
            mock_http_response.raise_for_status = MagicMock()
            mock_http_response.aiter_bytes = lambda chunk_size=None: _async_iter([b"fake-video-data"])

            mock_http_client = AsyncMock()
            mock_http_client.stream = _async_context_manager(mock_http_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)

            with patch("lib.video_backends.grok.httpx.AsyncClient", return_value=mock_http_client):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=output_path,
                    aspect_ratio="16:9",
                    duration_seconds=5,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.provider == PROVIDER_GROK
            assert result.model == "grok-imagine-video"
            assert result.duration_seconds == 5
            assert result.video_path == output_path

            # Verify SDK was called with correct params
            mock_video.generate.assert_awaited_once()
            call_kwargs = mock_video.generate.call_args[1]
            assert call_kwargs["prompt"] == "A cat walking"
            assert call_kwargs["model"] == "grok-imagine-video"
            assert call_kwargs["duration"] == 5
            assert call_kwargs["aspect_ratio"] == "16:9"
            assert call_kwargs["resolution"] == "720p"
            assert "image_url" not in call_kwargs

    async def test_image_to_video(self, output_path: Path, tmp_path: Path):
        from lib.video_backends.grok import GrokVideoBackend

        # Create a fake image file
        image_path = tmp_path / "start.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 8

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.video_backends.grok.xai_sdk") as mock_sdk:
            mock_sdk.AsyncClient.return_value = mock_client

            backend = GrokVideoBackend(api_key="test-key")

            mock_http_response = AsyncMock()
            mock_http_response.raise_for_status = MagicMock()
            mock_http_response.aiter_bytes = lambda chunk_size=None: _async_iter([b"fake-video-data"])

            mock_http_client = AsyncMock()
            mock_http_client.stream = _async_context_manager(mock_http_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)

            with patch("lib.video_backends.grok.httpx.AsyncClient", return_value=mock_http_client):
                request = VideoGenerationRequest(
                    prompt="Animate this scene",
                    output_path=output_path,
                    start_image=image_path,
                    duration_seconds=8,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.duration_seconds == 8

            # Verify image_url was passed as base64
            call_kwargs = mock_video.generate.call_args[1]
            assert "image_url" in call_kwargs
            assert call_kwargs["image_url"].startswith("data:image/png;base64,")


# --- Test helpers ---

async def _async_iter(items):
    for item in items:
        yield item


def _async_context_manager(mock_response):
    """Create an async context manager that yields mock_response for httpx.stream."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _stream(*args, **kwargs):
        yield mock_response

    return _stream
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_grok_video_backend.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'lib.video_backends.grok'`）

- [ ] **Step 3: 实现 GrokVideoBackend**

创建 `lib/video_backends/grok.py`：

```python
"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import base64
import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional, Set

import httpx
import xai_sdk

from lib.video_backends.base import (
    PROVIDER_GROK,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

# 图片后缀 → MIME 类型映射
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class GrokVideoBackend:
    """xAI Grok 视频生成后端。"""

    DEFAULT_MODEL = "grok-imagine-video"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        if not api_key:
            raise ValueError(
                "XAI_API_KEY 未设置\n"
                "请在系统配置页中配置 xAI API Key"
            )

        self._client = xai_sdk.AsyncClient(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: Set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> Set[VideoCapability]:
        return self._capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。"""
        # 1. Build SDK params
        generate_kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "timeout": timedelta(minutes=15),
            "interval": timedelta(seconds=5),
        }

        # 2. Image-to-video: base64 encode the start image
        if request.start_image and Path(request.start_image).exists():
            image_path = Path(request.start_image)
            suffix = image_path.suffix.lower()
            mime_type = _MIME_TYPES.get(suffix, "image/png")
            image_data = image_path.read_bytes()
            b64 = base64.b64encode(image_data).decode("ascii")
            generate_kwargs["image_url"] = f"data:{mime_type};base64,{b64}"

        # 3. Call SDK (handles polling automatically)
        logger.info("Grok 视频生成开始: model=%s, duration=%ds", self._model, request.duration_seconds)
        response = await self._client.video.generate(**generate_kwargs)

        # 4. Download video to output_path
        video_url = response.url
        actual_duration = getattr(response, "duration", request.duration_seconds)

        request.output_path.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient() as http_client:
            async with http_client.stream("GET", video_url, timeout=120) as resp:
                resp.raise_for_status()
                with open(request.output_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        logger.info("Grok 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_grok_video_backend.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add lib/video_backends/grok.py tests/test_grok_video_backend.py
git commit -m "feat: 实现 GrokVideoBackend (text-to-video + image-to-video)"
```

---

### Task 4: 注册 Grok 后端到 __init__.py

**Files:**
- Modify: `lib/video_backends/__init__.py`

- [ ] **Step 1: 添加注册代码和导出**

在 `lib/video_backends/__init__.py` 中：

1. 在导入区新增 `PROVIDER_GROK`：

```python
from lib.video_backends.base import (
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_SEEDANCE,
    ...
)
```

2. 在 `__all__` 列表中新增 `"PROVIDER_GROK"`

3. 在文件末尾（Seedance 注册之后）添加：

```python
# Grok: xai-sdk
from lib.video_backends.grok import GrokVideoBackend
register_backend(PROVIDER_GROK, GrokVideoBackend)
```

- [ ] **Step 2: 验证注册成功**

Run: `uv run python -c "from lib.video_backends import get_registered_backends; print(get_registered_backends())"`
Expected: 输出包含 `grok`

- [ ] **Step 3: Commit**

```bash
git add lib/video_backends/__init__.py
git commit -m "feat: 注册 GrokVideoBackend 到后端系统"
```

---

### Task 5: 新增 Grok 计费规则 — 测试先行

**Files:**
- Modify: `tests/test_cost_calculator.py`
- Modify: `lib/cost_calculator.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_cost_calculator.py` 末尾添加新测试类：

```python
class TestGrokCost:
    def test_default_model_per_second(self):
        calculator = CostCalculator()
        cost = calculator.calculate_grok_video_cost(
            duration_seconds=10,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.50)

    def test_short_video(self):
        calculator = CostCalculator()
        cost = calculator.calculate_grok_video_cost(
            duration_seconds=1,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.050)

    def test_max_duration(self):
        calculator = CostCalculator()
        cost = calculator.calculate_grok_video_cost(
            duration_seconds=15,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.75)

    def test_zero_duration(self):
        calculator = CostCalculator()
        cost = calculator.calculate_grok_video_cost(
            duration_seconds=0,
            model="grok-imagine-video",
        )
        assert cost == pytest.approx(0.0)

    def test_unknown_model_uses_default(self):
        calculator = CostCalculator()
        cost = calculator.calculate_grok_video_cost(
            duration_seconds=10,
            model="unknown-grok-model",
        )
        assert cost == pytest.approx(0.50)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_cost_calculator.py::TestGrokCost -v`
Expected: FAIL（`AttributeError: 'CostCalculator' object has no attribute 'calculate_grok_video_cost'`）

- [ ] **Step 3: 实现 Grok 计费**

在 `lib/cost_calculator.py` 的 `CostCalculator` 类中：

1. 在 `DEFAULT_SEEDANCE_MODEL` 之后添加计费字典：

```python
    # Grok 视频费用（美元/秒），不区分分辨率
    # 注意：此为参考值，需核实 xAI 官方定价
    GROK_VIDEO_COST = {
        "grok-imagine-video": 0.050,
    }

    DEFAULT_GROK_MODEL = "grok-imagine-video"
```

2. 在 `calculate_seedance_video_cost` 方法之后添加：

```python
    def calculate_grok_video_cost(
        self,
        duration_seconds: int,
        model: str | None = None,
    ) -> float:
        """
        计算 Grok 视频生成费用。

        Args:
            duration_seconds: 视频时长（秒）
            model: 模型名称

        Returns:
            费用（美元）
        """
        model = model or self.DEFAULT_GROK_MODEL
        per_second = self.GROK_VIDEO_COST.get(
            model, self.GROK_VIDEO_COST[self.DEFAULT_GROK_MODEL]
        )
        return duration_seconds * per_second
```

- [ ] **Step 4: 运行全部计费测试确认通过**

Run: `uv run python -m pytest tests/test_cost_calculator.py -v`
Expected: 全部 PASS（含新增的 TestGrokCost）

- [ ] **Step 5: Commit**

```bash
git add lib/cost_calculator.py tests/test_cost_calculator.py
git commit -m "feat: 新增 Grok 视频按秒计费规则"
```

---

### Task 6: UsageRepository 新增 Grok 计费分支

**Files:**
- Modify: `lib/db/repositories/usage_repo.py:9-10` (导入区)
- Modify: `lib/db/repositories/usage_repo.py:98-115` (`finish_call` 内的 cost 计算)

- [ ] **Step 1: 添加 PROVIDER_GROK 导入**

在 `usage_repo.py` 的导入行：

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_SEEDANCE
```

改为：

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_SEEDANCE
```

- [ ] **Step 2: 新增 Grok 计费分支**

在 `finish_call()` 的 cost 计算区块中，在 Seedance 分支（`if effective_provider == PROVIDER_SEEDANCE and row.call_type == "video":`）之后、`elif row.call_type == "image":` 分支之前，添加 Grok 分支：

```python
            elif effective_provider == PROVIDER_GROK and row.call_type == "video":
                cost_amount = cost_calculator.calculate_grok_video_cost(
                    duration_seconds=row.duration_seconds or 5,
                    model=row.model,
                )
                currency = "USD"
```

完整的 if-elif 链变为：

```python
        if status == "success":
            if effective_provider == PROVIDER_SEEDANCE and row.call_type == "video":
                cost_amount, currency = cost_calculator.calculate_seedance_video_cost(...)
            elif effective_provider == PROVIDER_GROK and row.call_type == "video":
                cost_amount = cost_calculator.calculate_grok_video_cost(
                    duration_seconds=row.duration_seconds or 5,
                    model=row.model,
                )
                currency = "USD"
            elif row.call_type == "image":
                cost_amount = cost_calculator.calculate_image_cost(...)
                currency = "USD"
            elif row.call_type == "video":
                cost_amount = cost_calculator.calculate_video_cost(...)
                currency = "USD"
```

- [ ] **Step 3: 验证导入无误**

Run: `uv run python -c "from lib.db.repositories.usage_repo import UsageRepository; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add lib/db/repositories/usage_repo.py
git commit -m "feat: UsageRepository 新增 Grok 视频计费分支"
```

---

### Task 7: SystemConfigManager 新增 XAI_API_KEY 支持

**Files:**
- Modify: `lib/system_config.py:157-181` (`_ENV_KEYS`)
- Modify: `lib/system_config.py:350-457` (`_apply_to_env`)

- [ ] **Step 1: 在 _ENV_KEYS 元组末尾添加**

在 `"FILE_SERVICE_BASE_URL",` 之后添加：

```python
        "XAI_API_KEY",
```

- [ ] **Step 2: 在 _apply_to_env 中添加映射**

在 `# File service base URL` 块之后、`# Rate limiting / performance` 块之前，添加：

```python
        # xAI API key (Grok)
        if "xai_api_key" in overrides:
            self._set_env("XAI_API_KEY", overrides.get("xai_api_key"))
        else:
            self._restore_or_unset("XAI_API_KEY")
```

- [ ] **Step 3: 验证配置生效**

Run: `uv run python -c "
from lib.system_config import SystemConfigManager
m = SystemConfigManager.__new__(SystemConfigManager)
m._ENV_KEYS  # check XAI_API_KEY is present
assert 'XAI_API_KEY' in m._ENV_KEYS
print('OK')
"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add lib/system_config.py
git commit -m "feat: SystemConfigManager 支持 XAI_API_KEY 配置"
```

---

### Task 8: 工厂方法新增 Grok 分支 + 分辨率注入

**Files:**
- Modify: `server/services/generation_tasks.py:30` (导入)
- Modify: `server/services/generation_tasks.py:45-67` (`_get_or_create_video_backend`)
- Modify: `server/services/generation_tasks.py:390-422` (`execute_video_task`)

- [ ] **Step 1: 更新导入**

将：

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_SEEDANCE
```

改为：

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_SEEDANCE
```

- [ ] **Step 2: 新增 Grok 工厂分支**

在 `_get_or_create_video_backend()` 中，Seedance 分支之后添加：

```python
    elif provider_name == PROVIDER_GROK:
        kwargs["api_key"] = os.environ.get("XAI_API_KEY")
        kwargs["model"] = provider_settings.get("model")
```

- [ ] **Step 3: 在 execute_video_task() 中注入模型级分辨率**

在 `execute_video_task()` 中，在调用 `generator.generate_video_async()` 之前，从 `video_model_settings` 读取分辨率：

```python
    # 模型级分辨率：从 video_model_settings.{model}.resolution 读取
    # 默认值：Gemini 1080p, Seedance 720p, Grok 720p
    _DEFAULT_RESOLUTION = {
        PROVIDER_GEMINI: "1080p",
        PROVIDER_SEEDANCE: "720p",
        PROVIDER_GROK: "720p",
    }
    provider_name = payload.get("video_provider") or project.get("video_provider") or os.environ.get("DEFAULT_VIDEO_PROVIDER", PROVIDER_GEMINI)
    provider_settings = payload.get("video_provider_settings", {})
    model_name = provider_settings.get("model") or (generator._video_backend.model if generator._video_backend else None)
    video_model_settings = project.get("video_model_settings", {})
    model_settings = video_model_settings.get(model_name, {}) if model_name else {}
    resolution = model_settings.get("resolution") or _DEFAULT_RESOLUTION.get(provider_name, "1080p")
```

然后在 `generate_video_async()` 调用中传入 `resolution=resolution`：

```python
    _, version, _, video_uri = await generator.generate_video_async(
        prompt=prompt_text,
        resource_type="videos",
        resource_id=resource_id,
        start_image=storyboard_file,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        resolution=resolution,
        seed=seed,
        service_tier=service_tier,
    )
```

- [ ] **Step 4: 验证导入无误**

Run: `uv run python -c "from server.services.generation_tasks import _get_or_create_video_backend; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add server/services/generation_tasks.py
git commit -m "feat: 视频后端工厂支持 Grok 供应商 + 模型级分辨率注入"
```

---

### Task 9: 运行全量测试

**Files:** 无修改

- [ ] **Step 1: 运行全部测试**

Run: `uv run python -m pytest -v`
Expected: 全部 PASS，无回归

- [ ] **Step 2: 如有失败，修复后重新运行**

- [ ] **Step 3: 最终 commit（如有修复）**

```bash
git add -A
git commit -m "fix: 修复测试回归"
```
