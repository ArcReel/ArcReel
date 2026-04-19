/**
 * Shared color palette for asset-kind visual cues.
 *
 * Used by:
 * - prompt editor mention highlights (useShotPromptHighlight)
 * - MentionPicker group headers + option accents
 * - ReferencePanel pill borders/fills
 *
 * Kept aligned (by intent) with AssetSidebar/AssetLibraryPage visual grouping:
 * character = blue, scene = emerald, prop = amber.
 */

/**
 * UI rendering type — extends AssetKind with "unknown" for error/fallback states.
 *
 * Distinct from AssetKind (in `@/types/reference-video`), which represents only
 * valid project assets. This separation lets the UI render a warning appearance
 * for missing or unresolved references without polluting the domain model.
 */
export type MentionKind = "character" | "scene" | "prop" | "unknown";

export interface AssetColorPalette {
  /** Text color class (tailwind) */
  textClass: string;
  /** Background tint class (tailwind, low alpha) */
  bgClass: string;
  /** Border class (tailwind) */
  borderClass: string;
}

export const ASSET_COLORS: Record<MentionKind, AssetColorPalette> = {
  character: {
    textClass: "text-sky-300",
    bgClass: "bg-sky-500/15",
    borderClass: "border-sky-500/40",
  },
  scene: {
    textClass: "text-emerald-300",
    bgClass: "bg-emerald-500/15",
    borderClass: "border-emerald-500/40",
  },
  prop: {
    textClass: "text-amber-300",
    bgClass: "bg-amber-500/15",
    borderClass: "border-amber-500/40",
  },
  unknown: {
    textClass: "text-red-300",
    bgClass: "bg-red-500/15",
    borderClass: "border-red-500/40",
  },
};

export function assetColor(kind: MentionKind | undefined): AssetColorPalette {
  if (!kind) return ASSET_COLORS.unknown;
  return ASSET_COLORS[kind] ?? ASSET_COLORS.unknown;
}
