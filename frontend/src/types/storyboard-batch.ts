/** 一集分镜图 / 分镜视频的批量生成：预览（名单、跳过项、预估费用）与提交结果。 */

import type { CostBreakdown } from "./cost";
import type { ReferenceBatchEnqueueFailure } from "./reference-video";
import type { WorkflowAdmission } from "./workflow";

/** `storyboards` 批量生成分镜图，`videos` 批量生成分镜视频。 */
export type StoryboardBatchKind = "storyboards" | "videos";

/** 没有可用产物、却不进这一批的原因。 */
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
  estimated_cost: CostBreakdown | null;
  /** 仅分镜视频：整批准入结论；`blocked` 时这一批不能提交。 */
  admission?: WorkflowAdmission;
}

export interface StoryboardBatchSubmitted {
  /** 分镜视频整批准入未通过时为 `null`，一个任务也没建。 */
  batch_id: string | null;
  task_ids_by_unit: Record<string, string>;
  skipped: StoryboardBatchSkip[];
  enqueue_failures: ReferenceBatchEnqueueFailure[];
  admission?: WorkflowAdmission;
}
