# GrokVideoBackend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate xAI Grok's grok-imagine-video as an alternative video generation backend

**Architecture:** Add `GrokVideoBackend` implementing the `VideoBackend` protocol, calling the Grok API via `xai_sdk.AsyncClient`. Resolution changes to model-level sub-configuration (`video_model_settings.{model}.resolution`). Reuse existing registration, billing, and configuration management patterns.

**Tech Stack:** xai_sdk (Python SDK), httpx (video download), pytest (testing)

---

## File Structure

| File | Responsibility |
|------|------|
| `lib/video_backends/grok.py` | **Add** — GrokVideoBackend implementation |
| `lib/video_backends/base.py` | Add `PROVIDER_GROK` constant |
| `lib/video_backends/__init__.py` | Register Grok backend + export constants |
| `lib/cost_calculator.py` | Add Grok billing rules |
| `lib/db/repositories/usage_repo.py` | `finish_call()` add Grok branch |
| `lib/system_config.py` | `_ENV_KEYS` + `_apply_to_env` add XAI_API_KEY |
| `server/services/generation_tasks.py` | factory method adds Grok branch + resolution injection |
| `pyproject.toml` | Add `xai-sdk` dependency |
| `tests/test_grok_video_backend.py` | **Add** — Grok backend unit tests |
| `tests/test_cost_calculator.py` | Add Grok billing test cases |

---

### Task 1: Add xai-sdk Dependency

**Files:**
- Modify: `pyproject.toml:7-29` (dependencies list)

- [ ] **Step 1: Add xai-sdk to pyproject.toml**

At the end of the `dependencies` list in `pyproject.toml`, add:

```toml
    "xai-sdk>=0.1.0",
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: xai-sdk and its dependencies installed successfully

- [ ] **Step 3: Verify importable**

Run: `uv run python -c "import xai_sdk; print(xai_sdk.__version__)"`
Expected: prints version number, no ImportError

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add xai-sdk dependency"
```

---

### Task 2: Add PROVIDER_GROK Constant and Register

**Files:**
- Modify: `lib/video_backends/base.py:7` (constants section)
- Modify: `lib/video_backends/__init__.py`

- [ ] **Step 1: Add constant to base.py**

Below `PROVIDER_SEEDANCE = "seedance"` in `lib/video_backends/base.py`, add:

```python
PROVIDER_GROK = "grok"
```

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "from lib.video_backends.base import PROVIDER_GROK; print(PROVIDER_GROK)"`
Expected: `grok`

- [ ] **Step 3: Commit**

```bash
git add lib/video_backends/base.py
git commit -m "feat: add PROVIDER_GROK constant"
```

---

### Task 3: Implement GrokVideoBackend — Tests First

**Files:**
- Create: `tests/test_grok_video_backend.py`
- Create: `lib/video_backends/grok.py`

- [ ] **Step 1: Write failing tests — text-to-video**

Create `tests/test_grok_video_backend.py`:

```python
"""GrokVideoBackend unit tests."""

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

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_grok_video_backend.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lib.video_backends.grok'`)

- [ ] **Step 3: Implement GrokVideoBackend**

Create `lib/video_backends/grok.py`:

```python
"""GrokVideoBackend — xAI Grok video generation backend."""

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

# Image extension → MIME type mapping
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class GrokVideoBackend:
    """xAI Grok video generation backend."""

    DEFAULT_MODEL = "grok-imagine-video"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        if not api_key:
            raise ValueError(
                "XAI_API_KEY is not set\n"
                "Please configure your xAI API Key in the system settings page"
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
        """Generate video."""
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
        logger.info("Grok video generation started: model=%s, duration=%ds", self._model, request.duration_seconds)
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

        logger.info("Grok video download complete: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_grok_video_backend.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/video_backends/grok.py tests/test_grok_video_backend.py
git commit -m "feat: implement GrokVideoBackend (text-to-video + image-to-video)"
```

---

### Task 4: Register Grok Backend in __init__.py

**Files:**
- Modify: `lib/video_backends/__init__.py`

- [ ] **Step 1: Add registration code and exports**

In `lib/video_backends/__init__.py`:

1. Add `PROVIDER_GROK` to the imports section:

```python
from lib.video_backends.base import (
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_SEEDANCE,
    ...
)
```

2. Add `"PROVIDER_GROK"` to the `__all__` list

3. At the end of the file (after Seedance registration), add:

```python
# Grok: xai-sdk
from lib.video_backends.grok import GrokVideoBackend
register_backend(PROVIDER_GROK, GrokVideoBackend)
```

- [ ] **Step 2: Verify registration succeeded**

Run: `uv run python -c "from lib.video_backends import get_registered_backends; print(get_registered_backends())"`
Expected: output contains `grok`

- [ ] **Step 3: Commit**

```bash
git add lib/video_backends/__init__.py
git commit -m "feat: register GrokVideoBackend in the backend system"
```

---

### Task 5: Add Grok Billing Rules — Tests First

**Files:**
- Modify: `tests/test_cost_calculator.py`
- Modify: `lib/cost_calculator.py`

- [ ] **Step 1: Write failing tests**

