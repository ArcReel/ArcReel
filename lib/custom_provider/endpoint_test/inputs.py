"""Input value objects shared by three endpoint-test modes: parameters, credentials, assets.

Parameters match ``VideoGenerationRequest`` except seed: no product control sets it, trial null would only delete
that template key, leaving unreachable branches. Assets never enter libraries/projects: callers read bytes into memory,
pass them here, then discard after use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from arcreel_market_core.endpoint_definition import AssetData

#: Source names (``inputs.*.source`` enum) → list/single shape; multipart fields equal source names.
ASSET_SOURCES: Mapping[str, bool] = {
    "start_image": False,
    "end_image": False,
    "reference_images": True,
    "reference_audio_files": True,
}


@dataclass(frozen=True)
class EndpointTestParameters:
    """Parameters for one endpoint test."""

    model: str
    prompt: str
    duration_seconds: int = 5
    aspect_ratio: str = "9:16"
    resolution: str | None = None
    generate_audio: bool = True


@dataclass(frozen=True)
class EndpointTestCredentials:
    """Inline credentials. Service boundaries normalize DB lookups by ``provider_id`` into the same objects."""

    base_url: str
    api_key: str


@dataclass(frozen=True)
class EndpointTestAssets:
    """Assets grouped by source name; absent sources yield ``None``, lists yield empty lists."""

    by_source: Mapping[str, AssetData | Sequence[AssetData] | None] = field(default_factory=dict)

    def get(self, source: str) -> AssetData | Sequence[AssetData] | None:
        return self.by_source.get(source)

    def items(self, source: str) -> list[AssetData]:
        """List-source assets; empty lists for absent/single-value sources."""
        value = self.by_source.get(source)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return list(value)
        return []

    def single(self, source: str) -> AssetData | None:
        """Single-source assets; ``None`` when absent."""
        value = self.by_source.get(source)
        return value if isinstance(value, AssetData) else None
