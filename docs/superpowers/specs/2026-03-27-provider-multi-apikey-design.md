# Provider Multi-API Key Support Design

**Date:** 2026-03-27
**Status:** Reviewed

## Overview

Support configuring multiple API Keys / Vertex credentials per provider, with manual switching of the currently active key, and connection testing for any individual key.

## Requirements

1. Multiple credentials (API Key or Vertex service account JSON) can be configured for the same provider
2. Each credential has: custom name, key value, optional custom base_url (AI Studio)
3. RPM / max_workers / request_gap and other settings follow the provider and are shared by all credentials
4. Each provider has one "currently active credential," manually switched on the provider settings page, taking effect globally
5. Connection testing can be performed on any individual credential
6. Bug fix: normalize trailing `/` in base_url

## Solution Selection

**Option A (Selected): Create new `provider_credential` table**

Credentials are independent structured entities (name, key, URL, active status); modeling them in a dedicated table is the most natural approach. This separates concerns from the existing `provider_config` (shared config KV) without polluting existing logic.

Rejected options:
- Option B (KV table slot prefix): naming conventions are brittle, queries are awkward
- Option C (JSON field): concurrent updates require read-modify-write, encryption/masking is complex

---

## Data Model

### New `provider_credential` Table

```python
class ProviderCredential(TimestampMixin, Base):
    __tablename__ = "provider_credential"
    __table_args__ = (
        Index("ix_provider_credential_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)          # "gemini-aistudio"
    name: Mapped[str] = mapped_column(String(128), nullable=False)             # user-defined name
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)           # for api_key-type providers
    credentials_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # Vertex JSON path
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)          # custom URL
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # created_at, updated_at provided by TimestampMixin
```

**Design notes:**
- `api_key` and `credentials_path` are mutually exclusive: api_key-type providers use `api_key`, Vertex uses `credentials_path`
- Each provider can have at most one `is_active=True` record (enforced at the application layer)
- `api_key` stored in plaintext (consistent with existing `provider_config` table), masked in API responses
- `base_url` is normalized to have a trailing `/` on storage
- `provider_config` table is unchanged, continues to store RPM / workers and other shared configuration

### Data Migration

Alembic migration script:
1. Create `provider_credential` table
2. Migrate existing `api_key` / `credentials_path` / `base_url` rows from `provider_config` into `provider_credential`, setting `is_active=True`, with name "Default Key"
3. Delete the migrated rows from `provider_config`

---

## Backend API

### Credential Management Endpoints (New)

All endpoints are in `server/routers/providers.py`, as sub-resources of providers.

#### `GET /api/v1/providers/{provider_id}/credentials`

Returns the list of all credentials for the provider (api_key masked).

**Response:**
```json
{
  "credentials": [
    {
      "id": 1,
      "provider": "gemini-aistudio",
      "name": "Personal Account",
      "api_key_masked": "AIza…xY2d",
      "credentials_filename": null,
      "base_url": "https://proxy.example.com/v1/",
      "is_active": true,
      "created_at": "2026-03-27T10:00:00Z"
    }
  ]
}
```

#### `POST /api/v1/providers/{provider_id}/credentials`

Add a credential. If this is the first credential for the provider, automatically set it as `is_active=True`.

**Request (api_key type):**
```json
{
  "name": "Team Account",
  "api_key": "AIza...",
  "base_url": "https://proxy.example.com/v1"
}
```

**Request (Vertex type):** multipart form — `name` field + `file` upload.

#### `PATCH /api/v1/providers/{provider_id}/credentials/{cred_id}`

Update a credential (name / api_key / base_url).

#### `DELETE /api/v1/providers/{provider_id}/credentials/{cred_id}`

Delete a credential. If the deleted credential is the active one and other credentials exist, automatically set the one with the earliest `created_at` as active. If all credentials are deleted, the provider status reverts to `unconfigured`. Triggers `invalidate_backend_cache()`.

#### `POST /api/v1/providers/{provider_id}/credentials/{cred_id}/activate`

Set the specified credential as active (simultaneously clearing other active flags for the same provider). Triggers `invalidate_backend_cache()` + `worker.reload_limits()`.

### Connection Test Changes

```
POST /api/v1/providers/{provider_id}/test?credential_id=123
```