Add a new test class at the end of `tests/test_cost_calculator.py`:

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

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_cost_calculator.py::TestGrokCost -v`
Expected: FAIL (`AttributeError: 'CostCalculator' object has no attribute 'calculate_grok_video_cost'`)

- [ ] **Step 3: Implement Grok billing**

In the `CostCalculator` class in `lib/cost_calculator.py`:

1. Add the billing dict after `DEFAULT_SEEDANCE_MODEL`:

```python
    # Grok video cost (USD per second), resolution-independent
    # Note: reference value, verify against official xAI pricing
    GROK_VIDEO_COST = {
        "grok-imagine-video": 0.050,
    }

    DEFAULT_GROK_MODEL = "grok-imagine-video"
```

2. After the `calculate_seedance_video_cost` method, add:

```python
    def calculate_grok_video_cost(
        self,
        duration_seconds: int,
        model: str | None = None,
    ) -> float:
        """
        Calculate Grok video generation cost.

        Args:
            duration_seconds: Video duration in seconds
            model: Model name

        Returns:
            Cost (USD)
        """
        model = model or self.DEFAULT_GROK_MODEL
        per_second = self.GROK_VIDEO_COST.get(
            model, self.GROK_VIDEO_COST[self.DEFAULT_GROK_MODEL]
        )
        return duration_seconds * per_second
```

- [ ] **Step 4: Run all billing tests to confirm they pass**

Run: `uv run python -m pytest tests/test_cost_calculator.py -v`
Expected: all PASS (including the new TestGrokCost)

- [ ] **Step 5: Commit**

```bash
git add lib/cost_calculator.py tests/test_cost_calculator.py
git commit -m "feat: add Grok video per-second billing rules"
```

---

### Task 6: UsageRepository — Add Grok Billing Branch

**Files:**
- Modify: `lib/db/repositories/usage_repo.py:9-10` (imports section)
- Modify: `lib/db/repositories/usage_repo.py:98-115` (cost calculation in `finish_call`)

- [ ] **Step 1: Add PROVIDER_GROK import**

Change:

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_SEEDANCE
```

to:

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_SEEDANCE
```

- [ ] **Step 2: Add Grok billing branch**

In the cost calculation block in `finish_call()`, after the Seedance branch (`if effective_provider == PROVIDER_SEEDANCE and row.call_type == "video":`), and before the `elif row.call_type == "image":` branch, add:

```python
            elif effective_provider == PROVIDER_GROK and row.call_type == "video":
                cost_amount = cost_calculator.calculate_grok_video_cost(
                    duration_seconds=row.duration_seconds or 5,
                    model=row.model,
                )
                currency = "USD"
```

The complete if-elif chain becomes:

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

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from lib.db.repositories.usage_repo import UsageRepository; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add lib/db/repositories/usage_repo.py
git commit -m "feat: UsageRepository adds Grok video billing branch"
```

---

### Task 7: SystemConfigManager — Add XAI_API_KEY Support

**Files:**
- Modify: `lib/system_config.py:157-181` (`_ENV_KEYS`)
- Modify: `lib/system_config.py:350-457` (`_apply_to_env`)

- [ ] **Step 1: Add to _ENV_KEYS tuple**

After `"FILE_SERVICE_BASE_URL",`, add:

```python
        "XAI_API_KEY",
```

- [ ] **Step 2: Add mapping in _apply_to_env**

After the `# File service base URL` block and before the `# Rate limiting / performance` block, add:

```python
        # xAI API key (Grok)
        if "xai_api_key" in overrides:
            self._set_env("XAI_API_KEY", overrides.get("xai_api_key"))
        else:
            self._restore_or_unset("XAI_API_KEY")
```

- [ ] **Step 3: Verify configuration takes effect**

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
git commit -m "feat: SystemConfigManager supports XAI_API_KEY configuration"
```

---

### Task 8: Factory Method — Add Grok Branch + Resolution Injection

**Files:**
- Modify: `server/services/generation_tasks.py:30` (imports)
- Modify: `server/services/generation_tasks.py:45-67` (`_get_or_create_video_backend`)
- Modify: `server/services/generation_tasks.py:390-422` (`execute_video_task`)

- [ ] **Step 1: Update imports**

Change:

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_SEEDANCE
```

to:

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_SEEDANCE
```

- [ ] **Step 2: Add Grok factory branch**

In `_get_or_create_video_backend()`, after the Seedance branch, add:

```python
    elif provider_name == PROVIDER_GROK:
        kwargs["api_key"] = os.environ.get("XAI_API_KEY")
        kwargs["model"] = provider_settings.get("model")
```

- [ ] **Step 3: Inject model-level resolution in execute_video_task()**

In `execute_video_task()`, before calling `generator.generate_video_async()`, read the resolution from `video_model_settings`:

```python
    # Model-level resolution: read from video_model_settings.{model}.resolution
    # Defaults: Gemini 1080p, Seedance 720p, Grok 720p
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

Then pass `resolution=resolution` in the `generate_video_async()` call:

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

- [ ] **Step 4: Verify import works**

Run: `uv run python -c "from server.services.generation_tasks import _get_or_create_video_backend; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add server/services/generation_tasks.py
git commit -m "feat: video backend factory supports Grok provider + model-level resolution injection"
```

---

### Task 9: Run Full Test Suite

**Files:** No changes

- [ ] **Step 1: Run all tests**

Run: `uv run python -m pytest -v`
Expected: all PASS, no regressions

- [ ] **Step 2: If any failures, fix and re-run**

- [ ] **Step 3: Final commit (if fixes were made)**

```bash
git add -A
git commit -m "fix: fix test regressions"
```
