# Instructor Integration and Structured Output Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the Instructor library to fix the issue where ArkTextBackend structured output is unavailable for doubao models, implementing automatic fallback based on model capabilities.

**Architecture:** 新建 `instructor_support.py` 工具模块提供 Instructor 降级函数。`ArkTextBackend` 构造时从 PROVIDER_REGISTRY 查询模型能力，在 `_generate_structured()` 中按能力分流：原生路径或 Instructor MD_JSON 路径。

**Tech Stack:** instructor, volcenginesdkarkruntime (Ark), pydantic

---

## File Structure

| File | Action | Responsibility |
|------|------|------|
| `lib/text_backends/instructor_support.py` | Create | Pure functions for Instructor fallback |
| `lib/text_backends/ark.py` | Modify | Capability check + fallback routing |
| `lib/config/registry.py` | Modify | Fix doubao model capabilities |
| `lib/project_manager.py` | Modify | response_schema 传 Pydantic 类 |
| `pyproject.toml` | Modify | 添加 instructor 依赖 |
| `tests/test_text_backends/test_instructor_support.py` | Create | instructor_support tests |
| `tests/test_text_backends/test_ark.py` | Modify | Capability check + fallback path tests |

---

### Task 1: 添加 instructor 依赖 + 修正 Registry

**Files:**
- Modify: `pyproject.toml:7-31`
- Modify: `lib/config/registry.py:106-111`

- [ ] **Step 1: 添加 instructor 依赖**

在 `pyproject.toml` 的 `dependencies` 列表中添加 `instructor`：

```python
# pyproject.toml dependencies 列表末尾，pyjianyingdraft 之后添加：
    "instructor>=1.7.0",
```

- [ ] **Step 2: 修正豆包模型 capabilities**

在 `lib/config/registry.py:106-111`，移除 `doubao-seed-2-0-lite-260215` 的 `structured_output` 能力：

```python
# 修改前 (line 106-111):
            "doubao-seed-2-0-lite-260215": ModelInfo(
                display_name="豆包 Seed 2.0 Lite",
                media_type="text",
                capabilities=["text_generation", "structured_output", "vision"],
                default=True,
            ),

# 修改后:
            "doubao-seed-2-0-lite-260215": ModelInfo(
                display_name="豆包 Seed 2.0 Lite",
                media_type="text",
                capabilities=["text_generation", "vision"],
                default=True,
            ),
```

- [ ] **Step 3: 安装依赖**

Run: `uv sync`
Expected: 成功安装 instructor 及其依赖

- [ ] **Step 4: 验证现有测试不受影响**

Run: `uv run python -m pytest tests/test_text_backends/ -v`
Expected: all PASS（registry 改动不影响现有测试，因为测试 mock 了 Ark 客户端）

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock lib/config/registry.py
git commit -m "chore: 添加 instructor 依赖，修正豆包模型 structured_output 能力声明"
```

---

### Task 2: 创建 instructor_support 模块（TDD）

**Files:**
- Create: `tests/test_text_backends/test_instructor_support.py`
- Create: `lib/text_backends/instructor_support.py`

- [ ] **Step 1: 编写 instructor_support 测试**

创建 `tests/test_text_backends/test_instructor_support.py`：

```python
"""instructor_support 模块测试。"""
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
        """正确返回 JSON 文本和 token 统计。"""
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
        """正确传递 mode 和 max_retries 参数。"""
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

            # 验证 from_openai 使用了正确的 mode
            mock_instructor.from_openai.assert_called_once_with(mock_client, mode=Mode.MD_JSON)
            # 验证 create_with_completion 使用了正确的参数
            mock_patched.chat.completions.create_with_completion.assert_called_once_with(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                response_model=SampleModel,
                max_retries=3,
            )

    def test_handles_none_usage(self):
        """completion.usage 为 None 时返回 None token 统计。"""
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

- [ ] **Step 3: 实现 instructor_support 模块**

创建 `lib/text_backends/instructor_support.py`：

```python
"""Instructor 降级支持 — 为不支持原生结构化输出的模型提供 prompt 注入 + 解析 + 重试。"""
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
    """通过 Instructor 生成结构化输出。

    返回 (json_text, input_tokens, output_tokens)。
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
git commit -m "feat: 添加 instructor_support 模块，提供结构化输出降级函数"
```

