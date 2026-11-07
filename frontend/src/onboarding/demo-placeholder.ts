/**
 * Demo images: derive SVGs by names, supplying data URIs to existing <img> elements.
 *
 * Onboarding shows partially produced projects, requiring images for every character/scene/prop/storyboard.
 * Never real images: binary assets inflate repositories with resolution/counts and cannot follow themes. Derive
 * hues from names and geometric motifs from kinds, sharing four-corner L framing marks so users recognize
 * unfilmed shots rather than loading failures.
 *
 * Motifs occupy central 60%: consumers use frames from 1:1 to 9:16 with
 * object-cover cropping; centered compositions alone remain complete across ratios.
 */

export type DemoPlaceholderMotif = "character" | "scene" | "prop" | "storyboard";

/** Four-corner L framing marks, shared signature across four motifs. */
const FRAMING_BRACKETS = [
  "M26 33 L26 26 L33 26",
  "M67 26 L74 26 L74 33",
  "M74 67 L74 74 L67 74",
  "M33 74 L26 74 L26 67",
]
  .map((d) => `<path d="${d}" fill="none" stroke="var(--pl-line)" stroke-width="1.1"/>`)
  .join("");

/** Per-kind geometry, centered within 100×100 coordinates. */
const MOTIFS: Record<DemoPlaceholderMotif, string> = {
  // Busts: heads + shoulders.
  character:
    '<circle cx="50" cy="40" r="10" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>' +
    '<path d="M32 72 Q50 52 68 72" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>',
  // Exteriors: horizons + distant mountains + celestial objects.
  scene:
    '<path d="M28 62 H72" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>' +
    '<path d="M32 62 L44 44 L56 62 Z M52 62 L62 48 L72 62 Z" fill="none" stroke="var(--pl-line)" stroke-width="1.4"/>' +
    '<circle cx="64" cy="38" r="5" fill="var(--pl-line)" opacity="0.5"/>',
  // Objects: nested diamonds.
  prop:
    '<path d="M50 30 L70 50 L50 70 L30 50 Z" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>' +
    '<path d="M50 41 L59 50 L50 59 L41 50 Z" fill="var(--pl-line)" opacity="0.35"/>',
  // Shots: thirds + diagonals + focal points.
  storyboard:
    '<path d="M43 30 V70 M57 30 V70 M30 43 H70 M30 57 H70" fill="none" stroke="var(--pl-line)" stroke-width="0.8" opacity="0.55"/>' +
    '<path d="M30 70 L70 30" fill="none" stroke="var(--pl-line)" stroke-width="1.2"/>' +
    '<circle cx="57" cy="43" r="4.5" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>',
};

/** Stable name-derived hues 0–359; equal names/salts yield equal colors, changed names change colors. */
function hashHue(name: string, salt: number): number {
  let hash = salt;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return hash % 360;
}

/**
 * Derive placeholders from seeds, returning data URIs usable directly in `<img src>`.
 *
 * @param seed Asset names; equal names give equal images, never color changes on rerenders.
 * @param motif Kind-specific motifs.
 */
export function demoPlaceholder(seed: string, motif: DemoPlaceholderMotif): string {
  const hue = hashHue(seed, 17);
  const hue2 = (hue + 40) % 360;
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="presentation" ' +
    `style="--pl-line:oklch(0.92 0.03 ${hue} / 0.45)">` +
    "<defs>" +
    '<radialGradient id="g" cx="32%" cy="26%" r="92%">' +
    `<stop offset="0%" stop-color="oklch(0.46 0.11 ${hue})"/>` +
    `<stop offset="52%" stop-color="oklch(0.27 0.06 ${hue2})"/>` +
    '<stop offset="100%" stop-color="oklch(0.15 0.02 265)"/>' +
    "</radialGradient>" +
    "</defs>" +
    '<rect width="100" height="100" fill="url(#g)"/>' +
    FRAMING_BRACKETS +
    MOTIFS[motif] +
    "</svg>";
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
