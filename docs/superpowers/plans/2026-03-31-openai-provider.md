# OpenAI Preset Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenAI as the fifth preset provider for ArcReel, supporting three media types: text (GPT-5.4), image (GPT Image 1.5), and video (Sora 2).

**Architecture:** 新增 `lib/openai_shared.py` 共享客户端工厂，三个独立 Backend（`OpenAITextBackend`、`OpenAIImageBackend`、`OpenAIVideoBackend`）各自实现对应 Protocol，通过现有 Registry 模式注册。费用计算、连接测试、工厂集成均为最小侵入式修改。

**Tech Stack:** OpenAI Python SDK 2.30.0, AsyncOpenAI, pytest

> **Note: ** Instructor fallback 不在本期范围内。本期仅实现原生 `response_format` 结构化输出，Instructor fallback 作为后续优化。

**Design Spec:** `docs/superpowers/specs/2026-03-31-openai-provider-design.md`

---

### Task 1: 常量与供应商注册

**Files:**
- Modify: `lib/providers.py`
- Modify: `lib/config/registry.py`

- [ ] **Step 1: 在 `lib/providers.py` 添加 OpenAI 常量**

At the end of the file现有常量旁添加：

```python
PROVIDER_OPENAI = "openai"
```

完整文件应为：

```python
"""供应商名称常量，image_backends / video_backends 共用。"""

from typing import Literal

PROVIDER_GEMINI = "gemini"
PROVIDER_ARK = "ark"
PROVIDER_GROK = "grok"
PROVIDER_OPENAI = "openai"

CallType = Literal["image", "video", "text"]
CALL_TYPE_IMAGE: CallType = "image"
CALL_TYPE_VIDEO: CallType = "video"
CALL_TYPE_TEXT: CallType = "text"
```

- [ ] **Step 2: 在 `lib/config/registry.py` 注册 OpenAI 供应商**

在 `PROVIDER_REGISTRY` 字典的 `"grok": ProviderMeta(...)` 条目之后，添加 `"openai"` 条目：

```python
    "openai": ProviderMeta(
        display_name="OpenAI",
        description="OpenAI 官方平台，支持 GPT-5.4 文本、GPT Image 图片和 Sora 视频生成。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        models={
            # --- text ---
            "gpt-5.4": ModelInfo(
                display_name="GPT-5.4",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
            ),
            "gpt-5.4-mini": ModelInfo(
                display_name="GPT-5.4 Mini",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "gpt-5.4-nano": ModelInfo(
                display_name="GPT-5.4 Nano",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
            ),
            # --- image ---
            "gpt-image-1.5": ModelInfo(
                display_name="GPT Image 1.5",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
                default=True,
            ),
            "gpt-image-1-mini": ModelInfo(
                display_name="GPT Image 1 Mini",
                media_type="image",
                capabilities=["text_to_image", "image_to_image"],
            ),
            # --- video ---
            "sora-2": ModelInfo(
                display_name="Sora 2",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
                default=True,
            ),
            "sora-2-pro": ModelInfo(
                display_name="Sora 2 Pro",
                media_type="video",
                capabilities=["text_to_video", "image_to_video"],
            ),
        },
    ),
```

- [ ] **Step 3: 验证注册正确**

Run: `uv run python -c "from lib.config.registry import PROVIDER_REGISTRY; p = PROVIDER_REGISTRY['openai']; print(p.display_name, p.media_types, len(p.models))"`

Expected: `OpenAI ['image', 'text', 'video'] 7`

- [ ] **Step 4: 提交**

```bash
git add lib/providers.py lib/config/registry.py
git commit -m "feat: 注册 OpenAI 为第五个预置供应商"
```

---

### Task 2: `openai_shared.py` 共享模块

**Files:**
- Create: `lib/openai_shared.py`

- [ ] **Step 1: 创建 `lib/openai_shared.py`**

```python
"""
OpenAI 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- OPENAI_RETRYABLE_ERRORS — 可重试错误类型
- create_openai_client — AsyncOpenAI 客户端工厂
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OPENAI_RETRYABLE_ERRORS: tuple[type[Exception], ...] = ()

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    OPENAI_RETRYABLE_ERRORS = (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
except ImportError:
    pass


def create_openai_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AsyncOpenAI:
    """创建 AsyncOpenAI 客户端，统一处理 api_key 和 base_url。"""
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)
```

- [ ] **Step 2: 验证导入**

Run: `uv run python -c "from lib.openai_shared import create_openai_client, OPENAI_RETRYABLE_ERRORS; print('OK', len(OPENAI_RETRYABLE_ERRORS))"`

Expected: `OK 4`

- [ ] **Step 3: 提交**

