/** 项目资产图（本体与衍生）的状态、批量生成与重生影响。 */

import type { CostBreakdown } from "./cost";

export type AssetSheetType = "character" | "scene" | "prop" | "product";

/** 产物清单对一张资产图的判定：missing 即待生成（含登记了文件却读不到）。 */
export type AssetSheetStatus = "current" | "stale" | "missing" | "blocked";

export interface AssetSheetRef {
  /** `<类型>/<名字>`；衍生为 `<类型>/<本体>/<衍生>`。 */
  unit_id: string;
  asset_type: AssetSheetType;
  /** 本体名；衍生时是其本体的名字。 */
  name: string;
  derivative: string | null;
}

export interface AssetSheetStatusRow extends AssetSheetRef {
  status: AssetSheetStatus;
  description_missing: boolean;
  image_to_image: boolean;
}

/** 批量生成的范围：画廊类型页或集层，二选一。 */
export type AssetSheetBatchScope = { asset_type: AssetSheetType } | { episode_id: number };

export type AssetSheetSkipReason = "generating" | "missing_description" | "owner_sheet_missing" | "unavailable";

export interface AssetSheetBatchPreview {
  targets: (AssetSheetRef & { depends_on: string | null })[];
  skipped: (AssetSheetRef & { reason: AssetSheetSkipReason })[];
  estimated_cost: CostBreakdown | null;
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
  storyboards: number;
  videos: number;
  derivatives: number;
}
