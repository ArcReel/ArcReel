---
status: accepted
---

# Use file mtime as a content fingerprint for media cache busting; mark versioned/fingerprinted files immutable

Session revision counters redownload files on page refresh, reentry from another session, and virtualized remounting; a new session's `?v=0` may also refer to changed content. Replace counters with file mtime in nanoseconds as the asset URL's `?v=` parameter (asset fingerprint). For requests with `?v=` or `versions/` in the path, file routes set `Cache-Control: public, max-age=31536000, immutable`. Unchanged content means unchanged URL and a disk-cache hit in all these scenarios.

## Consequences

- Project APIs and SSE events must deliver fingerprints, and the frontend maintains a fingerprint store.
- Once `immutable` is sent, clients cache long-term; fingerprints therefore must change with content. Immutable historical files under `versions/` naturally qualify.