```bash
git add lib/openai_shared.py
git commit -m "feat: 添加 openai_shared.py 共享客户端工厂"
```

---

### Task 3: OpenAI Text Backend

**Files:**
- Create: `lib/text_backends/openai.py`
- Create: `tests/test_openai_text_backend.py`
- Modify: `lib/text_backends/__init__.py`
- Modify: `lib/text_backends/factory.py`

- [ ] **Step 1: 编写测试 `tests/test_openai_text_backend.py`**

```python
"""OpenAITextBackend 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.providers import PROVIDER_OPENAI
from lib.text_backends.base import (
    ImageInput,
    TextCapability,
    TextGenerationRequest,
)


def _make_mock_response(content="Hello", input_tokens=10, output_tokens=5):
    """构造 mock ChatCompletion 响应。"""
    usage = MagicMock()
    usage.prompt_tokens = input_tokens
    usage.completion_tokens = output_tokens

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class TestOpenAITextBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-5.4-mini"

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key", model="gpt-5.4")
            assert backend.model == "gpt-5.4"

    def test_capabilities(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            assert TextCapability.TEXT_GENERATION in backend.capabilities
            assert TextCapability.STRUCTURED_OUTPUT in backend.capabilities
            assert TextCapability.VISION in backend.capabilities

    async def test_generate_plain_text(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response("Test output", 15, 8)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Say hello")
            result = await backend.generate(request)

        assert result.text == "Test output"
        assert result.provider == PROVIDER_OPENAI
        assert result.model == "gpt-5.4-mini"
        assert result.input_tokens == 15
        assert result.output_tokens == 8

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.4-mini"
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"
        assert call_kwargs["messages"][0]["content"] == "Say hello"

    async def test_generate_with_system_prompt(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response("Response")
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Do something",
                system_prompt="You are helpful",
            )
            await backend.generate(request)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][0]["content"] == "You are helpful"
        assert call_kwargs["messages"][1]["role"] == "user"

    async def test_generate_with_vision(self, tmp_path):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response("I see a cat")
        )

        # 创建假图片文件
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="What is this?",
                images=[ImageInput(path=img_path)],
            )
            result = await backend.generate(request)

        assert result.text == "I see a cat"
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        user_msg = call_kwargs["messages"][-1]
        assert isinstance(user_msg["content"], list)
        # 应有 image_url 和 text 两个 content part
        types = [part["type"] for part in user_msg["content"]]
        assert "image_url" in types
        assert "text" in types

    async def test_generate_structured_output(self):
        schema_response = json.dumps({"name": "Alice", "age": 30})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_response(schema_response)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(
                prompt="Extract info",
                response_schema={"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}},
            )
            result = await backend.generate(request)

        assert result.text == schema_response
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "response_format" in call_kwargs

    async def test_generate_usage_none_tolerant(self):
        """usage 为 None 时不应崩溃。"""
        response = _make_mock_response("OK")
        response.usage = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.openai import OpenAITextBackend

            backend = OpenAITextBackend(api_key="test-key")
            request = TextGenerationRequest(prompt="Hi")
            result = await backend.generate(request)

        assert result.text == "OK"
        assert result.input_tokens is None
        assert result.output_tokens is None
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_openai_text_backend.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'lib.text_backends.openai'`

- [ ] **Step 3: 实现 `lib/text_backends/openai.py`**

```python
"""OpenAITextBackend — OpenAI 文本生成后端。"""

from __future__ import annotations

import logging

from lib.openai_shared import create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    resolve_schema,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAITextBackend:
    """OpenAI 文本生成后端，支持 Chat Completions API。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """生成文本回复。"""
        messages = _build_messages(request)
        kwargs: dict = {"model": self._model, "messages": messages}

        if request.response_schema:
            schema = resolve_schema(request.response_schema)
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": schema,
                },
            }

        response = await self._client.chat.completions.create(**kwargs)

        usage = response.usage
        return TextGenerationResult(
            text=response.choices[0].message.content or "",
            provider=PROVIDER_OPENAI,
            model=self._model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )


def _build_messages(request: TextGenerationRequest) -> list[dict]:
    """将 TextGenerationRequest 转为 OpenAI messages 格式。"""
    messages: list[dict] = []

    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})

    # 构建 user message
    if request.images:
        content: list[dict] = []
        for img in request.images:
            if img.path:
                from lib.image_backends.base import image_to_base64_data_uri

                data_uri = image_to_base64_data_uri(img.path)
                content.append({"type": "image_url", "image_url": {"url": data_uri}})
            elif img.url:
                content.append({"type": "image_url", "image_url": {"url": img.url}})
        content.append({"type": "text", "text": request.prompt})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": request.prompt})

    return messages
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_openai_text_backend.py -v`

Expected: All PASSED

