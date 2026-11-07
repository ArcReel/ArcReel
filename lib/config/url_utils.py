"""URL normalization helpers."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

# Official OpenAI hosts both classify is_official_openai_base_url and supply explicit
# factory defaults without base_url. AsyncOpenAI otherwise falls back to OPENAI_BASE_URL;
# explicit official values disable that fallback. Sole authority,
# never scattered literals.
OFFICIAL_OPENAI_HOSTNAME = "api.openai.com"
OFFICIAL_OPENAI_BASE_URL = f"https://{OFFICIAL_OPENAI_HOSTNAME}/v1"

# Claude CLI SDK always appends this suffix, shared by probes/previews/runtime.
# Sole unambiguous removable complete Messages endpoint suffix copied from docs.
ANTHROPIC_MESSAGES_PATH = "/v1/messages"

# Official Anthropic hosts disable SDK ANTHROPIC_BASE_URL fallback when base_url is empty,
# like OFFICIAL_OPENAI_BASE_URL.
OFFICIAL_ANTHROPIC_BASE_URL = "https://api.anthropic.com"


def is_official_openai_base_url(base_url: str | None) -> bool:
    """Whether compatible base_url targets official api.openai.com.

    Official endpoints deprecate max_tokens, rejected by reasoning o-series/gpt-5 models;
    use max_completion_tokens. Third-party compatible vLLM/relays vary in support,
    conservatively retaining max_tokens.

    base_url comes only from DB; None/blank means official.

    Known limitation: proxies/relays always count nonofficial, so blindly forwarded max_tokens
    still fails official reasoning models with explicit self-explanatory 400.
    """
    effective = (base_url or "").strip()
    if not effective:
        return True
    # hostname lowercases/removes ports; schemaless None conservatively means nonofficial.
    return urlparse(effective).hostname == OFFICIAL_OPENAI_HOSTNAME


def ensure_openai_base_url(url: str | None) -> str | None:
    """Append missing compatible /v1 paths.

    Users may supply ``https://api.example.com`` while SDK expects
    ``https://api.example.com/v1``; append absent versions.
    """
    if not url:
        return url
    stripped = url.strip().rstrip("/")
    if not re.search(r"/v\d+$", stripped):
        stripped += "/v1"
    return stripped


def normalize_base_url(url: str | None) -> str | None:
    """Ensure base_url ends with /.

    Google genai http_options.base_url requires trailing slashes for successful path composition.
    Used by built-in Gemini backends.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.endswith("/"):
        url += "/"
    return url


def ensure_google_base_url(url: str | None) -> str | None:
    """Normalize Google genai base_url.

    SDK appends ``api_version`` (default ``v1beta``).
    User ``https://example.com/v1beta`` would duplicate into
    ``https://example.com/v1beta/v1beta/models`` and fail.

    Strip final versions such as ``/v1beta`` / ``/v1``, ensuring trailing ``/``.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    url = url.rstrip("/")
    # Strip final /v1 /v1beta /v1alpha versions.
    # Use [a-zA-Z], not \w: overlapping \d+\w* triggers CodeQL polynomial-regex warnings.
    url = re.sub(r"/v\d+[a-zA-Z]*$", "", url)
    if not url.endswith("/"):
        url += "/"
    return url


#: Override ``strip_version`` suffixes ``/v1`` / ``/v2beta`` / ``/v1.5`` use ASCII digits, matching frontend
#: ``RequestAddresses`` ``VERSION_SUFFIX``: JS ``\d`` is ASCII-only, Python includes fullwidth digits.
_VERSION_SUFFIX = re.compile(r"/v\d+(?:\.\d+)?[a-zA-Z]*$", re.ASCII)


def strip_base_url_version(base_url: str) -> str:
    """Strip final versions under ``strip_version``; callers add schemes/remove trailing slashes first."""
    return _VERSION_SUFFIX.sub("", base_url)


class InvalidAnthropicBaseUrlError(ValueError):
    """Agent base_url with unsupported query/fragment/userinfo or nonabsolute/non-http(s) addresses."""


def normalize_anthropic_base_url(raw: str) -> str:
    """Normalize Anthropic addresses so stored values equal call values.

    Only trim surrounding whitespace/trailing slashes and exact final ``/v1/messages``,
    the sole unambiguous copied complete endpoint.

    Never strip ``/vN`` / bare ``/messages`` or recognize/append ``/anthropic`` subpaths.
    Claude CLI SDK concatenates base_url + ``/v1/messages`` literally; guesses would split
    successful tests from runtime 404.
    """
    stripped = raw.strip().rstrip("/")
    if stripped.endswith(ANTHROPIC_MESSAGES_PATH):
        # Remove trailing slashes again after suffix stripping: ``https://x//v1/messages`` must store ``https://x``.
        stripped = stripped[: -len(ANTHROPIC_MESSAGES_PATH)].rstrip("/")
    return stripped


def validate_anthropic_base_url(raw: str) -> str:
    """Normalize/validate Agent base_url into shared persistence/call values.

    Raises:
        InvalidAnthropicBaseUrlError: Query/fragment/userinfo or absent scheme/host.
            Fixed messages never echo inputs, protecting embedded credentials from logs/responses.
    """
    normalized = normalize_anthropic_base_url(raw)
    if not normalized:
        raise InvalidAnthropicBaseUrlError("base_url is empty")
    # Reject characters, not nonempty parsed queries/fragments: empty ``https://x/a?``
    # still yields CLI ``https://x/a?/v1/messages``; matches frontend rejection exactly.
    if "?" in normalized or "#" in normalized:
        raise InvalidAnthropicBaseUrlError("base_url must not carry query or fragment")
    parsed = urlparse(normalized)
    try:
        has_userinfo = bool(parsed.username or parsed.password)
        host = parsed.hostname
        _ = parsed.port  # Lazy netloc parsing raises invalid-port ValueError only here.
        httpx.URL(normalized)
    except (ValueError, httpx.InvalidURL) as exc:
        raise InvalidAnthropicBaseUrlError("base_url is not a parsable URL") from exc
    if has_userinfo:
        raise InvalidAnthropicBaseUrlError("base_url must not carry userinfo")
    if parsed.scheme not in ("http", "https") or not host:
        raise InvalidAnthropicBaseUrlError("base_url must be an absolute http(s) URL")
    return normalized


def anthropic_endpoint_url(base_url: str, path_suffix: str) -> str:
    """Append ``path_suffix`` such as ``/v1/messages`` to ``base_url``.

    Like Claude CLI SDK, concatenate literally after :func:`validate_anthropic_base_url` normalizes ``base_url`` and removes
    queries/fragments/trailing slashes. Only literal composition keeps probes/discovery/runtime identical;
    ``httpx.URL`` decoding/path rewriting would turn escaped ``%2F`` into real separators.
    """
    return base_url.rstrip("/") + path_suffix
