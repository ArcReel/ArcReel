import { Landmark, Package, ShoppingBag, User, type LucideIcon } from "lucide-react";
import type { AssetType } from "@/types/asset";
import type { AssetSheetType } from "@/types/asset-sheet";

export const ASSET_TYPES: readonly AssetType[] = ["character", "scene", "prop"];

export const ASSET_TYPE_ICON: Record<AssetType, LucideIcon> = {
  character: User,
  scene: Landmark,
  prop: Package,
};

/** Four asset-image types: the three library types plus products. */
export const ASSET_SHEET_TYPE_ICON: Record<AssetSheetType, LucideIcon> = {
  ...ASSET_TYPE_ICON,
  product: ShoppingBag,
};
