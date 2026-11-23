
export type MentionKind = "product" | "character" | "scene" | "prop" | "unknown";

export interface AssetColorPalette {
  textClass: string;
  bgClass: string;
  borderClass: string;
  dotClass: string;
}

export const ASSET_COLORS: Record<MentionKind, AssetColorPalette> = {
  product: {
    textClass: "text-asset-product",
    bgClass: "bg-asset-product/15",
    borderClass: "border-asset-product/40",
    dotClass: "bg-asset-product",
  },
  character: {
    textClass: "text-asset-character",
    bgClass: "bg-asset-character/15",
    borderClass: "border-asset-character/40",
    dotClass: "bg-asset-character",
  },
  scene: {
    textClass: "text-asset-scene",
    bgClass: "bg-asset-scene/15",
    borderClass: "border-asset-scene/40",
    dotClass: "bg-asset-scene",
  },
  prop: {
    textClass: "text-asset-prop",
    bgClass: "bg-asset-prop/15",
    borderClass: "border-asset-prop/40",
    dotClass: "bg-asset-prop",
  },
  unknown: {
    textClass: "text-destructive",
    bgClass: "bg-destructive/15",
    borderClass: "border-destructive/40",
    dotClass: "bg-destructive",
  },
};

export function assetColor(kind: MentionKind | undefined): AssetColorPalette {
  if (!kind) return ASSET_COLORS.unknown;
  return ASSET_COLORS[kind] ?? ASSET_COLORS.unknown;
}
