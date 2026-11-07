/** Batch generation of storyboard images / storyboard videos for one episode: preview (targets, skipped items, estimated cost) and submission results. */

import type { CostBreakdown } from "./cost";
import type { ReferenceBatchEnqueueFailure } from "./reference-video";
import type { WorkflowAdmission } from "./workflow";

/** `storyboards` generates storyboard images in batches; `videos` generates storyboard videos in batches. */
export type StoryboardBatchKind = "storyboards" | "videos";

/** Reason an item without a usable artifact is excluded from this batch. */
export type StoryboardBatchSkipReason =
  | "missing_prompt"
  | "missing_storyboard"
  | "generating"
  | "reference_unavailable"
  | "unavailable";

export interface StoryboardBatchSkip {
  unit_id: string;
  reason: StoryboardBatchSkipReason;
}

export interface StoryboardBatchPreview {
  targets: { unit_id: string }[];
  skipped: StoryboardBatchSkip[];
  /** Totals only priced items; `null` when no items are priced. */
  estimated_cost: CostBreakdown | null;
  /** Number of items that cannot be priced by the price table and are excluded from `estimated_cost`. */
  unpriced_count: number;
  /** Storyboard videos only: admission result for the whole batch; `blocked` prevents submission. */
  admission?: WorkflowAdmission;
}

export interface StoryboardBatchSubmitted {
  /** `null` when the storyboard video batch fails admission; no tasks were created. */
  batch_id: string | null;
  task_ids_by_unit: Record<string, string>;
  skipped: StoryboardBatchSkip[];
  enqueue_failures: ReferenceBatchEnqueueFailure[];
  admission?: WorkflowAdmission;
}
