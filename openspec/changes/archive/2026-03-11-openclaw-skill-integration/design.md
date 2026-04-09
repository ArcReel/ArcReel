## Context

ArcReel currently uses OAuth2 Bearer JWT authentication; all API endpoints are verified through the `get_current_user` dependency. The frontend first obtains a JWT via `/auth/token`, and subsequent requests carry `Authorization: Bearer <jwt>`.

External platforms like OpenClaw need long-lived API Keys to call the ArcReel API, rather than short-lived JWTs. Additionally, the existing assistant conversation is SSE streaming-based, and external Agents need a synchronous request-response interface.

## Goals / Non-Goals

**Goals:**
- Add API Key authentication mode to the existing authentication system, coexisting with JWT authentication
- Reuse existing API endpoints without creating a separate "public API" layer
- Provide a synchronous Agent conversation endpoint for external callers
- Write a skill.md complying with the OpenClaw AgentSkill specification

**Non-Goals:**
- Not implementing multi-user/multi-tenant systems (keeping single-user mode)
- Not implementing API call rate limiting (future iteration)
- Not implementing API Key permission scope controls (all keys have full permissions)
- Not refactoring paths or parameter formats of existing API endpoints

## Decisions

### 1. API Key Format and Storage

**Decision**: Use `arc-` prefix + 32 random characters; only the SHA-256 hash is stored in the database.

Format: `arc-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (36 characters)

**Rationale**: The prefix helps users identify the source and also serves as a routing discriminator for authentication; hash storage ensures that database leakage does not expose the original key. Reference: Zopia's `zopia-xxxxxxxxxxxx` pattern.

### 2. Authentication Layer Modification Approach

**Decision**: Modify `_verify_and_get_payload` in `server/auth.py` to determine the authentication mode via the `arc-` prefix.

Flow:
1. Extract Bearer token
2. Check if the token starts with `arc-`
3. **Yes → API Key path**: Calculate SHA-256 hash → query database → on success return `{"sub": "apikey:<key_name>", "via": "apikey"}`
4. **No → JWT path**: JWT decode verification → on success return payload
5. Either path fails → return 401

**Rationale**: Prefix determination is deterministic, avoiding unnecessary JWT decode attempts. Minimal changes; all existing endpoints automatically gain API Key support.

**Alternatives**: Separate authentication middleware (larger change); new independent route prefix (violates reuse principle).

### 3. Synchronous Agent Conversation Endpoint

**Decision**: Add `POST /api/v1/agent/chat` endpoint; internally creates a temporary session → sends message → collects SSE stream until complete → returns full response.

Request body:
```json
{
  "project_name": "my-project",
  "message": "Help me write a mystery script",
  "session_id": null  // Optional; pass in to reuse a session
}
```

Response body:
```json
{
  "session_id": "xxx",
  "reply": "Sure, let me help you...",
  "status": "completed"
}
```

**Rationale**: Reference Zopia's `/api/v1/agent/chat` design. External Agents like OpenClaw do not support SSE and need a synchronous interface. Internally reuses AssistantService.

### 4. API Key Management Location

**Decision**: Backend adds `server/routers/api_keys.py`; frontend adds an "API Keys" tab in the settings page.

Database model `ApiKey`:
- `id`: primary key
- `name`: user-defined name
- `key_hash`: SHA-256 hash
- `key_prefix`: first 8 characters (`arc-xxxx`) for display in lists
- `created_at`: creation time
- `expires_at`: expiration time (optional; default 30 days)
- `last_used_at`: most recent use time

**Rationale**: Consistent with existing ORM system; reuses SQLAlchemy async + Alembic migration.

### 5. skill.md Dynamic Serving

**Decision**: skill.md is stored as a template at `public/skill.md.template`, with `{{BASE_URL}}` placeholders for API URLs. Dynamically rendered via a FastAPI route `GET /skill.md`: the actual base URL is inferred from the request's `Host` header and scheme, the placeholder is replaced, and the result is returned.

**Rationale**: This project is a self-hosted service; each user's domain/port is different. skill.md must dynamically adapt to API URLs. Static files cannot do this.

**Alternatives**: Have users manually fill in a base URL config (increases user burden); generate on the frontend (OpenClaw needs to obtain this directly from the server).

### 6. Frontend OpenClaw Usage Guide Modal

**Decision**: Add a 🦞 OpenClaw button to the top bar of the project lobby page; clicking it opens a usage guide Modal containing:
- Prompt (copyable): `Learn https://<domain>/skill.md and then follow the skill to freely create videos`
- Usage step instructions (4 steps)
- "Get API Token" button (navigates to the API Key management page)

The URL in the prompt inside the modal also needs to be dynamically replaced with the current access address.

**Rationale**: Reference Zopia's guide design; reduces user comprehension cost.

## Risks / Trade-offs

- **[Performance]** API Key requires database query verification on each request → add in-memory cache (LRU, TTL 5 minutes) to reduce database pressure
- **[Security]** API Key is long-lived → default 30-day expiration + support for manual revocation
- **[Compatibility]** Synchronous conversation endpoint may time out → set a reasonable response timeout (120 seconds); return partial response on timeout
- **[Single-user]** API Keys don't distinguish between users → acceptable in current single-user mode; needs extension for multi-user
