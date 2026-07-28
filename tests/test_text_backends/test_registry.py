"""Text backend registry tests."""

import pytest

from lib.text_backends.base import TextCapability, TextGenerationResult
from lib.text_backends.registry import (
    _BACKEND_FACTORIES,
    create_backend,
    get_registered_backends,
    register_backend,
)


class FakeTextBackend:
    def __init__(self, *, api_key=None, model=None):
        self._model = model or "fake-model"

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return self._model

    @property
    def capabilities(self):
        return {TextCapability.TEXT_GENERATION}

    async def generate(self, request):
        return TextGenerationResult(text="ok", provider="fake", model=self._model)


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_BACKEND_FACTORIES)
    _BACKEND_FACTORIES.clear()
    yield
    _BACKEND_FACTORIES.clear()
    _BACKEND_FACTORIES.update(saved)


class TestRegistry:
    def test_register_and_create(self):
        register_backend("fake", FakeTextBackend)
        backend = create_backend("fake", api_key="k")
        assert backend.name == "fake"
        assert backend.model == "fake-model"

    def test_create_with_model_override(self):
        register_backend("fake", FakeTextBackend)
        backend = create_backend("fake", model="custom-model")
        assert backend.model == "custom-model"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown text backend"):
            create_backend("nonexistent")

    def test_get_registered_backends(self):
        register_backend("a", FakeTextBackend)
        register_backend("b", FakeTextBackend)
        assert sorted(get_registered_backends()) == ["a", "b"]
