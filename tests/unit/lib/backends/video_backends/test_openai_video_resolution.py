"""Test OpenAIVideoBackend size parsing: snap to legal Sora enums by model/resolution tier, prioritizing aspect ratio.

sora-2 (base) supports only 720p; sora-2-pro at 1080p uses the exact HD tiers 1080x1920/1920x1080.
"""

from unittest.mock import MagicMock

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.openai import _SORA_LEGAL_SIZES, OpenAIVideoBackend


def _make_backend(model: str = "sora-2"):
    backend = OpenAIVideoBackend.__new__(OpenAIVideoBackend)
    backend._client = MagicMock()
    backend._model = model
    backend._capabilities = set()
    return backend


async def _capture_size(backend, **req_kwargs) -> str:
    captured: dict[str, object] = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.videos.create = fake_create
    req = VideoGenerationRequest(prompt="x", duration_seconds=4, **req_kwargs)
    with pytest.raises(RuntimeError):
        await backend.generate(req)
    size = captured.get("size")
    assert isinstance(size, str)  # size is required.
    return size


@pytest.mark.asyncio
@pytest.mark.parametrize(("aspect", "expected"), [("9:16", "720x1280"), ("16:9", "1280x720")])
async def test_standard_aspect_maps_to_exact_legal_size(tmp_path, aspect, expected):
    backend = _make_backend()
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio=aspect, resolution=None)
    assert size == expected
    assert size in _SORA_LEGAL_SIZES


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", [None, "720p", "1080p", "4K"])
async def test_resolution_does_not_break_ratio(tmp_path, resolution):
    """sora-2 (base) has only 720p: any resolution preserves the ratio at 720x1280 (clarity yields to ratio/model capability)."""
    backend = _make_backend()  # sora-2 base
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution=resolution)
    assert size == "720x1280"


@pytest.mark.asyncio
async def test_custom_resolution_value_ignored_uses_legal_size(tmp_path):
    """Custom resolution values (such as 1080x1920, not a legal Sora tier) no longer pass through as invalid size; snap to a legal tier by ratio."""
    backend = _make_backend()
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution="1080x1920")
    assert size == "720x1280"
    assert size in _SORA_LEGAL_SIZES


@pytest.mark.asyncio
async def test_size_always_set_and_legal(tmp_path):
    """Always send size as a legal enum; never omit it and let upstream determine the ratio."""
    backend = _make_backend()
    for aspect in ("9:16", "16:9", "1:1", "4:3", "21:9"):
        size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio=aspect, resolution=None)
        assert size in _SORA_LEGAL_SIZES, aspect


@pytest.mark.asyncio
@pytest.mark.parametrize(("aspect", "expected"), [("9:16", "1080x1920"), ("16:9", "1920x1080")])
async def test_sora2pro_1080p_uses_exact_high_res(tmp_path, aspect, expected):
    """sora-2-pro + 1080p returns the exact-ratio 1080p tier (fixing this PR's regression that reduced 1080 to 720p)."""
    backend = _make_backend(model="sora-2-pro")
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio=aspect, resolution="1080p")
    assert size == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", [None, "720p"])
async def test_sora2pro_default_and_720p_stay_720(tmp_path, resolution):
    """sora-2-pro with absent resolution or explicit 720p uses 720p, without an unauthorized 1080p upgrade and excess billing."""
    backend = _make_backend(model="sora-2-pro")
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution=resolution)
    assert size == "720x1280"


@pytest.mark.asyncio
async def test_sora2pro_4k_capped_to_1080p(tmp_path):
    """Sora tops out at 1080p: sora-2-pro requesting 4K caps at the exact 1080p tier, retaining the exact ratio."""
    backend = _make_backend(model="sora-2-pro")
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution="4K")
    assert size == "1080x1920"


@pytest.mark.asyncio
async def test_sora2pro_custom_short_picks_nearest_tier(tmp_path):
    """sora-2-pro custom resolution (short side 1000, closer to 1080) selects nearest tier 1080p, without incorrect downward rounding to 720p."""
    backend = _make_backend(model="sora-2-pro")
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution="1000x1778")
    assert size == "1080x1920"


@pytest.mark.asyncio
async def test_sora2_base_ignores_1080p_request(tmp_path):
    """sora-2 (base) does not support 1080p: a 1080p request still falls back to 720x1280 (clarity yields to model capability)."""
    backend = _make_backend(model="sora-2")
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution="1080p")
    assert size == "720x1280"


def test_offratio_1024_sizes_dropped():
    """1024x1792 / 1792x1024 (4:7) were removed from legal tiers; prioritizing ratio no longer produces 4:7 video."""
    assert "1024x1792" not in _SORA_LEGAL_SIZES
    assert "1792x1024" not in _SORA_LEGAL_SIZES
    assert set(_SORA_LEGAL_SIZES) == {"720x1280", "1280x720", "1080x1920", "1920x1080"}


@pytest.mark.asyncio
async def test_1080p_follows_entry_resolution_instead_of_pro_name(tmp_path, monkeypatch):
    from lib.catalog import query as catalog
    from tests.catalog_support import catalog_of, install_catalog, with_entry_facts

    entry = next(e for e in catalog.factory_snapshot().entries if e.maker == "openai" and e.name == "sora-2")
    data = catalog_of((entry,), {"openai": ("openai/sora-2",)})
    install_catalog(monkeypatch, with_entry_facts(data, "openai", "sora-2", resolutions=("720p", "1080p")))
    size = await _capture_size(
        _make_backend("sora-2"), output_path=tmp_path / "out.mp4", aspect_ratio="9:16", resolution="1080p"
    )
    assert size == "1080x1920"
