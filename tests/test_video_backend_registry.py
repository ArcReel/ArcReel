import pytest

from lib.video_backends.base import VideoCapability
from lib.video_backends.registry import (
    _BACKEND_FACTORIES,
    create_backend,
    get_registered_backends,
    register_backend,
)


class _FakeBackend:
    name = "fake"
    capabilities = {VideoCapability.TEXT_TO_VIDEO}

    def __init__(self, api_key: str = "default"):
        self.api_key = api_key

    async def generate(self, request):
        pass


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_BACKEND_FACTORIES)
    _BACKEND_FACTORIES.clear()
    yield
    _BACKEND_FACTORIES.clear()
    _BACKEND_FACTORIES.update(saved)


class TestRegistry:
    def test_register_and_create(self):
        register_backend("fake", _FakeBackend)
        backend = create_backend("fake", api_key="test-key")
        assert backend.name == "fake"
        assert backend.api_key == "test-key"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown video backend"):
            create_backend("nonexistent")

    def test_get_registered_backends(self):
        register_backend("fake", _FakeBackend)
        assert "fake" in get_registered_backends()

    def test_register_overwrites(self):
        register_backend("fake", _FakeBackend)
        register_backend("fake", lambda **kw: _FakeBackend(api_key="overwritten"))
        backend = create_backend("fake")
        assert backend.api_key == "overwritten"