- New optional query param `credential_id`
- If specified, test using that credential
- If not specified, test using the currently active credential
- If no credentials exist, return a "missing configuration" error

### Vertex Credential Upload Changes

```
POST /api/v1/providers/gemini-vertex/credentials
```

Changed to simultaneously upload a file + create a credential record. Files stored as `vertex_keys/vertex_cred_{cred_id}.json` (supports multiple files).

### Provider Status Determination Change

`ConfigService.get_all_providers_status()`'s `"ready"` determination changes from "whether `provider_config` has an `api_key`" to "whether `provider_credential` has an `is_active=True` record."

### ConfigResolver Integration

`ConfigResolver.provider_config()` return value logic adjustment:
1. Read shared configuration (RPM / workers, etc.) from `provider_config`
2. Read the active credential's `api_key` / `base_url` / `credentials_path` from `provider_credential`
3. Merge and return

**Callers are unaffected** — code like `db_config.get("api_key")` in `generation_tasks.py` does not need to change.

### ProviderConfigResponse Changes

The `fields` list in `GET /api/v1/providers/{provider_id}/config` no longer includes `api_key`, `credentials_path`, or `base_url` — only shared configuration fields (RPM / workers, etc.) are retained.

---

## Frontend UI

### ProviderDetail Page Redesign

Split into two sections:

**Section 1: Credential Management (replaces the original api_key / credentials_path / base_url fields)**

```
┌──────────────────────────────────────────────────┐
│  Key Management                    [+ Add Key]   │
├──────────────────────────────────────────────────┤
│  ● Personal Account    AIza…xY2d                 │
│    https://proxy.example.com/v1/                 │
│                        [Test] [Edit] [Delete]    │
│──────────────────────────────────────────────────│
│  ○ Team Account        AIza…k8Pm                 │
│                        [Test] [Activate] [Edit] [Delete] │
└──────────────────────────────────────────────────┘
```

- `●` / `○` indicates active/inactive status
- Each credential row displays: name, masked key (or Vertex filename), optional base_url
- Each credential has an independent "Test" button, calling `POST /test?credential_id=xxx`
- "Edit" expands an inline edit form
- "Add Key" opens an inline form
- Vertex provider's "Add" includes a file upload + name input

**Section 2: Shared Configuration (existing logic retained)**

```
┌──────────────────────────────────────────────────┐
│  ▸ Advanced Configuration                        │
│    Image RPM: [60]    Video RPM: [10]            │
│    Request Gap: [3.1] Image Workers: [2] Video Workers: [1] │
│                                        [Save]    │
└──────────────────────────────────────────────────┘
```

Saved via `PATCH /providers/{id}/config`, logic unchanged.

### New Type Definitions

```typescript
interface ProviderCredential {
  id: number;
  provider: string;
  name: string;
  api_key_masked: string | null;
  credentials_filename: string | null;
  base_url: string | null;
  is_active: boolean;
  created_at: string;
}
```

---

## base_url Normalization

### Problem

Google genai SDK's `http_options.base_url` requires a trailing `/`. User input without a trailing slash causes request failures.

### Fix

**Normalize on storage:** When creating/updating a credential, apply `url.strip()` to `base_url` and ensure it ends with `/`.

**Defensive normalization on consumption:** Add a defensive layer at the following 4 locations where `base_url` is used to create `genai.Client`:

1. `lib/image_backends/gemini.py:89` — image backend
2. `lib/video_backends/gemini.py:87` — video backend
3. `lib/gemini_client.py:498` — GeminiClient
4. `server/routers/providers.py:286` — connection test

Normalization function:
```python
def normalize_base_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.endswith("/"):
        url += "/"
    return url
```

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Provider has no credentials | Status is `unconfigured`, generation task errors with "key not configured" |
| Delete active credential, other credentials exist | Automatically set the credential with the earliest `created_at` as active |
| Delete active credential, no other credentials | Provider reverts to `unconfigured`, triggers `invalidate_backend_cache()` |
| Switch active credential | Clear backend cache, next generation task uses the new key |
| Duplicate credential names (within same provider) | Allowed, no unique constraint (distinguished by id) |
| base_url is empty string | Stored as `None`, uses the provider's default address |

## Out of Scope

- Automatic rotation / load balancing — manual switching only
- Encrypted api_key storage — consistent with existing `provider_config` table
- Credential usage statistics — out of current scope
