import type { ReferenceBatchAdmission } from "@/types";

/**
 * Five batch-enqueue outcomes. Server `decision` has only three: interrupted enqueueing retains created tasks,
 * so its decision stays `admitted`, distinguished from a fully queued batch by whether any units failed to enqueue. If interruption hits
 * the first target, no task is created: `interrupted` still has running tasks while `none_queued` has none.
 * Their implications differ and must not share wording.
 */
export type ReferenceBatchOutcome =
  | "queued"
  | "confirm"
  | "blocked"
  | "interrupted"
  | "none_queued";

/**
 * Centralize the decision: the canvas uses it to retain the outcome, and the dialog to choose visibility/shape. These are
 * two sides of one question; duplicate rules silently diverge when changed.
 */
export function referenceBatchOutcome(admission: ReferenceBatchAdmission): ReferenceBatchOutcome {
  if (admission.decision === "blocked") return "blocked";
  if (admission.decision === "confirmation_required") return "confirm";
  if (admission.enqueue_failures.length === 0) return "queued";
  return admission.task_ids.length > 0 ? "interrupted" : "none_queued";
}
