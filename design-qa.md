# Asset Card Generation Controls — Design QA

- Source visual truth: `/var/folders/kw/6zd2qrgs4c75yks_sh_qlb600000gn/T/codex-clipboard-a0309319-4ed8-4cef-95f1-a6b3b3be5019.png`
- Implementation screenshot: `implementation-asset-card-final.jpg`
- Viewport: `478 × 500` CSS px
- Source dimensions: `956 × 578` px at 2× density, normalized to `478 × 289` CSS px for comparison
- Implementation dimensions: `478 × 500` px at 1× density
- State: dark theme, 320px asset card, project-default image model with an intentionally long provider/model label, idle regenerate action

## Full-view comparison evidence

The source and browser-rendered implementation were opened together in one comparison input. The implementation preserves the existing card chrome, typography hierarchy, model-selector treatment, accent button, spacing, radii, colors, and copy. The requested difference is visible: the selector and regenerate button now remain within the card's padded content area.

## Focused region comparison evidence

The selector and regenerate action were inspected at card scale because this is the affected region. Browser geometry showed:

- Card: 320px wide, content control region from x=41 to x=319.
- Model selector: 278px wide, x=41 to x=319.
- Regenerate button: 278px wide, x=41 to x=319.
- Selector text truncates instead of increasing intrinsic width.
- Document horizontal overflow: false.
- Open dropdown: 278px wide and aligned to both selector edges.

## Required fidelity surfaces

- Fonts and typography: existing font family, weight, size, and line height are unchanged; the long label now truncates on one line.
- Spacing and layout rhythm: both controls align to the same 278px content width and preserve the card's 20px padding.
- Colors and visual tokens: existing dark-room and emerald accent tokens are unchanged.
- Image quality and asset fidelity: no image assets were changed; the supplied screenshot remains the visual reference.
- Copy and content: model fallback wording and “重新生成资产图” are unchanged.

## Findings

- No actionable P0, P1, or P2 differences remain in the affected region.

## Interaction and runtime checks

- Opened and closed the model selector.
- Confirmed the dropdown remains exactly as wide as its trigger.
- Confirmed the regenerate button remains within the card content boundary.
- Browser console errors: 0.
- Browser console warnings: 0.

## Comparison history

1. Initial source evidence showed the long model label expanding the generation-controls region beyond the card padding; the full-width regenerate button followed the expanded width.
2. Added a complete `min-width: 0` / `max-width: 100%` shrink chain to the shared selector, its trigger and label, and the four asset-card generation regions.
3. Post-fix browser evidence shows selector and button widths equal to the card content width with no horizontal overflow; the dropdown also matches the trigger width.

## Implementation checklist

- [x] Constrain shared model selector width.
- [x] Make the selected/fallback model label shrinkable and truncatable.
- [x] Constrain character, scene, prop, and product generation controls.
- [x] Verify long-label layout and dropdown interaction in the browser.

final result: passed