- [ ] **Step 5: 注册 Backend 到 `lib/text_backends/__init__.py`**

At the end of the file已有的 `register_backend(PROVIDER_GROK, GrokTextBackend)` 之后添加：

```python
from lib.providers import PROVIDER_OPENAI
from lib.text_backends.openai import OpenAITextBackend

register_backend(PROVIDER_OPENAI, OpenAITextBackend)
```

- [ ] **Step 6: 在 `lib/text_backends/factory.py` 添加映射和参数传递**

在 `PROVIDER_ID_TO_BACKEND` 字典中添加 `"openai": "openai"` 映射：

```python
PROVIDER_ID_TO_BACKEND: dict[str, str] = {
    "gemini-aistudio": "gemini",
    "gemini-vertex": "gemini",
    "ark": "ark",
    "grok": "grok",
    "openai": "openai",
}
```

在 `create_text_backend_for_task` 函数中，在 `if provider_id == "gemini-vertex":` 分支逻辑之后（`else` 分支内），添加 OpenAI 的参数传递。当前 else 分支只设置 `api_key`，OpenAI 还需要 `base_url`。修改 else 分支：

将：
```python
    else:
        kwargs["api_key"] = provider_config.get("api_key")
        if provider_id == "gemini-aistudio":
            kwargs["base_url"] = provider_config.get("base_url")
```

改为：
```python
    else:
        kwargs["api_key"] = provider_config.get("api_key")
        if provider_id in ("gemini-aistudio", "openai"):
            kwargs["base_url"] = provider_config.get("base_url")
```

- [ ] **Step 7: 验证注册**

Run: `uv run python -c "from lib.text_backends import get_registered_backends; print(get_registered_backends())"`

Expected: 输出包含 `'openai'`

- [ ] **Step 8: Commit**

```bash
git add lib/text_backends/openai.py tests/test_openai_text_backend.py lib/text_backends/__init__.py lib/text_backends/factory.py
git commit -m "feat: 实现 OpenAI 文本后端（GPT-5.4 系列）"
```

---

### Task 4: OpenAI Image Backend

**Files:**
- Create: `lib/image_backends/openai.py`
- Create: `tests/test_openai_image_backend.py`
- Modify: `lib/image_backends/__init__.py`

- [ ] **Step 1: 编写测试 `tests/test_openai_image_backend.py`**

```python
"""OpenAIImageBackend 单元测试。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.providers import PROVIDER_OPENAI


def _make_mock_image_response(b64_data: str = "aW1hZ2VfZGF0YQ=="):
    """构造 mock ImagesResponse。"""
    datum = MagicMock()
    datum.b64_json = b64_data

    response = MagicMock()
    response.data = [datum]
    return response


class TestOpenAIImageBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-image-1.5"

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key", model="gpt-image-1-mini")
            assert backend.model == "gpt-image-1-mini"

    def test_capabilities(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert ImageCapability.TEXT_TO_IMAGE in backend.capabilities
            assert ImageCapability.IMAGE_TO_IMAGE in backend.capabilities

    async def test_text_to_image(self, tmp_path: Path):
        """T2I 路径应调用 images.generate()。"""
        b64_data = base64.b64encode(b"fake-png-data").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=output_path,
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.provider == PROVIDER_OPENAI
        assert result.model == "gpt-image-1.5"
        assert result.image_path == output_path
        assert output_path.read_bytes() == b"fake-png-data"

        mock_client.images.generate.assert_awaited_once()
        call_kwargs = mock_client.images.generate.call_args[1]
        assert call_kwargs["model"] == "gpt-image-1.5"
        assert call_kwargs["size"] == "1024x1792"  # 9:16
        assert call_kwargs["quality"] == "medium"   # 1K
        assert call_kwargs["response_format"] == "b64_json"

    async def test_image_to_image(self, tmp_path: Path):
        """I2I 路径应调用 images.edit()。"""
        b64_data = base64.b64encode(b"edited-image").decode()
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        # 创建参考图
        ref_path = tmp_path / "ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="Edit this image",
                output_path=output_path,
                reference_images=[ReferenceImage(path=str(ref_path))],
            )
            result = await backend.generate(request)

        assert result.image_path == output_path
        assert output_path.read_bytes() == b"edited-image"
        mock_client.images.edit.assert_awaited_once()
        mock_client.images.generate.assert_not_awaited()

    async def test_size_mapping(self, tmp_path: Path):
        """验证 aspect_ratio → size 映射。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            for aspect, expected_size in [("16:9", "1792x1024"), ("1:1", "1024x1024"), ("9:16", "1024x1792")]:
                output_path = tmp_path / f"output_{aspect.replace(':', '_')}.png"
                request = ImageGenerationRequest(
                    prompt="test", output_path=output_path, aspect_ratio=aspect,
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["size"] == expected_size, f"aspect={aspect}"

    async def test_quality_mapping(self, tmp_path: Path):
        """验证 image_size → quality 映射。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            for img_size, expected_quality in [("512PX", "low"), ("1K", "medium"), ("2K", "high"), ("4K", "high")]:
                output_path = tmp_path / f"output_{img_size}.png"
                request = ImageGenerationRequest(
                    prompt="test", output_path=output_path, image_size=img_size,
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["quality"] == expected_quality, f"size={img_size}"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_openai_image_backend.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'lib.image_backends.openai'`

