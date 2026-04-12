# Instructor Integration and Structured Output Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the Instructor library to fix the issue where ArkTextBackend structured output is unavailable for doubao models, implementing automatic fallback based on model capabilities.

**Architecture:** Create a new `instructor_support.py` utility module providing Instructor fallback functions. `ArkTextBackend` queries model capabilities from PROVIDER_REGISTRY at construction time, and routes in `_generate_structured()` based on capabilities: the native path or the Instructor MD_JSON path.

**Tech Stack:** instructor, volcenginesdkarkruntime (Ark), pydantic

---

## File Structure

| File | Action | Responsibility |
|------|------|------|
| `lib/text_backends/instructor_support.py` | Create | Pure functions for Instructor fallback |
| `lib/text_backends/ark.py` | Modify | Capability check + fallback routing |
| `lib/config/registry.py` | Modify | Fix doubao model capabilities |
| `lib/project_manager.py` | Modify | Pass Pydantic class as response_schema |
| `pyproject.toml` | Modify | Add instructor dependency |
| `tests/test_text_backends/test_instructor_support.py` | Create | instructor_support tests |
| `tests/test_text_backends/test_ark.py` | Modify | Capability check + fallback path tests |

---

### Task 1: Add instructor Dependency + Fix Registry

**Files:**
- Modify: `pyproject.toml:7-31`
- Modify: `lib/config/registry.py:106-111`

- [ ] **Step 1: Add instructor dependency**

In the `dependencies` list in `pyproject.toml`, add `instructor` after `pyjianyingdraft`:

```python
# Add at the end of pyproject.toml dependencies list, after pyjianyingdraft:
    "instructor>=1.7.0",
```

- [ ] **Step 2: Fix doubao model capabilities**

At `lib/config/registry.py:106-111`, remove the `structured_output` capability from `doubao-seed-2-0-lite-260215`:

```python
# Before (line 106-111):
            "doubao-seed-2-0-lite-260215": ModelInfo(
                display_name="Doubao Seed 2.0 Lite",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
            ),

# After:
            "doubao-seed-2-0-lite-260215": ModelInfo(
                display_name="Doubao Seed 2.0 Lite",
                media_type="text",
                capabilities=["text_generation", "vision"],
                default=True,
            ),
```

- [ ] **Step 3: Install dependencies**

Run: `uv sync`
Expected: instructor and its dependencies installed successfully

- [ ] **Step 4: Verify existing tests are unaffected**

