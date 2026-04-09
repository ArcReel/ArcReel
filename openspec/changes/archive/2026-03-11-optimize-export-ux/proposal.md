## Why

The current project export functionality has two core UX issues:

1. **Download blocks the page**: Exports use the frontend fetch + Blob approach for downloading ZIP files; the entire file must be fully received in memory before saving is triggered. During this time the user cannot see progress and cannot switch pages (navigating away interrupts the fetch). For projects with many storyboard images/videos, the ZIP can be hundreds of MB, resulting in a very poor experience.
2. **Full export is redundant**: Every export packages all project content (including all historical version files under the `versions/` directory), with data volume far exceeding what users actually need. In most scenarios users only need the current version's resources, not historical version files.

## What Changes

- **Browser native download**: Change the export API's calling method from fetch → Blob → `<a>.click()` to directly having the browser open an authenticated download link. Browser native download supports progress display, can be paused/resumed, and does not block page switching.
- **Download URL secure authentication**: Introduce a short-lived one-time download token mechanism to avoid exposing long-lived JWTs in URL query strings.
- **Export scope options**: Add a selection to the export interaction — "Export All" vs. "Current Version Only":
  - **Export All**: Behavior is consistent with existing logic; packages the entire project directory (including `versions/`).
  - **Current Version Only**: Skips historical files under the `versions/` directory, retaining only the resources currently in use. Also records a `scope: "current"` marker in the `arcreel-export.json` manifest and retains only the current version entry in `versions.json` as metadata (preserving generation info like prompts), to allow restoring context on import.

## Capabilities

### New Capabilities
- `export-download-token`: Issuance and verification of short-lived one-time download tokens for secure authentication of browser native downloads
- `export-scope-selection`: Export scope selection (all / current version only), including backend packaging logic and frontend option UI

### Modified Capabilities
(No existing specs need modification)

## Impact

- **Backend**:
  - `server/auth.py` — add download token issuance/verification logic
  - `server/app.py` — authentication middleware needs to recognize download tokens
  - `server/routers/projects.py` — export endpoint supports scope parameter + download token verification
  - `server/services/project_archive.py` — packaging logic supports scope filtering
- **Frontend**:
  - `frontend/src/api.ts` — export API changed to obtain download URL rather than fetch Blob
  - `frontend/src/components/layout/GlobalHeader.tsx` — export button adds scope selection interaction
- **API changes**: export endpoint adds `scope` query param; new download token endpoint added
- **Compatibility**: Import logic already supports ZIP files without a `versions/` directory; `scope: "current"` export packages can be imported normally
