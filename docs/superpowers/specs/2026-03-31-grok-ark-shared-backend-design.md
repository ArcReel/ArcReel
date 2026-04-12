# Grok & Ark Shared Backend Refactor Design

## Background

In the current AI backend, OpenAI provides a `create_openai_client()` factory function through `openai_shared.py`,
and Gemini provides a shared RateLimiter + retry mechanism through `gemini_shared.py`.
However, Grok and Ark's three backends for image/video/text each independently create clients,
with duplicated initialization logic, validation logic, and hardcoded constants.

## Goal

Create a shared module for each of Grok and Ark (`grok_shared.py` / `ark_shared.py`),
providing unified client factory functions and eliminating duplicate code across the three backends.
Follows the same pattern as `openai_shared.py`.

## Design

### 1. `lib/grok_shared.py`

New module with responsibilities:
- Provide `create_grok_client(*, api_key: str) -> xai_sdk.AsyncClient` factory function
- Unified API Key validation logic and error messages

```python
"""
Grok (xAI) shared utilities module

Reused by text_backends / image_backends / video_backends.
"""
from __future__ import annotations
import xai_sdk

def create_grok_client(*, api_key: str) -> xai_sdk.AsyncClient:
    """Create xAI AsyncClient with unified validation and construction."""
    if not api_key:
        raise ValueError("XAI_API_KEY is not set\nPlease configure xAI API Key in system settings")
    return xai_sdk.AsyncClient(api_key=api_key)
```

### 2. `lib/ark_shared.py`

New module with responsibilities:
- Export `ARK_BASE_URL` constant (eliminates three hardcoded occurrences)
- Provide `create_ark_client(*, api_key: str | None = None) -> Ark` factory function
- Unified API Key validation (supports environment variable fallback) and error messages

```python
"""
Ark (Volcano Engine) shared utilities module

Reused by text_backends / image_backends / video_backends.
"""
from __future__ import annotations
import os

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

def create_ark_client(*, api_key: str | None = None):
    """Create Ark client with unified validation and construction."""
    from volcenginesdkarkruntime import Ark

    resolved_key = api_key or os.environ.get("ARK_API_KEY")
    if not resolved_key:
        raise ValueError("Ark API Key not provided. Please configure API Key in 'Global Settings → Providers'.")
    return Ark(base_url=ARK_BASE_URL, api_key=resolved_key)
```

### 3. Grok Backend Changes

#### image_backends/grok.py
- Remove `import xai_sdk` and inline API Key validation
- `__init__` changed to `self._client = create_grok_client(api_key=api_key)`

#### video_backends/grok.py
- Same: remove top-level `import xai_sdk` and inline validation
- `__init__` changed to `self._client = create_grok_client(api_key=api_key)`

#### text_backends/grok.py (largest change)
- Synchronous `xai_sdk.Client` changed to async `xai_sdk.AsyncClient` (via `create_grok_client()`)
- `asyncio.to_thread(chat.sample)` → `await chat.sample()`
- `asyncio.to_thread(chat.parse, ...)` → `await chat.parse(...)`
- Remove `import asyncio`
- Retain `self._xai_sdk = xai_sdk` (still needed for `xai_sdk.chat.system()` and other constructors)
- **Fallback**: if AsyncClient's chat API is inconsistent with Client, revert to `to_thread` + synchronous calls

### 4. Ark Backend Changes

#### image_backends/ark.py
- Remove `from volcenginesdkarkruntime import Ark`, `os.environ` reads, and base_url hardcoding
- `__init__` changed to `self._client = create_ark_client(api_key=api_key)`
- Remove `self._api_key` field (no longer needed)

#### video_backends/ark.py
- Same as above
- Remove `self._api_key` field

#### text_backends/ark.py
- Primary client changed to `self._client = create_ark_client(api_key=api_key)`
- `_ARK_BASE_URL` local constant changed to import `ARK_BASE_URL` from `ark_shared`
- OpenAI-compatible client retained inside the text backend (dedicated to Instructor fallback)

### 5. Unchanged Parts

- `openai_shared.py` / `gemini_shared.py` — remain as-is
- `lib/config/` configuration system — unaffected
- `text_backends/ark.py` OpenAI-compatible client — stays in place

## Change Checklist

| File | Operation | Description |
|------|-----------|-------------|
| `lib/grok_shared.py` | Add | Factory function |
| `lib/ark_shared.py` | Add | Factory function + base_url constant |
| `lib/image_backends/grok.py` | Modify | Use `create_grok_client()` |
| `lib/video_backends/grok.py` | Modify | Use `create_grok_client()` |
| `lib/text_backends/grok.py` | Modify | Use `create_grok_client()` + make async |
| `lib/image_backends/ark.py` | Modify | Use `create_ark_client()` |
| `lib/video_backends/ark.py` | Modify | Use `create_ark_client()` |
| `lib/text_backends/ark.py` | Modify | Primary client changed to `create_ark_client()` |

## Testing Strategy

Pure refactoring; behavior unchanged. Run `ruff check` + `pytest` in full to pass; no new tests needed.
If any Grok/Ark backend mocks are involved, update them to the new import paths.
