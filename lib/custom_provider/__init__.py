"""Custom provider module."""

CUSTOM_PROVIDER_PREFIX = "custom-"


def make_provider_id(db_id: int) -> str:
    """Construct a provider_id string for a custom provider, e.g. 'custom-3'."""
    return f"{CUSTOM_PROVIDER_PREFIX}{db_id}"


def parse_provider_id(provider_id: str) -> int:
    """Extract the database ID from a provider_id in 'custom-3' format.

    Raises:
        ValueError: if the format is incorrect
    """
    return int(provider_id.removeprefix(CUSTOM_PROVIDER_PREFIX))


def is_custom_provider(provider_id: str) -> bool:
    """Return True if the provider_id belongs to a custom provider."""
    return provider_id.startswith(CUSTOM_PROVIDER_PREFIX)
