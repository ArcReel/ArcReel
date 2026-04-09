## 1. Download Token Backend Implementation

- [x] 1.1 Add `create_download_token(username, project_name)` and `verify_download_token(token, project_name)` functions in `server/auth.py`
- [x] 1.2 Add `POST /api/v1/projects/{name}/export/token` endpoint in `server/routers/projects.py` to issue download tokens
- [x] 1.3 Modify the authentication middleware in `server/app.py` to pass through requests to `/api/v1/projects/*/export` paths that carry a `download_token` query parameter
- [x] 1.4 Modify the export endpoint in `server/routers/projects.py` to support `download_token` query parameter authentication (validate purpose, project match, and expiration)
- [x] 1.5 Write unit tests for download token logic (`tests/test_auth.py` supplement + `tests/test_projects_archive_routes.py` supplement)

## 2. Export Scope Backend Implementation

- [x] 2.1 Modify the export endpoint in `server/routers/projects.py` to accept the `scope` query parameter (`full` / `current`, default `full`), passing it to `ProjectArchiveService`
- [x] 2.2 Modify the `export_project` method in `server/services/project_archive.py` to accept a `scope` parameter
- [x] 2.3 Implement `scope=current` logic: skip files under `versions/storyboards/`, `versions/videos/`, `versions/characters/`, and `versions/clues/` when iterating directories
- [x] 2.4 Implement `versions/versions.json` trimming logic for `scope=current`: retain only the current_version entry for each resource
- [x] 2.5 Modify the `arcreel-export.json` manifest writing logic so the `scope` field reflects the actual export scope
- [x] 2.6 Write unit tests for scope logic (`tests/test_project_archive_service.py` supplement)

## 3. Frontend Export Interaction Refactoring

- [x] 3.1 Add `requestExportToken(projectName)` method in `frontend/src/api.ts` to call the token issuance endpoint
- [x] 3.2 Add `getExportDownloadUrl(projectName, downloadToken, scope)` helper method in `frontend/src/api.ts` to construct the complete download URL
- [x] 3.3 Create the `ExportScopeDialog` component (reusable dialog) providing "Current Version Only" and "All Data" two options
- [x] 3.4 Modify `GlobalHeader.tsx`'s `handleExportProject`: after clicking the export button, open `ExportScopeDialog`; after selection, issue token and trigger `window.open` browser native download
- [x] 3.5 Remove old `exportProject` fetch+Blob logic from `frontend/src/api.ts` (after confirming no other callers)
- [x] 3.6 Write tests for frontend export-related changes (`GlobalHeader.test.tsx` supplement + API test supplement)
