# Design QA — Asset card source controls

- Source visual truth: `/var/folders/kw/6zd2qrgs4c75yks_sh_qlb600000gn/T/codex-clipboard-01760bc1-feb1-4f63-93fc-113cf745fb27.png`
- Implementation screenshot: `implementation-reference-audio-state.png`
- Comparison image: `design-qa-comparison.png`
- Browser viewport: 1276 × 718 CSS px; focused character-card capture used a 420 × 190 CSS px clip.
- Pixel dimensions: source 668 × 408 px; implementation crop 420 × 190 px. The comparison scales the implementation crop to the source width while preserving aspect ratio.
- State: dark theme, linked global character, global image in the main slot, reference audio active. The alternate Voice ID state was separately verified in `implementation-voice-id-state.png`.

## Full-view comparison evidence

The character card was rendered with the production component and styles in the in-app browser. The source and focused implementation capture were combined in `design-qa-comparison.png`. The sound-field hierarchy, dark field treatment, audio player, status text, and primary generate action remain consistent with the source. The requested source-switch action is intentionally added to the sound heading.

## Focused region evidence

- Image source switch is positioned between the main-image and reference-image regions.
- Link/Unlink and image-switch controls measure 28 × 28 px with 14 × 14 px Lucide icons and share the existing toolbar color, radius, and hover treatment.
- Reference-audio state shows the audio player and “切换 Voice ID”.
- Voice-ID state replaces the player/upload area with the linked Voice ID and shows “切换参考音频”.
- Native voice-source select is absent.

## Findings

No actionable P0/P1/P2 findings remain.

Required fidelity surfaces:

- Fonts and typography: existing product font stack, sizes, weights, and hierarchy are preserved.
- Spacing and layout rhythm: the switch icon sits in the existing gap between image regions; sound controls remain aligned with the section heading and fields.
- Colors and visual tokens: all controls reuse existing text, hairline, focus, and hover tokens.
- Image quality and asset fidelity: no product image assets were replaced or synthesized; icons come from the existing Lucide dependency.
- Copy and content: Chinese, English, and Vietnamese strings are present; Chinese action labels match the requested wording.

## Comparison history

1. Initial pass found the image switch and unlink icon used a stronger green treatment than adjacent toolbar actions. Both were changed to the standard 28 px toolbar-button style with 14 px icons.
2. The Voice ID pass found that the saved reference-audio player remained visible after switching sources. The source area now renders the linked Voice ID instead.
3. Post-fix browser inspection found no console errors and confirmed all requested controls in the rendered DOM.

## Verification

- Primary interactions: image source switch, reference-audio/Voice-ID switch, link/unlink affordances.
- Automated component tests: 21 passed.
- TypeScript typecheck: passed.
- ESLint: passed for production files; the temporary QA entry was removed after capture.
- Browser console errors: none.

final result: passed
