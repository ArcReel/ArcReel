"""图片后端注册表测试。"""

import pytest

from lib.image_backends.registry import (
    _BACKEND_FACTORIES,
    create_backend,
    get_registered_backends,
    register_backend,
)


class _DummyBackend:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def _clean_image_registry():
    """注册表是模块级全局：清空后跑，跑完还原，避免测试用后端泄漏给其他用例。"""
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