- [ ] **Step 3: 实现 `lib/image_backends/openai.py`**

```python
"""OpenAIImageBackend — OpenAI 图片生成后端。"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.openai_shared import create_openai_client
from lib.providers import PROVIDER_OPENAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-image-1.5"

# aspect_ratio → OpenAI size
_SIZE_MAP: dict[str, str] = {
    "9:16": "1024x1792",
    "16:9": "1792x1024",
    "1:1": "1024x1024",
    "3:4": "1024x1792",
    "4:3": "1792x1024",
}

# image_size → OpenAI quality
_QUALITY_MAP: dict[str, str] = {
    "512PX": "low",
    "1K": "medium",
    "2K": "high",
    "4K": "high",
}


class OpenAIImageBackend:
    """OpenAI 图片生成后端，支持 T2I 和 I2I。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片（T2I 或 I2I）。"""
        if request.reference_images:
            return await self._generate_edit(request)
        return await self._generate_create(request)

    async def _generate_create(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """T2I：通过 images.generate() 生成。"""
        response = await self._client.images.generate(
            model=self._model,
            prompt=request.prompt,
            size=_SIZE_MAP.get(request.aspect_ratio, "1024x1792"),
            quality=_QUALITY_MAP.get(request.image_size, "medium"),
            response_format="b64_json",
            n=1,
        )
        return self._save_and_return(response, request)

    async def _generate_edit(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """I2I：通过 images.edit() 传入参考图。"""
        image_files = []
        try:
            for ref in request.reference_images:
                ref_path = Path(ref.path)
                if not ref_path.exists():
                    logger.warning("参考图不存在，跳过: %s", ref_path)
                    continue
                image_files.append(open(ref_path, "rb"))
            response = await self._client.images.edit(
                model=self._model,
                image=image_files,
                prompt=request.prompt,
                response_format="b64_json",
            )
        finally:
            for f in image_files:
                f.close()
        return self._save_and_return(response, request)

    def _save_and_return(self, response, request: ImageGenerationRequest) -> ImageGenerationResult:
        """解码 base64 保存图片并返回结果。"""
        image_bytes = base64.b64decode(response.data[0].b64_json)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(image_bytes)

        logger.info("OpenAI 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_openai_image_backend.py -v`

Expected: All PASSED

- [ ] **Step 5: 注册 Backend 到 `lib/image_backends/__init__.py`**

At the end of the file已有的 `register_backend(PROVIDER_GROK, GrokImageBackend)` 之后添加：

```python
from lib.image_backends.openai import OpenAIImageBackend
from lib.providers import PROVIDER_OPENAI

register_backend(PROVIDER_OPENAI, OpenAIImageBackend)
```

- [ ] **Step 6: Commit**

```bash
git add lib/image_backends/openai.py tests/test_openai_image_backend.py lib/image_backends/__init__.py
git commit -m "feat: 实现 OpenAI 图片后端（GPT Image 1.5）"
```

---

### Task 5: OpenAI Video Backend

**Files:**
- Create: `lib/video_backends/openai.py`
- Create: `tests/test_openai_video_backend.py`
- Modify: `lib/video_backends/__init__.py`

- [ ] **Step 1: 编写测试 `tests/test_openai_video_backend.py`**