---

### Task 3: 改造 ArkTextBackend 支持能力感知降级（TDD）

**Files:**
- Modify: `tests/test_text_backends/test_ark.py`
- Modify: `lib/text_backends/ark.py:21-84`

- [ ] **Step 1: 为能力判断和降级路径编写测试**

在 `tests/test_text_backends/test_ark.py` 末尾新增测试类：

```python
class TestCapabilityAwareStructured:
    """测试基于模型能力的结构化输出路径选择。"""

    @pytest.fixture
    def backend_no_structured(self, mock_ark):
        """创建一个模型不支持原生 structured_output 的 backend。"""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        # 使用默认模型 doubao-seed-2-0-lite-260215，registry 中已移除 structured_output
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    @pytest.fixture
    def backend_with_structured(self, mock_ark):
        """创建一个模型支持原生 structured_output 的 backend（模拟）。"""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        # 手动设置为支持原生
        b._supports_native_structured = True
        return b

    async def test_default_model_does_not_support_native_structured(self, backend_no_structured):
        """默认豆包模型不支持原生结构化输出。"""
        assert backend_no_structured._supports_native_structured is False

    async def test_fallback_uses_instructor(self, backend_no_structured):
        """模型不支持原生时走 Instructor 降级路径。"""
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
        """模型支持原生时走 response_format 路径。"""
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
        """未注册模型保守降级为 Instructor。"""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k", model="unknown-model-xyz")
        assert b._supports_native_structured is False
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_text_backends/test_ark.py::TestCapabilityAwareStructured -v`
Expected: FAIL — `AttributeError: 'ArkTextBackend' object has no attribute '_supports_native_structured'`

- [ ] **Step 3: 实现 ArkTextBackend 改造**

修改 `lib/text_backends/ark.py`。在 `__init__` 中添加能力判断，在 `_generate_structured` 中添加分流：

```python
"""ArkTextBackend — 火山方舟文本生成后端。"""
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
    """Ark (火山方舟) 文本生成后端。"""

    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None):
        from volcenginesdkarkruntime import Ark

        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("Ark API Key 未提供")

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
        """检查当前模型是否支持原生结构化输出。"""
        from lib.config.registry import PROVIDER_REGISTRY

        provider_meta = PROVIDER_REGISTRY.get("ark")
        if provider_meta:
            model_info = provider_meta.models.get(self._model)
            if model_info:
                return "structured_output" in model_info.capabilities
        # 未注册模型保守降级
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

    # _generate_vision, _build_messages, _parse_chat_response 保持不变
```

Note: `_generate_vision`（lines 86-119）、`_build_messages`（lines 121-126）、`_parse_chat_response`（lines 128-138）保持不变，不在此处重复。

- [ ] **Step 4: 更新现有 capabilities 测试**

`tests/test_text_backends/test_ark.py` 中的 `TestProperties.test_capabilities` 需要更新，因为默认模型不再有 `STRUCTURED_OUTPUT`：

```python
# 修改 TestProperties.test_capabilities (line 28-34):
    def test_capabilities(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.VISION,
        }
```

- [ ] **Step 5: 运行全部 ark 测试确认通过**

Run: `uv run python -m pytest tests/test_text_backends/test_ark.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add lib/text_backends/ark.py tests/test_text_backends/test_ark.py
git commit -m "feat: ArkTextBackend 支持基于模型能力的结构化输出降级"
```

---

### Task 4: 修正 ProjectManager + 全量回归

**Files:**
- Modify: `lib/project_manager.py:1596`

- [ ] **Step 1: 修改 response_schema 传入方式**

在 `lib/project_manager.py:1596`，将 `.model_json_schema()` 调用改为直接传 Pydantic 类：

```python
# 修改前 (line 1593-1597):
        result = await generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview.model_json_schema(),
            ),

# 修改后:
        result = await generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview,
            ),
```

- [ ] **Step 2: 运行全量测试回归**

Run: `uv run python -m pytest -v`
Expected: all PASS

- [ ] **Step 3: 提交**

```bash
git add lib/project_manager.py
git commit -m "fix: ProjectManager 传 Pydantic 类替代 JSON Schema dict 作为 response_schema"
```
