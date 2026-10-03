import { Landmark, Package, User, type LucideIcon } from "lucide-react";
import type { AssetType } from "@/types/asset";

export const ASSET_TYPES: readonly AssetType[] = ["character", "scene", "prop"];

export const ASSET_TYPE_ICON: Record<AssetType, LucideIcon> = {
  character: User,
  scene: Landmark,
  prop: Package,
};
