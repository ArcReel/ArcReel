"""LoadedConfig: async loading output and sole sync construction input for built-in backends.

Carries three things: ① credential overlays (api_key / access_key / secret_key /
base_url etc. resolved from db_config; keys equal column names, following ADR 0037: registry key name = column name = constructor argument name = config key);
② provider registry meta (source of default_base_url / api_model_name); ③ shared rate_limiter.

Construction closures only read this envelope to assemble backends, without DB queries or await. This boundary structurally
ensures construction can be unit-tested without DB access (manually supply LoadedConfig + model_id and assert constructed arguments).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.config.registry import ProviderMeta


@dataclass(frozen=True)
class LoadedConfig:
    """Sole input envelope for built-in build closures."""

    credentials: dict[str, str | None]
    provider_meta: ProviderMeta | None
    # Simple-family _build_simple ignores rate_limiter, matching former _fill_simple_provider_kwargs: rate limiting is
    # built into each simple backend's httpx client. Reserve this slot for future migrated special-family build closures, such as gemini media.
    rate_limiter: Any | None
    # Effective credential record ID overlaid into credentials; None for credentials from provider configuration rather than records. Channels report
    # the actual inference credential through it, letting the asset library select provider materials by its bindings (lib.backends.provider_assets).
    credential_id: int | None = None
