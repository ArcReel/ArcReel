"""Shared validation functions, reusable across multiple routers."""

from __future__ import annotations

from fastapi import HTTPException

from lib.config.registry import PROVIDER_REGISTRY

# Legacy provider name → new-format registry provider_id.
# Kept in sync with generation_worker._normalize_provider_id().
_LEGACY_PROVIDER_NAMES: dict[str, str] = {
    "gemini": "gemini-aistudio",
    "vertex": "gemini-vertex",
    "seedance": "ark",
}


def validate_backend_value(value: str, field_name: str) -> None:
    """Validate a backend field value in ``provider/model`` format.

    Also accepts legacy single-provider names (e.g. ``"gemini"``) for
    compatibility with existing projects.

    Raises:
        HTTPException(400): Format is invalid or provider is not in the registry.
    """
    if "/" not in value:
        if value in _LEGACY_PROVIDER_NAMES or value in PROVIDER_REGISTRY:
            return  # Legacy format or bare registry id; handled downstream by _normalize_provider_id()
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} format should be provider/model",
        )
    provider_id = value.split("/", 1)[0]
    if provider_id not in PROVIDER_REGISTRY and not provider_id.startswith("custom-"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider_id}",
        )
