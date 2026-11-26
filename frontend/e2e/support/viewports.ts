export interface AcceptanceViewport {
  width: number;
  height: number;
}

export const DESKTOP_VIEWPORT: AcceptanceViewport = { width: 1440, height: 900 };
export const COMPACT_VIEWPORT: AcceptanceViewport = { width: 1024, height: 600 };

export const RESPONSIVE_TAG = "@responsive";
export const VIEWPORT_CONTRACT_TAG = "@viewport-contract";

export function projectName(viewport: AcceptanceViewport) {
  return `${viewport.width}x${viewport.height}`;
}
