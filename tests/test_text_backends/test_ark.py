"""ArkTextBackend tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lib.text_backends.ark import ArkTextBackend
from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult


@pytest.fixture
def mock_ark():
    with patch("lib.text_backends.ark.Ark", create=True) as MockArk:
        # Also patch the import inside __init__
        with patch.dict("sys.modules", {"volcenginesdkarkruntime": MagicMock(Ark=MockArk)}):
            yield MockArk


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

    def test_no_api_key_raises(self, mock_ark):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API Key"):
                ArkTextBackend()


class TestGenerate:
    @pytest.fixture
    def backend(self, mock_ark):
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
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
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
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
