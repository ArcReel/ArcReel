"""宫格 4K 门控的分辨率取档。"""

from typing import Any, cast

import pytest

from lib.config.resolver import ConfigResolver
from lib.grid.layout import large_grid_allowed
from server.services.grid_resolution import resolve_image_resolution

pytestmark = pytest.mark.unit


class _FakeResolver:
    """只实现门控用到的两个方法；记录被问到的能力槽与身份。"""

    def __init__(self, resolution: str | None = None, raises: bool = False):
        self._resolution = resolution
        self._raises = raises
        self.asked_capability: str | None = None
        self.asked_identity: tuple[str, str] | None = None

    async def resolve_image_backend(self, project: dict, payload: Any, *, capability: str):
        if self._raises:
            raise RuntimeError("no image provider configured")
        self.asked_capability = capability
        return type("Resolved", (), {"provider_id": "gemini", "model_id": "img-model"})()

    async def resolve_resolution(self, project: dict, provider_id: str, model_id: str) -> str | None:
        self.asked_identity = (provider_id, model_id)
        return self._resolution


def _as_resolver(fake: _FakeResolver) -> ConfigResolver:
    """测试边界的一次显式转换：替身只实现门控用到的两个方法，其余接口不参与本模块。"""
    return cast(ConfigResolver, fake)


async def test_reads_resolution_of_the_t2i_slot():
    resolver = _FakeResolver("4K")
    assert await resolve_image_resolution(_as_resolver(resolver), {}) == "4K"
    assert resolver.asked_capability == "t2i"
    assert resolver.asked_identity == ("gemini", "img-model")


@pytest.mark.parametrize("resolution", ["4K", "2K", None])
async def test_resolution_drives_the_gate(resolution: str | None):
    resolver = _FakeResolver(resolution)
    resolved = await resolve_image_resolution(_as_resolver(resolver), {})
    assert large_grid_allowed(resolved) is (resolution == "4K")


async def test_resolution_failure_blocks_large_grid():
    # 解析不出图像供应商时按未配置处理：门控是收紧方向，不放行大宫格
    resolver = _FakeResolver(raises=True)
    resolved = await resolve_image_resolution(_as_resolver(resolver), {})
    assert resolved is None
    assert large_grid_allowed(resolved) is False
