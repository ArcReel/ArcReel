"""TextBackend Protocol + data classes tests."""

from pathlib import Path

from lib.text_backends.base import (
    ImageInput,
    TextBackend,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
    resolve_schema,
)


class TestTextCapability:
    def test_values(self):
        assert TextCapability.TEXT_GENERATION == "text_generation"
        assert TextCapability.STRUCTURED_OUTPUT == "structured_output"
        assert TextCapability.VISION == "vision"

    def test_is_str_enum(self):
        assert isinstance(TextCapability.TEXT_GENERATION, str)


class TestTextTaskType:
    def test_values(self):
        assert TextTaskType.SCRIPT == "script"
        assert TextTaskType.OVERVIEW == "overview"
        assert TextTaskType.STYLE_ANALYSIS == "style"


class TestImageInput:
    def test_path_only(self):
        inp = ImageInput(path=Path("/tmp/img.png"))
        assert inp.path == Path("/tmp/img.png")
        assert inp.url is None

    def test_url_only(self):
        inp = ImageInput(url="https://example.com/img.png")
        assert inp.path is None
        assert inp.url == "https://example.com/img.png"


class TestTextGenerationRequest:
    def test_minimal(self):
        req = TextGenerationRequest(prompt="hello")
        assert req.prompt == "hello"
        assert req.response_schema is None
        assert req.images is None
        assert req.system_prompt is None

    def test_full(self):
        req = TextGenerationRequest(
            prompt="analyze",
            response_schema={"type": "object"},
            images=[ImageInput(path=Path("/tmp/img.png"))],
            system_prompt="You are a helpful assistant.",
        )
        assert req.response_schema == {"type": "object"}
        assert len(req.images) == 1
        assert req.system_prompt == "You are a helpful assistant."


class TestTextGenerationResult:
    def test_minimal(self):
        result = TextGenerationResult(text="output", provider="gemini", model="flash")
        assert result.text == "output"
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_with_tokens(self):
        result = TextGenerationResult(
            text="output",
            provider="ark",
            model="seed",
            input_tokens=100,
            output_tokens=50,
        )
        assert result.input_tokens == 100
        assert result.output_tokens == 50


class TestResolveSchema:
    def test_dict_without_refs_unchanged(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert resolve_schema(schema) == schema

    def test_inlines_refs(self):
        schema = {
            "$defs": {"Inner": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            "type": "object",
            "properties": {"child": {"$ref": "#/$defs/Inner"}},
        }
        result = resolve_schema(schema)
        assert "$defs" not in result
        assert "$ref" not in str(result)
        assert result["properties"]["child"]["properties"]["x"]["type"] == "integer"

    def test_pydantic_class(self):

        from pydantic import BaseModel

        class Item(BaseModel):
            value: int

        class Container(BaseModel):
            items: list[Item]

        result = resolve_schema(Container)
        assert "$ref" not in str(result)
        assert "$defs" not in result
        items_schema = result["properties"]["items"]["items"]
        assert items_schema["properties"]["value"]["type"] == "integer"

    def test_circular_ref_raises(self):
        schema = {
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/$defs/Node"}},
                },
            },
            "type": "object",
            "properties": {"root": {"$ref": "#/$defs/Node"}},
        }
        import pytest

        with pytest.raises(ValueError, match="循环引用"):
            resolve_schema(schema)

    def test_preserves_extra_keys_on_ref(self):
        schema = {
            "$defs": {"Inner": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            "type": "object",
            "properties": {"child": {"$ref": "#/$defs/Inner", "description": "A child"}},
        }
        result = resolve_schema(schema)
        assert result["properties"]["child"]["description"] == "A child"
        assert result["properties"]["child"]["type"] == "object"


class TestTextBackendProtocol:
    def test_satisfies_protocol(self):
        class FakeBackend:
            @property
            def name(self) -> str:
                return "fake"

            @property
            def model(self) -> str:
                return "fake-model"

            @property
            def capabilities(self) -> set[TextCapability]:
                return {TextCapability.TEXT_GENERATION}

            async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
                return TextGenerationResult(text="ok", provider="fake", model="fake-model")

        backend: TextBackend = FakeBackend()
        assert backend.name == "fake"
        assert backend.model == "fake-model"
        assert TextCapability.TEXT_GENERATION in backend.capabilities