```python
"""OpenAIVideoBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.providers import PROVIDER_OPENAI
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
)


def _make_mock_video(status="completed", seconds="8", video_id="vid_123"):
    """构造 mock Video 响应。"""
    video = MagicMock()
    video.id = video_id
    video.status = status
    video.seconds = seconds
    video.error = None
    return video


def _make_mock_content(data: bytes = b"fake-video-data"):
    """构造 mock download_content 响应。"""
    content = MagicMock()
    content.content = data
    return content


class TestOpenAIVideoBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "sora-2"

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2-pro")
            assert backend.model == "sora-2-pro"

    def test_capabilities(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
            assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
            assert VideoCapability.GENERATE_AUDIO not in backend.capabilities
            assert VideoCapability.NEGATIVE_PROMPT not in backend.capabilities
            assert VideoCapability.SEED_CONTROL not in backend.capabilities

    async def test_text_to_video(self, tmp_path: Path):
        video_data = b"mp4-video-content"
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(
            return_value=_make_mock_video(seconds="8")
        )
        mock_client.videos.download_content = AsyncMock(
            return_value=_make_mock_content(video_data)
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="A cat walking in the park",
                output_path=output_path,
                aspect_ratio="9:16",
                duration_seconds=8,
            )
            result = await backend.generate(request)

        assert result.provider == PROVIDER_OPENAI
        assert result.model == "sora-2"
        assert result.duration_seconds == 8
        assert result.video_path == output_path
        assert result.task_id == "vid_123"
        assert output_path.read_bytes() == video_data

        call_kwargs = mock_client.videos.create_and_poll.call_args[1]
        assert call_kwargs["prompt"] == "A cat walking in the park"
        assert call_kwargs["model"] == "sora-2"
        assert call_kwargs["seconds"] == "8"
        assert call_kwargs["size"] == "720x1280"  # 9:16
        assert "input_reference" not in call_kwargs

    async def test_image_to_video(self, tmp_path: Path):
        start_image = tmp_path / "start.png"
        start_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(
            return_value=_make_mock_video(seconds="4")
        )
        mock_client.videos.download_content = AsyncMock(
            return_value=_make_mock_content(b"video")
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="Animate this",
                output_path=output_path,
                start_image=start_image,
                duration_seconds=4,
            )
            result = await backend.generate(request)

        assert result.duration_seconds == 4
        call_kwargs = mock_client.videos.create_and_poll.call_args[1]
        ref = call_kwargs["input_reference"]
        assert ref["type"] == "image_url"
        assert ref["image_url"].startswith("data:image/png;base64,")

    async def test_failed_video_raises(self, tmp_path: Path):
        error = MagicMock()
        error.message = "Content policy violation"
        failed_video = _make_mock_video(status="failed")
        failed_video.error = error

        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=failed_video)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="Bad content",
                output_path=output_path,
            )
            with pytest.raises(RuntimeError, match="Sora 视频生成失败"):
                await backend.generate(request)

    async def test_duration_mapping(self, tmp_path: Path):
        """验证 duration_seconds → VideoSeconds 映射。"""
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(
            return_value=_make_mock_video(seconds="4")
        )
        mock_client.videos.download_content = AsyncMock(
            return_value=_make_mock_content(b"v")
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")

            for seconds, expected in [(3, "4"), (4, "4"), (5, "8"), (8, "8"), (10, "12"), (15, "12")]:
                output_path = tmp_path / f"output_{seconds}.mp4"
                request = VideoGenerationRequest(
                    prompt="test", output_path=output_path, duration_seconds=seconds,
                )
                await backend.generate(request)
                call_kwargs = mock_client.videos.create_and_poll.call_args[1]
                assert call_kwargs["seconds"] == expected, f"duration={seconds}"

    async def test_size_mapping(self, tmp_path: Path):
        """验证 aspect_ratio → VideoSize 映射。"""
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(
            return_value=_make_mock_video(seconds="4")
        )
        mock_client.videos.download_content = AsyncMock(
            return_value=_make_mock_content(b"v")
        )

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")

            for aspect, expected_size in [("9:16", "720x1280"), ("16:9", "1280x720")]:
                output_path = tmp_path / f"output_{aspect.replace(':', '_')}.mp4"
                request = VideoGenerationRequest(
                    prompt="test", output_path=output_path, aspect_ratio=aspect,
                )
                await backend.generate(request)
                call_kwargs = mock_client.videos.create_and_poll.call_args[1]
                assert call_kwargs["size"] == expected_size, f"aspect={aspect}"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_openai_video_backend.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'lib.video_backends.openai'`

- [ ] **Step 3: 实现 `lib/video_backends/openai.py`**

