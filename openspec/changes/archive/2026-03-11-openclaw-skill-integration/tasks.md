## 1. Database Layer — ApiKey Model and Migration

- [x] 1.1 Create `lib/db/models/api_key.py` ORM model (id, name, key_hash, key_prefix, created_at, expires_at, last_used_at)
- [x] 1.2 Register the new model in `lib/db/models/__init__.py`
- [x] 1.3 Generate an Alembic migration and run `alembic upgrade head`
- [x] 1.4 Create `lib/db/repositories/api_key_repository.py` (CRUD + query by hash + update last_used_at)

## 2. Authentication Layer Modification — API Key Authentication Routing

- [x] 2.1 Modify `_verify_and_get_payload` in `server/auth.py` to route to API Key or JWT path based on the `arc-` prefix
- [x] 2.2 Implement API Key validation logic (SHA-256 hash → query database → check expiration → return payload)
- [x] 2.3 Add LRU in-memory cache for API Key query results (TTL 5 minutes)
- [x] 2.4 Write unit tests for authentication routing (API Key success/expired/not found; JWT unaffected)

## 3. API Key Management Routes

- [x] 3.1 Create `server/routers/api_keys.py` (POST create, GET list, DELETE delete)
- [x] 3.2 Implement API Key generation logic (`arc-` + 32 random characters; hash stored; full key returned at creation time)
- [x] 3.3 Register routes in `server/app.py`
- [x] 3.4 Clear cache when a key is deleted
- [x] 3.5 Write integration tests for API Key management endpoints

## 4. Synchronous Agent Conversation Endpoint

- [x] 4.1 Create the `POST /api/v1/agent/chat` endpoint (create or reuse session → send message → collect complete response)
- [x] 4.2 Internally integrate with AssistantService, collecting SSE event stream until complete
- [x] 4.3 Implement 120-second timeout handling; return partial response + status: "timeout" on timeout
- [x] 4.4 Write tests for the synchronous conversation endpoint

## 5. Skill Definition File and Dynamic Rendering

- [x] 5.1 Create `public/skill.md.template`, write ArcReel Skill definition in the Zopia format, with `{{BASE_URL}}` placeholders for API URLs
- [x] 5.2 Create the `GET /skill.md` route (no authentication required), infer base URL from request Host/scheme, replace placeholders, and return
- [x] 5.3 Verify that `GET /skill.md` returns the correct dynamic URL under different deployment addresses

## 6. Frontend — API Key Management Page

- [x] 6.1 Add an "API Keys" tab component to the settings page
- [x] 6.2 Implement API Key list display (name, prefix, creation time, expiration time, last used time)
- [x] 6.3 Implement API Key creation functionality (dialog showing the full key, noting it is only visible once)
- [x] 6.4 Implement API Key deletion functionality (confirmation dialog)

## 7. Frontend — OpenClaw Guide Modal

- [x] 7.1 Add a 🦞 OpenClaw button to the project lobby page's top bar
- [x] 7.2 Implement the guide Modal component: prompt area (copyable, with dynamic skill.md URL), 4-step usage guide, "Get API Token" button
- [x] 7.3 URL in the prompt dynamically adapts to the current access address (`window.location.origin`)
- [x] 7.4 "Get API Token" button navigates to the API Key management page
