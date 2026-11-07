---
status: accepted
---

> Jianying freeze extensions extract clip-out frames with bundled ffmpeg, and BGM loudness measurements use it too. If it cannot run, these are unavailable alongside rendering. “Jianying export is unaffected” below applies only without freeze extensions.

# Render final cuts locally with bundled ffmpeg; save/register cuts and Jianying drafts as artifacts

Server ffmpeg subprocesses render cuts. Both cuts and Jianying exports are queue tasks in a provider-free local `render` lane, globally fixed at one without configuration: GB-scale peak memory would exhaust Docker under concurrency. They make no provider calls and write no usage records (`docs/adr/0076`).

**Always use ffmpeg bundled with `imageio-ffmpeg`, never PATH or configurable paths.** Supported deployments are Docker x86_64/aarch64 and native Windows/macOS; `imageio-ffmpeg` provides static builds for all of them containing libx264/libass/xfade. System binaries vary: Homebrew lacks libass for subtitles and Windows often has none. One bundled binary ensures consistent output without capability selection or per-feature degradation. If it cannot run, rendering is unavailable with an explanation; Jianying export is unaffected as qualified above. Other repository ffmpeg calls share the locator. No ffprobe is bundled; probing is separate. Bundle one OFL Chinese subtitle font instead of relying on system fonts.

**Save both artifacts inside projects and register them.** Identity is episode + edit timeline + narration version, plus burned-subtitle choice for cuts (`docs/adr/0089`, `docs/adr/0090`). Freeze basis from the requested revision at render start; concurrent timeline changes make finished outputs stale immediately. Local Jianying directories/versions affect download packaging only, not identity/basis. Keep latest files per identity. Archives exclude cuts/drafts, so imports read missing and rerender.

## Rejected alternatives

- **Configured path → PATH → bundled with capability selection**: adds probing/inconsistent outputs for almost nonexistent custom-version/hardware-encoding needs.
- **Per-feature degradation** (image subtitles without libass, hard cuts without xfade): silently diverges from Jianying, violating shared sources.
- **MoviePy, MLT, GES, Remotion, browser rendering**: excluded respectively for dependency/performance conflicts, pip unavailability, licensing/per-render pricing, and encoding/performance constraints.
- **Provider lanes/configurable concurrency**: no provider exists, and peak memory requires one.
- **Generate-on-download and delete Jianying drafts**: cannot judge currency and violates artifact semantics.
- **Keep cut histories**: free local rerenders from revisions/materials replace hundreds-of-MB versions; paid unit videos retain histories.

## Consequences

- `imageio-ffmpeg` stays on FFmpeg 7.x without releases after 2025-01; replacements change only the locator. Binary/font redistribution includes licenses and source-access instructions.
- Bundled libass does not wrap Chinese; whole sentences overflow. Explicitly wrap Chinese ASS by width/font size, leaving English/Vietnamese space wrapping to libass. Jianying wraps automatically; positions need not match.
- Timeline defines cuts: transitions preserve total duration (`docs/adr/0090`). Test effects in both outputs and remove inconsistent transitions from vocabulary.
