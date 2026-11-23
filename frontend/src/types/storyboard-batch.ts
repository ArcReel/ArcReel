
import type { CostBreakdown } from "./cost";
import type { ReferenceBatchEnqueueFailure } from "./reference-video";
import type { WorkflowAdmission } from "./workflow";

export type StoryboardBatchKind = "storyboards" | "videos";

export type StoryboardBatchSkipReason =
  | "missing_prompt"
  | "missing_storyboard"
  | "generating"
  | "reference_unavailable"
  | "unavailable";

export interface StoryboardBatchSkip {
  unit_id: string;
  reason: StoryboardBatchSkipReason;
  problem?: { code: string; message: string; params: Record<string, unknown> };
}

export interface StoryboardBatchPreview {
  targets: { unit_id: string }[];
  skipped: StoryboardBatchSkip[];
  estimated_cost: CostBreakdown | null;
  unpriced_count: number;
  admission?: WorkflowAdmission;
}

export interface StoryboardBatchSubmitted {
  batch_id: string | null;
  task_ids_by_unit: Record<string, string>;
  skipped: StoryboardBatchSkip[];
  enqueue_failures: ReferenceBatchEnqueueFailure[];
  admission?: WorkflowAdmission;
}
