## Context

Current export flow: the frontend calls `GET /api/v1/projects/{name}/export` via `fetch` (with Bearer JWT), waits for the complete ZIP response body to be loaded into memory (Blob), then triggers browser save by creating an `<a>` tag. The backend uses FastAPI `FileResponse` to return a temporary ZIP file.

Issues: no progress indicator during large file downloads; switching pages interrupts the fetch. Additionally, every export includes all historical version files under the `versions/` directory, causing data redundancy.

Authentication current state: JWT (HS256, 7-day validity) passed via `Authorization: Bearer` header. SSE endpoints use a `?token=` query param fallback. The export endpoint is not on the allowlist and requires authentication.

## Goals / Non-Goals

**Goals:**
- Export downloads are taken over natively by the browser, supporting progress display, pause/resume, and page switching without interruption
- Authentication security for download URLs: avoids exposing long-lived JWT; uses short-lived one-time tokens
- Support two export scopes: all (including version history) and current version only
- "Current version only" export packages can be imported normally, retaining necessary version metadata

**Non-Goals:**
- Not refactoring the import process (existing import logic already handles ZIP files without versions/)
- Not implementing resumable uploads or chunked downloads
- Not implementing background async packaging + notification mechanism (not necessary for current project scale)
- Not modifying the `?token=` authentication method for SSE

## Decisions

### 1. Browser Native Download Approach: Issue Download Token + `window.open`

**Approach**: Frontend first calls `POST /api/v1/projects/{name}/export/token` to obtain a short-lived download token, then opens `GET /api/v1/projects/{name}/export?download_token=xxx&scope=full|current` via `window.open` or an `<a>` tag; the browser takes over the download directly.

**Alternatives**:
- *Put JWT directly in URL query*: Simple but insecure; JWT has a long validity (7 days) and would appear in browser history and server logs. **Rejected**.
- *Cookie authentication*: Would require refactoring the entire authentication system and introducing CSRF protection; too large a change. **Rejected**.
- *Content-Disposition + fetch streaming*: Browser support for the fetch streaming API is inconsistent, and frontend code still needs to maintain the connection. **Rejected**.

**Download token design**:
- Issuance endpoint: `POST /api/v1/projects/{name}/export/token` (requires Bearer JWT authentication)
- Token format: JWT (HS256, shared secret with existing), payload contains `sub` (username), `project` (project name), `purpose: "download"`, `exp` (5-minute expiration)
- Validation rules: export endpoint validates `download_token` query param, verifies `purpose` and `project` fields match
- One-time: no server-side state management needed (no Redis required); short validity + project-name binding satisfies security requirements

### 2. Export Scope Parameter: `scope` Query Param

**Approach**: Export endpoint accepts `scope=full|current` query param (default `full` for backward compatibility).

- `scope=full`: existing behavior, packages the entire project directory
- `scope=current`:
  - Skips historical files under the `versions/` directory (`versions/storyboards/`, `versions/videos/`, etc.)
  - Retains `versions/versions.json`, but trims to only the current version entry
  - The `scope` field in the manifest file `arcreel-export.json` is set to `"current"`

### 3. versions.json Trimming Strategy

When exporting "current version only", the `versions.json` only retains the version record pointed to by `current_version` for each resource entry. This means:
- Generation prompt and created_at metadata are preserved
- File paths point to existing version files (current version files are in the main resource directory, not under the versions/ subdirectory)
- After import, VersionManager can work normally (only one version)

### 4. Frontend Interaction: Display Selection Dialog After Clicking Export

After clicking the "Export ZIP" button, a simple selection dialog (not a full-screen modal) opens, providing two option cards:
- "Current Version Only" (recommended, smaller size)
- "All Data" (including version history)

After selection, immediately trigger download token issuance → browser native download.

## Risks / Trade-offs

- **[Download token validity too short]** → 5-minute window is sufficient to cover the delay from issuance to browser initiating the request. In extreme network conditions, users can click export again.
- **[Download token is not one-time]** → Theoretically the token can be reused multiple times within 5 minutes, but it is bound to the project name and has short validity; the risk is acceptable. If strict one-time use is needed, a Redis nonce can be added later.
- **[Trimming versions.json may lose historical context]** → This is the expected behavior for "current version only"; users should be clearly aware when choosing (noted in the UI).
- **[Backward compatibility]** → `scope` defaults to `full`; existing callers (if any external integrations exist) are not affected.
