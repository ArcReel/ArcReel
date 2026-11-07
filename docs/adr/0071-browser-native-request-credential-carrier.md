---
status: accepted
---

# Credentials for browser-native requests: header-bearing fetch for SSE, anonymous media reads, download tokens for exports

Browser-native requests cannot carry an `Authorization` header. ArcReel previously gave three scenarios different answers: SSE appended a long-lived session JWT to `?token=`, project exports used short-lived download tokens, and static media had no authentication. Their strength and exposure were inverted: SSE carried a long-lived, fully privileged credential through query parameters into access logs. Handle the three according to their causes rather than unifying them under one carrier.

**Stop treating SSE as a browser-native request**. The frontend consumes streams through `fetch` with an `Authorization` header; the server moves the three streaming endpoints back onto ordinary protected routers and removes both `get_current_user_flexible` and the `?token=` form. Authentication-enforcement tests assert 401 for requests carrying `?token=`. Native `EventSource` reconnection reuses URLs unchanged, requiring any URL credential to be reminted before every reconnect, effectively adding credential lifecycles to compensate for an inherent `EventSource` limitation. Switching to `fetch` removes that cause, only reducing backend complexity. The cost is frontend ownership of event parsing (`eventsource-parser`), `Last-Event-ID` resumption, and backoff reconnection; both project and assistant streams automatically rebuild after disconnection.

**Static media intentionally remains anonymously readable**. ArcReel is a single-administrator self-hosted application whose threat model excludes attackers reading media without authorization. Authenticating media would require credentials on approximately fifty `<img>` / `<video>` src locations, extra Bearer credentials for embedded Agent and MCP image retrieval, and broken copied links/new-tab opening, buying only "unreachable without guessing the path." Future sharing or external embedding should explicitly issue sharing credentials and is outside this decision.

"Static media" has precise scope; anonymous readability does not make project directories anonymously readable. Public project-file endpoints (`/files/{project}/{path}`) require both directories and extensions to be allowlisted. Directories are limited to those written by generation/upload media flows: `storyboards/ end_frames/ videos/ reference_videos/ thumbnails/ characters/ scenes/ props/ products/ grids/ audio/` (including subdirectories), their respective snapshot buckets under `versions/`, and project-root `style_reference.*`. Extensions are limited to images (`.png .jpg .jpeg .webp`), video (`.mp4`), and audio (`.wav .mp3`); extensions are case-insensitive, while directories and filenames are case-sensitive. Global asset endpoints (`/global-assets/{type}/{filename}`) follow the same extension allowlist. `project.json`, scripts, source files, drafts, subtitles and presentation JSON, `versions.json`, finished videos in `output/`, and non-media files inside media directories are excluded, returning 404 identically to nonexistent files; they are read only through protected APIs. Both endpoints always return `X-Content-Type-Options: nosniff`. Define allowlists centrally in `server/routers/files.py`.

**Export downloads retain download tokens**, now the only credential form for self-authenticating endpoints. This resolves the "unify the three" question deferred at the end of ADR `docs/adr/0059`.

## Explicitly rejected

- **Cookie authentication**. It could give `<img>`, native downloads, and SSE credentials automatically, but adds value only when media requires login or credentials must be removed entirely from URLs. With anonymous media and SSE moving to `fetch`, only costs remain: login/logout changes, CSRF exposure, defining `AUTH_ENABLED=false` behavior, and threat-model reassessment.
- **Short-lived purpose-bound tickets (`purpose=stream`)**. Reusing download-token minting requires little change, but tickets still occupy URLs and `EventSource` needs a fresh ticket before each reconnect, adding a runtime ticket lifecycle solely to work around `EventSource` being unable to carry headers.
- **Separately deployed frontend**. The `CORS_ORIGINS` allowlist serves only non-browser cross-origin API calls (using Bearer); browser access must share the backend origin. This is both a premise for not needing Cookies and a constraint of this decision.

## Consequences

- The glossary's "browser-native request" now covers only `<img>` / `<video>` src and native download navigation; "self-authenticating endpoint" covers only export-download endpoints.
- Update SSE query-parameter leakage entries in `docs/security/threat-model.md` together with implementation; Cookies and refresh tokens still require reassessment.
- The frontend no longer has `EventSource` or `fakeEventSource` test doubles; streaming-client test doubles become controllable `fetch` response streams.
