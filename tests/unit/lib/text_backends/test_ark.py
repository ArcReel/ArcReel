"""ArkTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.text_backends.ark import ArkTextBackend
from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult


@pytest.fixture
def mock_ark():
    mock_client = MagicMock()
    with patch("lib.text_backends.ark.create_ark_client", return_value=mock_client) as mock_create:
        yield mock_create, mock_client


class TestProperties:
    def test_name(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.name == "ark"

    def test_default_model(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.model == "doubao-seed-2-0-lite-260215"

    def test_capabilities(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.VISION,
        }

    def test_no_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API Key"):
                ArkTextBackend()


class TestGenerate:
    @pytest.fixture
    def backend(self, mock_ark):
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    async def test_plain_text(self, backend, sync_to_thread):
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  ark output  "))],
            usage=SimpleNamespace(prompt_tokens=15, completion_tokens=8),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "ark output"
        assert result.provider == "ark"
        assert result.input_tokens == 15
        assert result.output_tokens == 8


class TestVision:
    async def test_vision_uses_chat_completions(self, mock_ark, sync_to_thread):
        """vision 路径走 chat.completions.create，与 plain 共用响应解析。"""
        from lib.text_backends.base import ImageInput

        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  style description  "))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )
        mock_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await b.generate(
            TextGenerationRequest(prompt="describe style", images=[ImageInput(url="https://example.com/img.jpg")])
        )

        assert result.text == "style description"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        # 确认走的是 chat.completions 而不是 responses API
        mock_client.chat.completions.create.assert_called_once()

    async def test_vision_message_format(self, mock_ark, sync_to_thread):
        """vision 请求构建 image_url 格式的多模态消息。"""
        from lib.text_backends.base import ImageInput

        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        mock_client.chat.completions.create = MagicMock(return_value=mock_resp)

        await b.generate(
            TextGenerationRequest(
                prompt="describe",
                system_prompt="you are helpful",
                images=[ImageInput(url="https://example.com/img.jpg")],
            )
        )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "you are helpful"}
        user_content = messages[1]["content"]
        assert user_content[0] == {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}
        assert user_content[1] == {"type": "text", "text": "describe"}


class TestCapabilityAwareStructured:
    """测试基于模型能力的结构化输出路径选择。"""

    @pytest.fixture
    def backend_no_structured(self, mock_ark):
        """创建一个模型不支持原生 structured_output 的 backend。"""
        _, mock_client = mock_ark
        # 使用默认模型 doubao-seed-2-0-lite-260215，registry 中已移除 structured_output
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    @pytest.fixture
    def backend_with_structured(self, mock_ark):
        """创建一个模型支持原生 structured_output 的 backend（模拟）。"""
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        # 手动添加原生结构化输出能力
        b._capabilities.add(TextCapability.STRUCTURED_OUTPUT)
        return b

    async def test_default_model_does_not_support_native_structured(self, backend_no_structured):
        """默认豆包模型不支持原生结构化输出。"""
        assert TextCapability.STRUCTURED_OUTPUT not in backend_no_structured.capabilities

    async def test_fallback_uses_instructor(self, backend_no_structured, sync_to_thread):
        """模型不支持原生时走 Instructor 降级路径。"""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            key: str

        sample = TestModel(key="value")

        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=(sample.model_dump_json(), 50, 20),
        ) as mock_instructor:
            result = await backend_no_structured.generate(
                TextGenerationRequest(prompt="gen", response_schema=TestModel)
            )

            mock_instructor.assert_called_once()
            assert result.text == '{"key":"value"}'
            assert result.input_tokens == 50
            assert result.output_tokens == 20

    async def test_native_path_when_supported(self, backend_with_structured, sync_to_thread):
        """模型支持原生时走 response_format 路径。"""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await backend_with_structured.generate(TextGenerationRequest(prompt="gen", response_schema=schema))

        assert result.text == '{"key": "value"}'
        call_args = backend_with_structured._test_client.chat.completions.create.call_args
        assert "response_format" in call_args.kwargs

    async def test_unknown_model_falls_back_to_instructor(self, mock_ark):
        """未注册模型保守降级为 Instructor。"""
        b = ArkTextBackend(api_key="k", model="unknown-model-xyz")
        assert TextCapability.STRUCTURED_OUTPUT not in b.capabilities

    async def test_dict_schema_fallback_uses_json_object(self, backend_no_structured, sync_to_thread):
        """dict schema 走 json_object 降级路径。"""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=15),
        )
        backend_no_structured._openai_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await backend_no_structured.generate(
            TextGenerationRequest(prompt="gen", response_schema={"type": "object"})
        )

        assert result.text == '{"key": "value"}'
        call_args = backend_no_structured._openai_client.chat.completions.create.call_args
        assert call_args.kwargs["response_format"] == {"type": "json_object"}

    async def test_truncation_warning_logged_on_finish_reason_length(
        self, backend_no_structured, sync_to_thread, caplog
    ):
        """当 Ark 返回 finish_reason=length 时应记录 WARNING。"""
        import logging

        mock_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="partial"),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=8192),
        )
        backend_no_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with caplog.at_level(logging.WARNING, logger="lib.text_backends.base"):
            await backend_no_structured.generate(TextGenerationRequest(prompt="hi"))

        assert any("被截断" in r.message for r in caplog.records)

    async def test_native_structured_truncation_raises_and_skips_instructor_fallback(
        self, backend_with_structured, sync_to_thread
    ):
        """原生 structured 通道截断直接抛 TextOutputTruncatedError，不降级到 Instructor 重试。"""
        from lib.text_backends.base import TextOutputTruncatedError

        mock_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="partial"),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=8192),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with patch("lib.text_backends.instructor_support.instructor_fallback_sync") as mock_fallback:
            with pytest.raises(TextOutputTruncatedError) as exc_info:
                await backend_with_structured.generate(
                    TextGenerationRequest(prompt="gen", response_schema={"type": "object"})
                )
            mock_fallback.assert_not_called()

        assert exc_info.value.provider == "ark"
        assert exc_info.value.model == "mock-model-with-structured"

    async def test_max_output_tokens_plain(self, backend_no_structured, sync_to_thread):
        """plain 路径透传 max_tokens。"""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        backend_no_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)
        await backend_no_structured.generate(TextGenerationRequest(prompt="hi", max_output_tokens=16000))
        call_kwargs = backend_no_structured._test_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 16000
        assert "max_completion_tokens" not in call_kwargs

    async def test_max_output_tokens_structured_native(self, backend_with_structured, sync_to_thread):
        """原生 structured 路径透传 max_tokens。"""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"a":1}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        backend_with_structured._test_client.chat.completions.create = MagicMock(return_value=mock_resp)
        await backend_with_structured.generate(
            TextGenerationRequest(prompt="g", response_schema={"type": "object"}, max_output_tokens=20000)
        )
        call_kwargs = backend_with_structured._test_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 20000
        assert "max_completion_tokens" not in call_kwargs

    async def test_max_output_tokens_instructor_fallback(self, backend_no_structured, sync_to_thread):
        """Instructor 降级路径透传 max_tokens。"""
        from pydantic import BaseModel

        class M(BaseModel):
            k: str

        sample = M(k="v")
        with patch(
            "lib.text_backends.instructor_support.generate_structured_via_instructor",
            return_value=(sample.model_dump_json(), 1, 1),
        ) as mock_fn:
            await backend_no_structured.generate(
                TextGenerationRequest(prompt="g", response_schema=M, max_output_tokens=24000)
            )
            assert mock_fn.call_args.kwargs["max_tokens"] == 24000
            assert mock_fn.call_args.kwargs["token_param"] == "max_tokens"

    async def test_instructor_fallback_wire_param_is_max_tokens(self, backend_no_structured, sync_to_thread):
        """导线级守护：Ark 降级链路最终发给端点的参数名必须是 max_tokens。"""
        from pydantic import BaseModel

        class M(BaseModel):
            k: str

        sample = M(k="v")
        completion = SimpleNamespace(usage=None)
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(sample, completion))

        with patch("instructor.from_openai", return_value=mock_patched):
            await backend_no_structured.generate(
                TextGenerationRequest(prompt="g", response_schema=M, max_output_tokens=24000)
            )

        call_kwargs = mock_patched.chat.completions.create_with_completion.call_args.kwargs
        assert call_kwargs["max_tokens"] == 24000
        assert "max_completion_tokens" not in call_kwargs

    async def test_native_failure_falls_back(self, backend_with_structured, sync_to_thread):
        """原生 json_schema 运行时失败后降级到 json_object。"""
        backend_with_structured._test_client.chat.completions.create = MagicMock(
            side_effect=Exception("schema not supported")
        )
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": 1}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend_with_structured._openai_client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = await backend_with_structured.generate(
            TextGenerationRequest(prompt="gen", response_schema={"type": "object"})
        )

        assert result.text == '{"a": 1}'
        # 原生路径应该被尝试过
        backend_with_structured._test_client.chat.completions.create.assert_called_once()
        # 降级路径应该被使用
        backend_with_structured._openai_client.chat.completions.create.assert_called_once()


class TestSuccessPathReverify:
    """原生 200 后复验 schema：违例则降级带校验路径，并合并原生计费 token。"""

    @pytest.fixture
    def backend(self, mock_ark):
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        b._capabilities.add(TextCapability.STRUCTURED_OUTPUT)
        return b

    async def test_violating_json_pydantic_triggers_fallback_with_token_merge(self, backend, sync_to_thread):
        """原生 200 + 违反 Pydantic schema 的 JSON → 降级 + token 合并。"""
        import json

        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        violating = json.dumps({"name": "Alice", "age": "三十"}, ensure_ascii=False)
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=violating))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=60),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Alice", age=30)
        completion = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30))
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patch("instructor.from_openai", return_value=mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == instructor_result.model_dump_json()
        # 原生 200 调用（100/60）已被代理计费，与 Instructor 调用（80/30）合并
        assert result.input_tokens == 180
        assert result.output_tokens == 90
        backend._test_client.chat.completions.create.assert_called_once()

    async def test_non_json_triggers_fallback(self, backend, sync_to_thread):
        """原生 200 + 非 JSON（代理静默忽略 response_format）→ 降级。"""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="## markdown not json"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Bob", age=25)
        completion = SimpleNamespace(usage=None)
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patch("instructor.from_openai", return_value=mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == instructor_result.model_dump_json()
        # Instructor usage 为 None，结果 token 即原生 200 调用计费量（10/5）
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    async def test_both_usages_none_preserves_none_not_zero(self, backend, sync_to_thread):
        """原生与 Instructor 计量皆 None → 合并后保持 None（未追踪），不塌成字面 0。"""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        # 原生 200 返回非 JSON 触发降级，且原生 usage 缺失
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="## markdown not json"))],
            usage=None,
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Bob", age=25)
        completion = SimpleNamespace(usage=None)
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patch("instructor.from_openai", return_value=mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == instructor_result.model_dump_json()
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_valid_json_satisfying_schema_no_fallback(self, backend, sync_to_thread):
        """原生 200 + 满足 schema 的 JSON → 直接采用，不降级。"""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        import json

        valid = json.dumps({"name": "Alice", "age": 30})
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=valid))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with patch("instructor.from_openai") as mock_from_openai:
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == valid
        assert result.input_tokens == 20
        assert result.output_tokens == 10
        mock_from_openai.assert_not_called()

    async def test_dict_schema_non_json_falls_back_to_json_object_with_token_merge(self, backend, sync_to_thread):
        """dict schema 原生 200 返回非 JSON → json_object 降级，并合并原生 token。"""
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        fb = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"key": "value"}'))],
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=15),
        )
        backend._openai_client.chat.completions.create = MagicMock(return_value=fb)

        result = await backend.generate(TextGenerationRequest(prompt="x", response_schema={"type": "object"}))

        assert result.text == '{"key": "value"}'
        # 原生 10/5 与 json_object 降级 30/15 合并
        assert result.input_tokens == 40
        assert result.output_tokens == 20
        call_kwargs = backend._openai_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    async def test_dict_schema_valid_json_no_fallback(self, backend, sync_to_thread):
        """dict schema 原生 200 + 合法 JSON（即便违反声明类型）→ 不降级。"""
        import json

        violating = json.dumps({"key": "value", "age": "thirty"})
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=violating))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)
        backend._openai_client.chat.completions.create = MagicMock()

        result = await backend.generate(
            TextGenerationRequest(
                prompt="x",
                response_schema={"type": "object", "properties": {"age": {"type": "integer"}}},
            )
        )

        assert result.text == violating
        backend._openai_client.chat.completions.create.assert_not_called()

    async def test_coercible_value_does_not_trigger_fallback(self, backend, sync_to_thread):
        """原生 200 + 可强转值（int 字段给 "30"）→ 不降级。

        ark 原生 response_format 未声明 strict，复验同样 strict=False，容忍可强转值，
        避免对供应商已接受的合法响应触发多余的计费降级调用。
        """
        import json

        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        coercible = json.dumps({"name": "Alice", "age": "30"})
        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=coercible))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with patch("instructor.from_openai") as mock_from_openai:
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        assert result.text == coercible
        assert result.input_tokens == 20
        assert result.output_tokens == 10
        mock_from_openai.assert_not_called()

    async def test_empty_choices_on_200_falls_back(self, backend, sync_to_thread):
        """原生 200 但 choices 为空（中转/内容审核）→ 解析异常被 catch → 降级，不逃逸。"""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        mock_resp = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        instructor_result = Person(name="Bob", age=25)
        completion = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30))
        mock_patched = MagicMock()
        mock_patched.chat.completions.create_with_completion = MagicMock(return_value=(instructor_result, completion))

        with patch("instructor.from_openai", return_value=mock_patched):
            result = await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        # 解析异常落入 except → 降级路径，返回带校验结果（不并入原生 token：原生未成功解析）
        assert result.text == instructor_result.model_dump_json()
        assert result.input_tokens == 80
        assert result.output_tokens == 30

    async def test_fallback_transient_error_does_not_replay_native_call(self, backend, sync_to_thread):
        """降级路径瞬态错误只在降级层重试，不重放已成功（已计费）的原生调用。"""
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str
            age: int

        mock_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="非 JSON 散文"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        backend._test_client.chat.completions.create = MagicMock(return_value=mock_resp)

        with (
            patch(
                "lib.text_backends.instructor_support.instructor_fallback_sync",
                side_effect=ConnectionError("503 service unavailable"),
            ) as mock_instructor,
            patch("lib.retry.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(ConnectionError):
                await backend.generate(TextGenerationRequest(prompt="x", response_schema=Person))

        # 原生 200 调用只发生一次；降级层自身重试 3 次穷尽
        backend._test_client.chat.completions.create.assert_called_once()
        assert mock_instructor.call_count == 3


class TestBaseUrl:
    def test_custom_base_url_passes_to_both_clients(self):
        with patch("lib.text_backends.ark.create_ark_client") as mock_ark_create:
            with patch("lib.text_backends.ark.OpenAI") as mock_openai_ctor:
                ArkTextBackend(api_key="k", base_url="https://ark.cn-beijing.volces.com/api/plan/v3")
                mock_ark_create.assert_called_once_with(
                    api_key="k",
                    base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
                )
                mock_openai_ctor.assert_called_once_with(
                    base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
                    api_key="k",
                )

    def test_default_base_url_keeps_ark_v3(self):
        from lib.ark_shared import ARK_BASE_URL

        with patch("lib.text_backends.ark.create_ark_client") as mock_ark_create:
            with patch("lib.text_backends.ark.OpenAI") as mock_openai_ctor:
                ArkTextBackend(api_key="k")
                mock_ark_create.assert_called_once_with(api_key="k", base_url=ARK_BASE_URL)
                mock_openai_ctor.assert_called_once_with(base_url=ARK_BASE_URL, api_key="k")