```python
"""OpenAIVideoBackend — OpenAI Sora 视频生成后端。"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from lib.openai_shared import create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sora-2"

# aspect_ratio → Sora VideoSize
_SIZE_MAP: dict[str, str] = {
    "9:16": "720x1280",
    "16:9": "1280x720",
}


class OpenAIVideoBackend:
    """OpenAI Sora 视频生成后端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频（T2V 或 I2V）。"""
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": _map_duration(request.duration_seconds),
            "size": _SIZE_MAP.get(request.aspect_ratio, "720x1280"),
        }

        if request.start_image and Path(request.start_image).exists():
            kwargs["input_reference"] = _encode_start_image(request.start_image)

        logger.info(
            "OpenAI 视频生成开始: model=%s, seconds=%s",
            self._model,
            kwargs["seconds"],
        )

        video = await self._client.videos.create_and_poll(**kwargs)

        if video.status == "failed":
            raise RuntimeError(f"Sora 视频生成失败: {video.error}")

        content = await self._client.videos.download_content(video.id)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(content.content)

        logger.info("OpenAI 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            duration_seconds=int(video.seconds),
            task_id=video.id,
        )


def _map_duration(seconds: int) -> str:
    """将 duration_seconds 映射到 Sora 支持的时长。"""
    if seconds <= 4:
        return "4"
    elif seconds <= 8:
        return "8"
    else:
        return "12"


def _encode_start_image(image_path: Path) -> dict:
    """将本地图片编码为 Sora input_reference 参数。"""
    image_path = Path(image_path)
    suffix = image_path.suffix.lower()
    mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")
    image_data = image_path.read_bytes()
    b64 = base64.b64encode(image_data).decode("ascii")
    data_uri = f"data:{mime_type};base64,{b64}"
    return {
        "type": "image_url",
        "image_url": data_uri,
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_openai_video_backend.py -v`

Expected: All PASSED

- [ ] **Step 5: 注册 Backend 到 `lib/video_backends/__init__.py`**

At the end of the file已有的 `register_backend(PROVIDER_GROK, GrokVideoBackend)` 之后添加：

```python
# OpenAI Sora
from lib.providers import PROVIDER_OPENAI
from lib.video_backends.openai import OpenAIVideoBackend

register_backend(PROVIDER_OPENAI, OpenAIVideoBackend)
```

- [ ] **Step 6: Commit**

```bash
git add lib/video_backends/openai.py tests/test_openai_video_backend.py lib/video_backends/__init__.py
git commit -m "feat: 实现 OpenAI 视频后端（Sora 2）"
```

---

### Task 6: Cost Calculator 扩展

**Files:**
- Modify: `lib/cost_calculator.py`
- Modify: `tests/test_cost_calculator.py`

- [ ] **Step 1: 在 `tests/test_cost_calculator.py` 添加 OpenAI 测试用例**

At the end of the file添加：

```python
class TestOpenAICost:
    def test_openai_text_cost(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_text_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            provider="openai",
            model="gpt-5.4-mini",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.75 + 4.50)

    def test_openai_text_cost_default_model(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_text_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            provider="openai",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.75)

    def test_openai_image_cost(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_image_cost(
            model="gpt-image-1.5",
            quality="medium",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.034)

    def test_openai_image_cost_low(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_image_cost(
            model="gpt-image-1-mini",
            quality="low",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.005)

    def test_openai_video_cost(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_video_cost(
            duration_seconds=8,
            model="sora-2",
        )
        assert currency == "USD"
        assert amount == pytest.approx(0.80)

    def test_openai_video_cost_pro(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_openai_video_cost(
            duration_seconds=4,
            model="sora-2-pro",
            resolution="1080p",
        )
        assert currency == "USD"
        assert amount == pytest.approx(2.80)

    def test_unified_entry_openai(self):
        calculator = CostCalculator()
        # 文本
        amount, currency = calculator.calculate_cost(
            "openai", "text", input_tokens=500_000, output_tokens=100_000,
        )
        assert amount == pytest.approx(0.375 + 0.45)

        # 图片
        amount, currency = calculator.calculate_cost(
            "openai", "image", model="gpt-image-1.5", quality="high",
        )
        assert amount == pytest.approx(0.133)

        # 视频
        amount, currency = calculator.calculate_cost(
            "openai", "video", duration_seconds=12, model="sora-2",
        )
        assert amount == pytest.approx(1.20)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_cost_calculator.py::TestOpenAICost -v`

Expected: FAIL — `AttributeError: 'CostCalculator' object has no attribute 'calculate_openai_image_cost'`

- [ ] **Step 3: 在 `lib/cost_calculator.py` 添加 OpenAI 定价和计算方法**

在 `CostCalculator` 类中添加以下定价常量（在 `GROK_TEXT_COST` 之后）：

```python
    # OpenAI 文本 token 费率（美元/百万 token）
    OPENAI_TEXT_COST = {
        "gpt-5.4": {"input": 2.50, "output": 15.00},
        "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
        "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
    }

    # OpenAI 图片费率（美元/张），按 quality 区分
    OPENAI_IMAGE_COST = {
        "gpt-image-1.5": {"low": 0.009, "medium": 0.034, "high": 0.133},
        "gpt-image-1-mini": {"low": 0.005, "medium": 0.011, "high": 0.036},
    }
    DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1.5"

    # OpenAI 视频费率（美元/秒），按分辨率区分
    OPENAI_VIDEO_COST = {
        "sora-2": {"720p": 0.10},
        "sora-2-pro": {"720p": 0.30, "1024p": 0.50, "1080p": 0.70},
    }
    DEFAULT_OPENAI_VIDEO_MODEL = "sora-2"
```