Run: `uv run python -m pytest tests/test_text_backends/ -v`
Expected: all PASS (registry change does not affect existing tests since they mock the Ark client)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock lib/config/registry.py
git commit -m "chore: add instructor dependency and fix doubao model structured_output capability declaration"
```

---

### Task 2: Create instructor_support Module (TDD)

**Files:**
- Create: `tests/test_text_backends/test_instructor_support.py`
- Create: `lib/text_backends/instructor_support.py`

- [ ] **Step 1: Write instructor_support tests**

Create `tests/test_text_backends/test_instructor_support.py`:

```python
"""Tests for instructor_support module."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from lib.text_backends.instructor_support import generate_structured_via_instructor


class SampleModel(BaseModel):
    name: str
    age: int


class TestGenerateStructuredViaInstructor:
    def test_returns_json_and_tokens(self):
        """Returns JSON text and token counts correctly."""
        mock_client = MagicMock()
        sample = SampleModel(name="Alice", age=30)
        mock_completion = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
        )

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            json_text, input_tokens, output_tokens = generate_structured_via_instructor(
                client=mock_client,
                model="doubao-seed-2-0-lite-260215",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
            )

        assert json_text == sample.model_dump_json()
        assert input_tokens == 50
        assert output_tokens == 20

    def test_passes_mode_and_retries(self):
        """Correctly passes mode and max_retries parameters."""
        from instructor import Mode

        mock_client = MagicMock()
        sample = SampleModel(name="Bob", age=25)
        mock_completion = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            generate_structured_via_instructor(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                mode=Mode.MD_JSON,
                max_retries=3,
            )

            # Verify from_openai was called with the correct mode
            mock_instructor.from_openai.assert_called_once_with(mock_client, mode=Mode.MD_JSON)
            # Verify create_with_completion was called with the correct arguments
            mock_patched.chat.completions.create_with_completion.assert_called_once_with(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_retries=3,
            )

    def test_handles_none_usage(self):
        """Returns None token counts when completion.usage is None."""
        mock_client = MagicMock()
        sample = SampleModel(name="Charlie", age=35)
        mock_completion = SimpleNamespace(usage=None)

        with patch("lib.text_backends.instructor_support.instructor") as mock_instructor:
            mock_patched = MagicMock()
            mock_instructor.from_openai.return_value = mock_patched
            mock_patched.chat.completions.create_with_completion.return_value = (
                sample,
                mock_completion,
            )

            json_text, input_tokens, output_tokens = generate_structured_via_instructor(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
            )

        assert json_text == sample.model_dump_json()
        assert input_tokens is None
        assert output_tokens is None
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_text_backends/test_instructor_support.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.text_backends.instructor_support'`

- [ ] **Step 3: Implement instructor_support module**

Create `lib/text_backends/instructor_support.py`:

```python
"""Instructor fallback support — provides prompt injection + parsing + retry for models without native structured output."""
from __future__ import annotations

import instructor
from instructor import Mode
from pydantic import BaseModel


def generate_structured_via_instructor(
    client,
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
) -> tuple[str, int | None, int | None]:
    """Generate structured output via Instructor.

    Returns (json_text, input_tokens, output_tokens).
    """
    patched = instructor.from_openai(client, mode=mode)
    result, completion = patched.chat.completions.create_with_completion(
        model=model,
        messages=messages,
        response_model=response_model,
        max_retries=max_retries,
    )
    json_text = result.model_dump_json()

    input_tokens = None
    output_tokens = None
    if completion.usage:
        input_tokens = completion.usage.prompt_tokens
        output_tokens = completion.usage.completion_tokens

    return json_text, input_tokens, output_tokens
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_text_backends/test_instructor_support.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add lib/text_backends/instructor_support.py tests/test_text_backends/test_instructor_support.py
git commit -m "feat: add instructor_support module providing structured output fallback function"
```

---

### Task 3: Refactor ArkTextBackend for Capability-Aware Fallback (TDD)

**Files:**
- Modify: `tests/test_text_backends/test_ark.py`
- Modify: `lib/text_backends/ark.py:21-84`

- [ ] **Step 1: Write tests for capability detection and fallback path**

Add a new test class at the end of `tests/test_text_backends/test_ark.py`:

```python
class TestCapabilityAwareStructured:
    """Test structured output path selection based on model capabilities."""

    @pytest.fixture
    def backend_no_structured(self, mock_ark):
        """Create a backend whose model does not support native structured_output."""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        # Use default model doubao-seed-2-0-lite-260215, which has structured_output removed from registry
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    @pytest.fixture
    def backend_with_structured(self, mock_ark):
        """Create a backend whose model supports native structured_output (simulated)."""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        # Manually set to support native
        b._supports_native_structured = True
        return b

    async def test_default_model_does_not_support_native_structured(self, backend_no_structured):
        """Default doubao model does not support native structured output."""
        assert backend_no_structured._supports_native_structured is False

    async def test_fallback_uses_instructor(self, backend_no_structured):
        """When model does not support native, falls back to the Instructor path."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            key: str

        sample = TestModel(key="value")

        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=(sample.model_dump_json(), 50, 20),
        ) as mock_instructor:
            with patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
                result = await backend_no_structured.generate(
                    TextGenerationRequest(prompt="gen", response_schema=TestModel)
                )

            mock_instructor.assert_called_once()
            assert result.text == '{"key":"value"}'
            assert result.input_tokens == 50
            assert result.output_tokens == 20

    async def test_native_path_when_supported(self, backend_with_structured):
        """When model supports native, uses the response_format path."""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(
            return_value=mock_resp
        )

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        with patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await backend_with_structured.generate(
                TextGenerationRequest(prompt="gen", response_schema=schema)
            )

        assert result.text == '{"key": "value"}'
        call_args = backend_with_structured._test_client.chat.completions.create.call_args
        assert "response_format" in call_args.kwargs

    async def test_unknown_model_falls_back_to_instructor(self, mock_ark):
        """Unregistered model conservatively falls back to Instructor."""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k", model="unknown-model-xyz")
        assert b._supports_native_structured is False
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_text_backends/test_ark.py::TestCapabilityAwareStructured -v`
Expected: FAIL — `AttributeError: 'ArkTextBackend' object has no attribute '_supports_native_structured'`

- [ ] **Step 3: Implement ArkTextBackend refactoring**

Modify `lib/text_backends/ark.py`. Add capability detection in `__init__`, and add routing in `_generate_structured`:

```python
"""ArkTextBackend — Volcano Engine Ark text generation backend."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional, Set

from lib.providers import PROVIDER_ARK
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "doubao-seed-2-0-lite-260215"


class ArkTextBackend:
    """Ark (Volcano Engine) text generation backend."""

    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None):
        from volcenginesdkarkruntime import Ark

        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("Ark API Key not provided")

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or DEFAULT_MODEL
        self._supports_native_structured = self._check_native_structured()
        self._capabilities: Set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.VISION,
        }
        if self._supports_native_structured:
            self._capabilities.add(TextCapability.STRUCTURED_OUTPUT)

    def _check_native_structured(self) -> bool:
        """Check whether the current model supports native structured output."""
        from lib.config.registry import PROVIDER_REGISTRY

        provider_meta = PROVIDER_REGISTRY.get("ark")
        if provider_meta:
            model_info = provider_meta.models.get(self._model)
            if model_info:
                return "structured_output" in model_info.capabilities
        # Conservatively fall back for unregistered models
        return False

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> Set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if request.images:
            return await self._generate_vision(request)
        if request.response_schema:
            return await self._generate_structured(request)
        return await self._generate_plain(request)

    async def _generate_plain(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)
        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            model=self._model,
            messages=messages,
        )
        return self._parse_chat_response(response)

    async def _generate_structured(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self._supports_native_structured:
            from lib.text_backends.base import resolve_schema

            messages = self._build_messages(request)
            schema = resolve_schema(request.response_schema)
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=self._model,
                messages=messages,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "response",
                    "schema": schema,
                }},
            )
            return self._parse_chat_response(response)
        else:
            from lib.text_backends.instructor_support import generate_structured_via_instructor

            messages = self._build_messages(request)
            json_text, input_tokens, output_tokens = await asyncio.to_thread(
                generate_structured_via_instructor,
                client=self._client,
                model=self._model,
                messages=messages,
                response_model=request.response_schema,
            )
            return TextGenerationResult(
                text=json_text,
                provider=PROVIDER_ARK,
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    # _generate_vision (lines 86-119), _build_messages (lines 121-126), _parse_chat_response (lines 128-138) remain unchanged
```

Note: `_generate_vision`, `_build_messages`, and `_parse_chat_response` remain unchanged and are not repeated here.

- [ ] **Step 4: Update existing capabilities test**

The `TestProperties.test_capabilities` in `tests/test_text_backends/test_ark.py` needs updating because the default model no longer has `STRUCTURED_OUTPUT`:

```python
# Update TestProperties.test_capabilities (line 28-34):
    def test_capabilities(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.VISION,
        }
```

- [ ] **Step 5: Run all ark tests to confirm they pass**

Run: `uv run python -m pytest tests/test_text_backends/test_ark.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add lib/text_backends/ark.py tests/test_text_backends/test_ark.py
git commit -m "feat: ArkTextBackend supports capability-aware structured output fallback"
```

---

### Task 4: Fix ProjectManager + Full Regression

**Files:**
- Modify: `lib/project_manager.py:1596`

- [ ] **Step 1: Change response_schema to pass Pydantic class directly**

At `lib/project_manager.py:1596`, change the `.model_json_schema()` call to pass the Pydantic class directly:

```python
# Before (line 1593-1597):
        result = await generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview.model_json_schema(),
            ),

# After:
        result = await generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview,
            ),
```

- [ ] **Step 2: Run full regression tests**

Run: `uv run python -m pytest -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add lib/project_manager.py
git commit -m "fix: ProjectManager passes Pydantic class instead of JSON Schema dict as response_schema"
```
