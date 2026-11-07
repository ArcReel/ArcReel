import type { AssetSheetStatusRow, AssetSheetType, Character, Product, Prop, Scene } from "@/types";

/** Browsing-card asset: one normalized shape across four types, differing only in optional counts. */
export interface GalleryAsset {
  type: AssetSheetType;
  name: string;
  description: string;
  /** Sheet path registered on the asset entry; absent means first generation. */
  sheetPath: string | null;
  aliasCount: number;
  derivativeCount: number;
  /** Product-original image count. */
  referenceCount: number;
}

export type GalleryAssetSource = Character | Scene | Prop | Product;

function sheetPathOf(type: AssetSheetType, asset: GalleryAssetSource): string | undefined {
  switch (type) {
    case "character":
      return (asset as Character).character_sheet;
    case "scene":
      return (asset as Scene).scene_sheet;
    case "prop":
      return (asset as Prop).prop_sheet;
    case "product":
      return (asset as Product).product_sheet;
  }
}

export function toGalleryAsset(type: AssetSheetType, name: string, asset: GalleryAssetSource): GalleryAsset {
  return {
    type,
    name,
    description: asset.description ?? "",
    sheetPath: sheetPathOf(type, asset) || null,
    aliasCount: "aliases" in asset ? (asset.aliases?.length ?? 0) : 0,
    derivativeCount: type === "character" ? Object.keys((asset as Character).derivatives ?? {}).length : 0,
    referenceCount: type === "product" ? ((asset as Product).reference_images?.length ?? 0) : 0,
  };
}

/** Sheet state uses manifest decisions when available, otherwise inferred from entry sheet-path registration. */
export function sheetStateOf(asset: GalleryAsset, row: AssetSheetStatusRow | undefined): "missing" | "stale" | "current" {
  if (row) return row.status === "missing" || row.status === "stale" ? row.status : "current";
  return asset.sheetPath ? "current" : "missing";
}

/** Card-image corner status; `current` shows nothing. */
export type GalleryMarker = "generating" | "no-description" | "missing" | "stale" | "current";

export function markerOf(asset: GalleryAsset, row: AssetSheetStatusRow | undefined, generating: boolean): GalleryMarker {
  if (generating) return "generating";
  if (!asset.description.trim()) return "no-description";
  return sheetStateOf(asset, row);
}

export type GalleryFilter = "all" | "pending" | "stale";

/** Toolbar filters sheet state only: pending means no usable sheet, outdated means mismatch with current descriptions/originals. */
export function matchesGalleryFilter(asset: GalleryAsset, row: AssetSheetStatusRow | undefined, filter: GalleryFilter): boolean {
  if (filter === "all") return true;
  const state = sheetStateOf(asset, row);
  return filter === "pending" ? state === "missing" : state === "stale";
}

/** Resource type for sheet-version APIs. */
export const VERSION_RESOURCE = {
  character: "characters",
  scene: "scenes",
  prop: "props",
  product: "products",
} as const satisfies Record<AssetSheetType, string>;
