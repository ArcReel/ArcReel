import type { ReferenceBatchAdmission } from "@/types";

export function hasReferenceOverflow(unit: ReferenceBatchAdmission["units"][number]): boolean {
  return unit.problems.some((problem) => problem.code === "video_reference_images_exceeded");
}

export type ReferenceBatchOutcome =
  | "queued"
  | "confirm"
  | "blocked"
  | "interrupted"
  | "none_queued";

export function referenceBatchOutcome(admission: ReferenceBatchAdmission): ReferenceBatchOutcome {
  if (admission.decision === "blocked") return "blocked";
  if (admission.decision === "confirmation_required") return "confirm";
  if (admission.enqueue_failures.length === 0) return "queued";
  return admission.task_ids.length > 0 ? "interrupted" : "none_queued";
}
