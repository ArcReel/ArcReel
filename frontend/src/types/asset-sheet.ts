/** Status, batch generation, and regeneration impact of project asset sheets (bases and derivatives). */

import type { CostBreakdown } from "./cost";

export type AssetSheetType = "character" | "scene" | "prop" | "product";

/** Artifact manifest status for an asset sheet: missing means awaiting generation, including a registered file that cannot be read. */
export type AssetSheetStatus = "current" | "stale" | "missing" | "blocked";

export interface AssetSheetRef {
  /** `<type>/<name>`; derivatives use `<type>/<base>/<derivative>`. */
  unit_id: string;
  asset_type: AssetSheetType;
  /** Base name; for a derivative, the name of its base. */
  name: string;
  derivative: string | null;
}

export interface AssetSheetStatusRow extends AssetSheetRef {
  status: AssetSheetStatus;
  description_missing: boolean;
  image_to_image: boolean;
}

/** Batch generation scope: either a gallery type page or an episode. */
export type AssetSheetBatchScope = { asset_type: AssetSheetType } | { episode_id: number };

export type AssetSheetSkipReason = "generating" | "missing_description" | "owner_sheet_missing" | "unavailable";

export interface AssetSheetBatchPreview {
  targets: (AssetSheetRef & { depends_on: string | null })[];
  skipped: (AssetSheetRef & { reason: AssetSheetSkipReason })[];
  /** Totals only priced items; `null` when no items are priced. */
  estimated_cost: CostBreakdown | null;
  /** Number of items that cannot be priced by the price table and are excluded from `estimated_cost`. */
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
  /** Whether the server currently considers this asset sheet stale. */
  stale: boolean;
  storyboards: number;
  videos: number;
  derivatives: number;
}
