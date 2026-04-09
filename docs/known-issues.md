# Known Issues

## Status Legend

- **Fixed** — resolved and merged into main
- **Pending** — not yet fixed, workaround available if noted
- **Investigating** — root cause not yet confirmed

---

## Video Generation

| # | Status | Description | Notes |
|---|--------|-------------|-------|
| 1 | Fixed | Veo video URI expires after 2 days, causing playback failure when reloading history | Fixed in 2026-01-21: URI is now persisted so it can be restored on reload |
| 2 | Pending | Grok image generation runs single-threaded even when max_workers > 1 | Suspected DB config residue; add pool logging to confirm |

## Script Generation

| # | Status | Description | Notes |
|---|--------|-------------|-------|
| 3 | Fixed | Structured output unavailable for Doubao Seed 2.0 Lite; Ark backend reports error | Fixed in 2026-03-30: removed incorrect `structured_output` capability declaration; Instructor fallback now used |

## Agent

| # | Status | Description | Notes |
|---|--------|-------------|-------|
| 4 | Fixed | Memory leak: idle Claude subprocesses not released, ~250 MB each | Fixed in 2026-03-23: idle TTL cleanup and max concurrent session limit added |
| 5 | Fixed | Reconnecting to a session in progress caused the most recent assistant reply to be lost | Fixed in 2026-02-28: `_build_initial_raw_messages()` filter logic corrected |

## Frontend / UI

| # | Status | Description | Notes |
|---|--------|-------------|-------|
| 6 | Fixed | Landing page still showed "Platform Value / Cases / WeChat Account" sections after redesign | Fixed in 2026-02-10: removed all legacy sections |

## Other

| # | Status | Description | Notes |
|---|--------|-------------|-------|
| 7 | Pending | `ffmpeg` not installed in some Docker environments, causing video concatenation to fail | Workaround: install manually with `apt-get install ffmpeg` |
