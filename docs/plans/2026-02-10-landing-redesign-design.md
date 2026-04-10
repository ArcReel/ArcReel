# Landing Page Redesign (Confirmed)

## Goals
Change the ArcReel landing page from "segmented information layout (platform value / case studies / official account)" to "strong above-the-fold hero + discover more content feed", improving first-screen impact and conversion path clarity while maintaining brand identity, without mechanically copying the reference site.

## User-Confirmed Key Decisions
- Page structure: `Above-the-fold Hero + Discover More`.
- Removed sections: platform value, case studies, and official account sections all removed.
- Top bar strategy: minimal — retain only Logo, `Contact Us`, `Enter Dashboard`.
- Contact interaction:
  - Desktop: hovering the WeChat entry shows a small QR card.
  - Mobile: clicking the WeChat entry toggles the same small card open/closed.
- Above-the-fold button: retain only one primary CTA (Enter Dashboard).
- Discover more content: phase 1 uses static placeholder cards (8–12 cards); replace with real content later.
- Copy style: short slogan + one-line subtitle.

## Visual Direction
- Dark background + perspective grid + neon accents, emphasizing a "stage" feel.
- Large headline uses short two-line layout to form a central visual anchor.
- Below the hero, use a "character ability array" instead of complex illustrations; satisfies structure and rhythm first, then wait for asset replacement.
- Discover more cards uniformly use 16:9 ratio with a play button overlay; support lightweight hover tooltips.

## Interaction and State
- Contact Us entry uses a unified state machine: `closed / open`.
- `open` trigger depends on device capability:
  - Hover-capable devices: mouseenter opens, mouseleave closes.
  - Touch devices: click toggles; clicking outside closes.
- Esc to close is retained; show degraded fallback text when QR code fails to load.

## Implementation Scope
- Primary modification: `frontend/src/react/components/landing-page.js`.
- Route wiring adjustment: `frontend/src/react/main.js`.
- Style enhancements: `frontend/src/css/app.css`.
- Regression tests: `frontend/tests/landing-page.test.mjs`.

## Acceptance Criteria
- Landing page no longer shows "platform value / case studies / official account" sections.
- Top bar has only "Contact Us + Enter Dashboard" actions.
- Above the fold has only one primary CTA.
- "Discover More" section and static card collection are present.
- Contact Us desktop hover / mobile click behavior works; has fallback when QR code is unavailable.
