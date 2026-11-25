"""宫格 4K 门控的分辨率取档。"""

from types import SimpleNamespace
from typing import Any

import pytest

from lib.config.resolver import ConfigResolver
from lib.script.grid.grid_resolution import grid_image_size, resolve_image_resolution
from lib.script.grid.layout import GRID_FALLBACK_RESOLUTION, large_grid_allowed


def _patch_image_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    default_resolution: str | None = None,
    raises: bool = False,
) -> list[str]:
    asked: list[str] = []

    async def _read_identity(self, project: dict | None, bucket: str, *, payload: Any = None):
        del self, project, payload
        if raises:
            raise RuntimeError("no image provider configured")
        asked.append(bucket)
        return SimpleNamespace(provider_id="gemini-aistudio", model_id="img-model"), default_resolution

    monkeypatch.setattr("lib.script.grid.grid_resolution.ImageRuntime.read_identity", _read_identity)
    return asked


async def test_reads_resolution_of_the_t2i_slot(monkeypatch: pytest.MonkeyPatch):
    asked = _patch_image_runtime(monkeypatch, default_resolution="4K")
    assert await resolve_image_resolution(ConfigResolver(None), {}) == "4K"
    assert asked == ["t2i"]


@pytest.mark.parametrize("resolution", ["4K", "2K", None])
async def test_resolution_drives_the_gate(monkeypatch: pytest.MonkeyPatch, resolution: str | None):
    _patch_image_runtime(monkeypatch, default_resolution=resolution)
    resolved = await resolve_image_resolution(ConfigResolver(None), {})
    assert large_grid_allowed(resolved) is (resolution == "4K")


async def test_resolution_failure_blocks_large_grid(monkeypatch: pytest.MonkeyPatch):
    _patch_image_runtime(monkeypatch, raises=True)
    resolved = await resolve_image_resolution(ConfigResolver(None), {})
    assert resolved is None
    assert large_grid_allowed(resolved) is False


@pytest.mark.parametrize(
    ("provider_id", "model_id", "expected"),
    [
        ("vidu", "viduq1", None),
        ("vidu", "viduq2", GRID_FALLBACK_RESOLUTION),
        ("ark", "doubao-seedream-4-5", GRID_FALLBACK_RESOLUTION),
        ("custom-1", "any-model", GRID_FALLBACK_RESOLUTION),
    ],
)
def test_unset_grid_size_follows_catalog(provider_id: str, model_id: str, expected: str | None):
    assert grid_image_size(None, provider_id, model_id) == expected


def test_configured_grid_size_is_sent_as_is():
    assert grid_image_size("4K", "vidu", "viduq1") == "4K"
