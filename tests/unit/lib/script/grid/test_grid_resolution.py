"""Resolution-tier selection for grid 4K gates."""

from typing import Any, cast

import pytest

from lib.config.resolver import ConfigResolver
from lib.script.grid.grid_resolution import grid_image_size, resolve_image_resolution
from lib.script.grid.layout import GRID_FALLBACK_RESOLUTION, large_grid_allowed


class _FakeResolver:
    """Implement only the gate's two methods, recording queried capability slots and identities."""

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
    """One explicit test-boundary conversion: the substitute implements only two gate methods; other interfaces are outside this module."""
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
    # Unresolved image providers count as unconfigured; this narrowing gate must not allow large grids.
    resolver = _FakeResolver(raises=True)
    resolved = await resolve_image_resolution(_as_resolver(resolver), {})
    assert resolved is None
    assert large_grid_allowed(resolved) is False


@pytest.mark.parametrize(
    ("provider_id", "model_id", "expected"),
    [
        ("vidu", "viduq1", None),  # Catalog declares only 1080p: omit fallback and leave upstream default.
        ("vidu", "viduq2", GRID_FALLBACK_RESOLUTION),  # Catalog includes the fallback tier.
        ("ark", "doubao-seedream-4-5", GRID_FALLBACK_RESOLUTION),  # Catalog declares no tiers.
        ("custom-1", "any-model", GRID_FALLBACK_RESOLUTION),  # Not in the catalog.
    ],
)
def test_unset_grid_size_follows_catalog(provider_id: str, model_id: str, expected: str | None):
    assert grid_image_size(None, provider_id, model_id) == expected


def test_configured_grid_size_is_sent_as_is():
    assert grid_image_size("4K", "vidu", "viduq1") == "4K"