添加计算方法（在 `calculate_grok_video_cost` 之后）：

```python
    def calculate_openai_image_cost(
        self,
        model: str | None = None,
        quality: str = "medium",
    ) -> tuple[float, str]:
        """
        计算 OpenAI 图片生成费用。

        Returns:
            (amount, currency) — 金额和币种 (USD)
        """
        model = model or self.DEFAULT_OPENAI_IMAGE_MODEL
        model_costs = self.OPENAI_IMAGE_COST.get(model, self.OPENAI_IMAGE_COST[self.DEFAULT_OPENAI_IMAGE_MODEL])
        per_image = model_costs.get(quality, model_costs.get("medium", 0.034))
        return per_image, "USD"

    def calculate_openai_video_cost(
        self,
        duration_seconds: int,
        model: str | None = None,
        resolution: str = "720p",
    ) -> tuple[float, str]:
        """
        计算 OpenAI 视频生成费用。

        Returns:
            (amount, currency) — 金额和币种 (USD)
        """
        model = model or self.DEFAULT_OPENAI_VIDEO_MODEL
        model_costs = self.OPENAI_VIDEO_COST.get(model, self.OPENAI_VIDEO_COST[self.DEFAULT_OPENAI_VIDEO_MODEL])
        per_second = model_costs.get(resolution.lower(), next(iter(model_costs.values())))
        return duration_seconds * per_second, "USD"
```

更新 `_TEXT_COST_TABLES` 添加 OpenAI 入口：

```python
    _TEXT_COST_TABLES: dict[str, tuple[dict, str, str]] = {
        "ark": ("ARK_TEXT_COST", "doubao-seed-2-0-lite-260215", "CNY"),
        "grok": ("GROK_TEXT_COST", "grok-4-1-fast-reasoning", "USD"),
        "openai": ("OPENAI_TEXT_COST", "gpt-5.4-mini", "USD"),
    }
```

在 `calculate_cost()` 中导入 `PROVIDER_OPENAI` 并添加路由分支。修改文件顶部导入：

```python
from lib.providers import PROVIDER_ARK, PROVIDER_GROK, PROVIDER_OPENAI, CallType
```

在 `calculate_cost()` 的 `if call_type == "image":` 分支中，在 `if provider == PROVIDER_GROK:` 之后添加：

```python
            if provider == PROVIDER_OPENAI:
                return self.calculate_openai_image_cost(model=model, quality=quality)
```

在 `if call_type == "video":` 分支中，在 `if provider == PROVIDER_GROK:` 之后添加：

```python
            if provider == PROVIDER_OPENAI:
                return self.calculate_openai_video_cost(
                    duration_seconds=duration_seconds or 8,
                    model=model,
                    resolution=resolution or "720p",
                )
```

`calculate_cost()` 签名新增 `quality` 参数：

```python
    def calculate_cost(
        self,
        provider: str,
        call_type: CallType,
        *,
        model: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        generate_audio: bool = True,
        usage_tokens: int | None = None,
        service_tier: str = "default",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        quality: str | None = None,
    ) -> tuple[float, str]:
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_cost_calculator.py -v`

Expected: All PASSED（包括原有测试和新增 OpenAI 测试）

- [ ] **Step 5: Commit**

```bash
git add lib/cost_calculator.py tests/test_cost_calculator.py
git commit -m "feat: 添加 OpenAI 文本/图片/视频定价计算"
```

---

### Task 7: 连接测试与工厂集成

**Files:**
- Modify: `server/routers/providers.py`
- Modify: `server/services/generation_tasks.py`

- [ ] **Step 1: 在 `server/routers/providers.py` 添加 OpenAI 连接测试**

在 `_test_grok` 函数之后、`_TEST_DISPATCH` 字典之前添加：

```python
def _test_openai(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 OpenAI API Key。"""
    from openai import OpenAI

    kwargs: dict = {"api_key": config["api_key"]}
    base_url = config.get("base_url")
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    models = client.models.list()
    available = sorted(m.id for m in models.data[:10])
    return ConnectionTestResponse(
        success=True,
        available_models=available,
        message="连接成功",
    )
```

在 `_TEST_DISPATCH` 字典中添加 `"openai"` 入口：

```python
_TEST_DISPATCH: dict[str, Callable[[dict[str, str]], ConnectionTestResponse]] = {
    "gemini-aistudio": _test_gemini_aistudio,
    "gemini-vertex": _test_gemini_vertex,
    "ark": _test_ark,
    "grok": _test_grok,
    "openai": _test_openai,
}
```

- [ ] **Step 2: 在 `server/services/generation_tasks.py` 添加 OpenAI 工厂分支**

