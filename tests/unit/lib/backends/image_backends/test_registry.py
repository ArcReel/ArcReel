"""Image-backend registry tests."""

import pytest

from lib.backends.image_backends.registry import (
    _BACKEND_FACTORIES,
    create_backend,
    get_registered_backends,
    register_backend,
)


class _DummyBackend:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def clean_image_registry():
    """Registries are module globals; clear before tests and restore afterward to avoid leaking test backends."""
    saved = dict(_BACKEND_FACTORIES)
    _BACKEND_FACTORIES.clear()
    yield
    _BACKEND_FACTORIES.clear()
    _BACKEND_FACTORIES.update(saved)


def test_register_and_create():
    register_backend("dummy", _DummyBackend)
    assert get_registered_backends() == ["dummy"]
    backend = create_backend("dummy", api_key="test")
    assert backend.kwargs == {"api_key": "test"}


def test_create_unknown_raises():
    with pytest.raises(ValueError, match="Unknown image backend"):
        create_backend("nonexistent")
