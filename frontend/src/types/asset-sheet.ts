
import type { CostBreakdown } from "./cost";

export type AssetSheetType = "character" | "scene" | "prop" | "product";

export type AssetSheetStatus = "current" | "stale" | "missing" | "blocked";

export interface AssetSheetRef {
  unit_id: string;
  asset_type: AssetSheetType;
  name: string;
  derivative: string | null;
}

export interface AssetSheetStatusRow extends AssetSheetRef {
  status: AssetSheetStatus;
  description_missing: boolean;
  image_to_image: boolean;
}

/** 批量生成的范围：画廊类型页或集层，二选一。 */
export type AssetSheetBatchScope = ({ asset_type: AssetSheetType } | { episode_id: number }) & {
  generation_options?: import("@/stores/run-settings-store").GenerationOptions;
};

export type AssetSheetSkipReason = "generating" | "missing_description" | "owner_sheet_missing" | "unavailable";

export interface AssetSheetBatchPreview {
  targets: (AssetSheetRef & { depends_on: string | null })[];
  skipped: (AssetSheetRef & { reason: AssetSheetSkipReason })[];
  estimated_cost: CostBreakdown | null;
  unpriced_count: number;
}

export interface AssetSheetBatchMember extends AssetSheetRef {
  task_id: string | null;
  deduped: boolean;
  status: string;
  problem_code?: string | null;
}

export interface AssetSheetBatchSubmitted {
  batch_id: string;
  members: AssetSheetBatchMember[];
}

export interface AssetRegenerationImpact {
  stale: boolean;
  storyboards: number;
  videos: number;
  derivatives: number;
}
