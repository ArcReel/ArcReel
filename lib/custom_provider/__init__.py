"""Custom providers."""

import re

CUSTOM_PROVIDER_PREFIX = "custom-"

#: Custom endpoint prefixes (``ce-<id>``), forbidden for built-in keys (``lib.custom_provider.
#: endpoints`` validates at import). Both share model ``endpoint`` columns/lookup entry points; prefixes define disjoint
#: namespaces and uniquely determine lookup sides.
CUSTOM_ENDPOINT_KEY_PREFIX = "ce-"


def make_provider_id(db_id: int) -> str:
    """Construct custom provider_id strings, such as 'custom-3'."""
    return f"{CUSTOM_PROVIDER_PREFIX}{db_id}"


def parse_provider_id(provider_id: str) -> int:
    """Extract DB IDs from provider_id in 'custom-3' form.

    Raises:
        ValueError: Invalid formats.
    """
    return int(provider_id.removeprefix(CUSTOM_PROVIDER_PREFIX))


def is_custom_provider(provider_id: str) -> bool:
    """Whether provider_id identifies custom providers."""
    return provider_id.startswith(CUSTOM_PROVIDER_PREFIX)


def make_endpoint_key(db_id: int) -> str:
    """Construct custom endpoint keys, such as 'ce-3'. System-assigned; share files omit keys."""
    return f"{CUSTOM_ENDPOINT_KEY_PREFIX}{db_id}"


_CUSTOM_ENDPOINT_KEY = re.compile(rf"^{re.escape(CUSTOM_ENDPOINT_KEY_PREFIX)}([1-9][0-9]*)\Z")


def parse_endpoint_key(endpoint: str) -> int:
    """Extract DB IDs from 'ce-3' endpoint keys.

    Canonical forms only: prefix + positive integers without leading zeroes. Literal keys are uniquely canonical; deletion compares
    model endpoint columns literally, so relaxed parsing would split the meaning of identical endpoints across paths.

    Raises:
        ValueError: Noncanonical forms: leading zeroes, signs, whitespace, underscores, non-ASCII digits, etc.
    """
    match = _CUSTOM_ENDPOINT_KEY.match(endpoint)
    if match is None:
        raise ValueError(f"malformed custom endpoint key: {endpoint!r}")
    return int(match.group(1))


def is_custom_endpoint(endpoint: str) -> bool:
    """Whether keys identify custom endpoints."""
    return endpoint.startswith(CUSTOM_ENDPOINT_KEY_PREFIX)
