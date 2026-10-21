"""宫格 4K 门控的分辨率取档。"""

from typing import Any, cast

import pytest

from lib.config.resolver import ConfigResolver
from lib.script.grid.grid_resolution import grid_image_size, resolve_image_resolution
from lib.script.grid.layout import GRID_FALLBACK_RESOLUTION, large_grid_allowed


class _FakeResolver:
    """只实现门控用到的两个方法；记录被问到的能力槽与身份。"""

    def __init__(self, resolution: str | None = None, raises: bool = False):
        self._resolution = resolution
        self._raises = raises
        self.asked_generation_type: str | None = None
        self.asked_identity: tuple[str, str] | None = None

    async def resolve_image_backend(self, project: dict, payload: Any, *, generation_type: str):
        if self._raises:
            raise RuntimeError("no image provider configured")
        self.asked_generation_type = generation_type
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
    assert resolver.asked_generation_type == "t2i"
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


@pytest.mark.parametrize(
    ("provider_id", "model_id", "expected"),
    [
        ("vidu", "viduq1", None),  # 目录只声明 1080p：不发保底档，交上游默认
        ("vidu", "viduq2", GRID_FALLBACK_RESOLUTION),  # 目录含保底档
        ("ark", "doubao-seedream-4-5", GRID_FALLBACK_RESOLUTION),  # 目录未声明档位
        ("custom-1", "any-model", GRID_FALLBACK_RESOLUTION),  # 不在目录中
    ],
)
def test_unset_grid_size_follows_catalog(provider_id: str, model_id: str, expected: str | None):
    assert grid_image_size(None, provider_id, model_id) == expected


def test_configured_grid_size_is_sent_as_is():
    assert grid_image_size("4K", "vidu", "viduq1") == "4K"
