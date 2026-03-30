import pytest

from lib.image_backends.registry import create_backend, get_registered_backends, register_backend


class _DummyBackend:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_register_and_create(monkeypatch):
    from lib.image_backends import registry

    monkeypatch.setattr(registry, "_BACKEND_FACTORIES", {})
    register_backend("dummy", _DummyBackend)
    assert "dummy" in get_registered_backends()
    backend = create_backend("dummy", api_key="test")
    assert backend.kwargs == {"api_key": "test"}


def test_create_unknown_raises(monkeypatch):
    from lib.image_backends import registry

    monkeypatch.setattr(registry, "_BACKEND_FACTORIES", {})
    with pytest.raises(ValueError, match="Unknown image backend"):
        create_backend("nonexistent")