At the top of the file的 `from lib.providers import ...` 导入行中添加 `PROVIDER_OPENAI`：

```python
from lib.providers import PROVIDER_ARK, PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_OPENAI
```

在 `_PROVIDER_ID_TO_BACKEND` 字典中**添加一行**（Note: 不要替换整个字典，保留已有条目）：

```python
# 在现有字典末尾添加：
    PROVIDER_OPENAI: PROVIDER_OPENAI,
```

在 `_DEFAULT_VIDEO_RESOLUTION` 字典中添加 OpenAI 条目：

```python
# 在现有字典末尾添加：
    PROVIDER_OPENAI: "720p",
```

在 `_get_or_create_video_backend` 函数中，在 `elif backend_name == PROVIDER_GROK:` 分支之后添加：

```python
    elif backend_name == PROVIDER_OPENAI:
        db_config = await resolver.provider_config("openai")
        kwargs["api_key"] = db_config.get("api_key")
        kwargs["base_url"] = db_config.get("base_url")
        kwargs["model"] = effective_model
```

在 `_get_or_create_image_backend` 函数中，在 `elif backend_name == PROVIDER_GROK:` 分支之后添加相同的分支：

```python
    elif backend_name == PROVIDER_OPENAI:
        db_config = await resolver.provider_config("openai")
        kwargs["api_key"] = db_config.get("api_key")
        kwargs["base_url"] = db_config.get("base_url")
        kwargs["model"] = effective_model
```

- [ ] **Step 3: 验证导入无误**

Run: `uv run python -c "from server.routers.providers import _TEST_DISPATCH; print(list(_TEST_DISPATCH.keys()))"`

Expected: 输出包含 `'openai'`

- [ ] **Step 4: 提交**

```bash
git add server/routers/providers.py server/services/generation_tasks.py
git commit -m "feat: 添加 OpenAI 连接测试与工厂集成"
```

---

### Task 8: 前端图标集成

**Files:**
- Modify: `frontend/src/components/ui/ProviderIcon.tsx`

- [ ] **Step 1: 更新 `ProviderIcon.tsx`**

At the top of the file import 区域添加 OpenAI 图标导入：

```typescript
import OpenAIColor from "@lobehub/icons/es/OpenAI/components/Color";
```

在 `PROVIDER_NAMES` 中添加：

```typescript
export const PROVIDER_NAMES: Record<string, string> = {
  "gemini-aistudio": "AI Studio",
  "gemini-vertex": "Vertex AI",
  ark: "火山方舟",
  grok: "Grok",
  openai: "OpenAI",
};
```

在 `ProviderIcon` 组件中，在 `if (providerId === "ark")` 之后添加 OpenAI 分支：

```typescript
if (providerId === "openai") return <OpenAIColor className={cls} />;
```

完整的组件函数：

```typescript
export function ProviderIcon({ providerId, className }: { providerId: string; className?: string }) {
  const cls = className ?? "h-6 w-6";
  if (providerId === "gemini-vertex") return <VertexAIColor className={cls} />;
  if (providerId.startsWith("gemini")) return <GeminiColor className={cls} />;
  if (providerId.startsWith("grok")) return <GrokMono className={cls} />;
  if (providerId === "ark") return <VolcengineColor className={cls} />;
  if (providerId === "openai") return <OpenAIColor className={cls} />;
  // Fallback: first letter badge
  return (
    <span className={`inline-flex items-center justify-center rounded bg-gray-700 text-xs font-bold uppercase text-gray-300 ${cls}`}>
      {providerId[0]}
    </span>
  );
}
```

- [ ] **Step 2: 验证前端构建**

Run: `cd frontend && pnpm typecheck`

Expected: 无类型错误

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/ui/ProviderIcon.tsx
git commit -m "feat: 添加 OpenAI 供应商图标"
```

---

### Task 9: 全量测试与 lint

**Files:** 无新增

- [ ] **Step 1: 运行 ruff lint**

Run: `uv run ruff check lib/openai_shared.py lib/text_backends/openai.py lib/image_backends/openai.py lib/video_backends/openai.py`

Expected: 无错误。如有，修复后重新运行。

- [ ] **Step 2: 运行 ruff format**

Run: `uv run ruff format lib/openai_shared.py lib/text_backends/openai.py lib/image_backends/openai.py lib/video_backends/openai.py`

- [ ] **Step 3: 运行全量后端测试**

Run: `uv run python -m pytest -v`

Expected: All PASSED，无回归

- [ ] **Step 4: 运行前端检查**

Run: `cd frontend && pnpm check`

Expected: typecheck + test 均通过

- [ ] **Step 5: 如有格式修复，提交**

```bash
git add -u
git commit -m "style: ruff format OpenAI 后端代码"
```
