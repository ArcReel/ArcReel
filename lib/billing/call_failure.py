"""Machine-stable provider-call failure encoding for accounting.

Failed ``api_calls`` persist raw ``error_message`` plus codes/parameters, allowing read-side localization in current
languages, including historical failures after language switches. Unknown exceptions retain raw text only, leaving ``error_code`` /
``error_params`` empty for unchanged display.

Do **not share enums** with :mod:`lib.generation.task_failure`: those codes require server translations in ``lib/i18n`` errors
via mandatory ``FAILURE_CODE_KEYS`` registration, rendered in task read APIs; these four codes classify
provider failures for frontend rendering. Shared enums would force each side to register the other's codes.

Reuse ``[code] {json}`` serialization concepts: machine codes plus JSON-native parameter dictionaries.
Existing ``api_calls`` ``error_code`` / ``error_params`` columns store halves separately, avoiding concatenation
and reparsing by readers.

Classify exception types/structured attributes (HTTP status, upstream ``code``), never guessed message text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from openai import APITimeoutError

from lib.backends.http_status_errors import ArtifactDownloadError


class CallErrorCode(StrEnum):
    """Closed machine-code set for failed calls, rendered as phrases by readers."""

    RATE_LIMITED = "rate_limited"
    CONTENT_POLICY = "content_policy"
    TIMEOUT = "timeout"
    DOWNLOAD_FAILED = "download_failed"
    # Process restarts interrupt pending calls without classifiable exceptions; startup writes missing-task-identity failures.
    INTERRUPTED = "interrupted"


# Upstream policy-blocking ``code`` values. OpenAI-compatible official/Azure/relay protocols put them
# in error-body code, attached by SDKs: machine fields, not message text.
CONTENT_POLICY_PROVIDER_CODES: frozenset[str] = frozenset(
    {
        "content_filter",
        "content_policy_violation",
        "moderation_blocked",
    }
)

# Statuses themselves mean excessive waiting: 408 request timeout, 504 gateway timeout.
_TIMEOUT_STATUS_CODES: frozenset[int] = frozenset({408, 504})

_RATE_LIMIT_STATUS_CODE = 429

# Stable artifact-download-exhaustion codes: built-ins raise ``ArtifactDownloadError``, declarative backends raise
# ``DeclarativeRuntimeError`` with identical ``code``. Code-based classification unifies both chains.
_ARTIFACT_DOWNLOAD_FAILED_CODE = ArtifactDownloadError.code


@dataclass(frozen=True, slots=True)
class CallFailure:
    """Persisted failures: raw text + code + parameters. Unknown exceptions have raw text only."""

    error_message: str
    error_code: str | None = None
    error_params: dict[str, Any] | None = None


def classify_call_failure(exc: BaseException | str) -> CallFailure:
    """Encode exceptions caught by accounting scopes into persisted triples.

    Order is priority: download failures first, although root causes may be timeouts/HTTP statuses, because users need
    retryable retrieval failures rather than timed-out generation. Other three classes do not overlap; traverse ``__cause__``:
    outer ambiguous-submission wrappers may lack classifiable information while wrapped root causes provide it.
    ``error_message`` always retains outermost text.

    Also accept raw failure strings: some resume accounting paths have no exception objects. Classification depends on
    types/attributes, absent from text, so preserve raw text only, matching unknown exceptions.
    """
    if isinstance(exc, str):
        return CallFailure(exc)
    message = str(exc)
    if _provider_error_code(exc) == _ARTIFACT_DOWNLOAD_FAILED_CODE:
        return CallFailure(message, CallErrorCode.DOWNLOAD_FAILED, _download_params(exc))
    for candidate in _cause_chain(exc):
        classified = _classify_single(candidate)
        if classified is not None:
            code, params = classified
            return CallFailure(message, code, params)
    return CallFailure(message)


def _classify_single(exc: BaseException) -> tuple[CallErrorCode, dict[str, Any]] | None:
    status = _http_status(exc)
    if status == _RATE_LIMIT_STATUS_CODE:
        # 429 covers openai.RateLimitError with response attributes and google ResourceExhausted
        # with exception code attributes, without enumerating SDK types.
        return CallErrorCode.RATE_LIMITED, _retry_after_params(exc)
    if _provider_error_code(exc) in CONTENT_POLICY_PROVIDER_CODES:
        return CallErrorCode.CONTENT_POLICY, {}
    if isinstance(exc, TimeoutError | httpx.TimeoutException | APITimeoutError) or status in _TIMEOUT_STATUS_CODES:
        # TimeoutError covers built-in/asyncio aliases, including video polling; httpx/openai timeouts
        # have distinct bases, not TimeoutError subclasses, requiring explicit inclusion.
        return CallErrorCode.TIMEOUT, {}
    return None


def _cause_chain(exc: BaseException) -> list[BaseException]:
    """Explicit outer-to-inner ``raise ... from`` chains; implicit ``__context__`` excluded."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__
    return chain


def _download_params(exc: BaseException) -> dict[str, Any]:
    """Download-failure parameters, including HTTP status if discoverable.

    Download exceptions carry detail text only; wrapped download failures (``raise ... from exc``) carry statuses,
    found through ``__cause__`` chains.
    """
    status = _http_status_in_chain(exc)
    return {} if status is None else {"status": status}


def _retry_after_params(exc: BaseException) -> dict[str, Any]:
    """Rate-limit parameters: wait seconds from integer-second ``Retry-After`` responses."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return {}
    raw = headers.get("Retry-After")
    # Never parse HTTP-date Retry-After: readers need seconds, whose conversion requires response times
    # unavailable reliably here.
    if not isinstance(raw, str) or not raw.strip().isdigit():
        return {}
    return {"retry_after_seconds": int(raw.strip())}


def _http_status(exc: BaseException) -> int | None:
    """HTTP status on exceptions: httpx uses ``response``, google exception attributes."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        # Exclude bool, an int subclass; capability-exception ``code`` is string here, also rejected by isinstance.
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _http_status_in_chain(exc: BaseException) -> int | None:
    """First HTTP-status-bearing exception in ``__cause__`` chains."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        status = _http_status(current)
        if status is not None:
            return status
        seen.add(id(current))
        current = current.__cause__
    return None


def _provider_error_code(exc: BaseException) -> str | None:
    """String upstream ``code``; ``None`` if absent/nonstring."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) else None
