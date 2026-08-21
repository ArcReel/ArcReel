import type { ReferenceBatchAdmission } from "@/types";

/**
 * 一次批量入队的四种结局。服务端的 `decision` 只分到三种：入队中断不撤销已建的任务，
 * 那一路的 decision 仍是 `admitted`，与「整批都建上了」只差有没有单元没排上。
 */
export type ReferenceBatchOutcome = "queued" | "confirm" | "blocked" | "interrupted";

/**
 * 判定收在这一处：画布据此决定要不要留下这份结论，弹窗据此决定开合与形态。两处问的是
 * 同一件事的正反面，各写一遍就会在改判时静默失配。
 */
export function referenceBatchOutcome(admission: ReferenceBatchAdmission): ReferenceBatchOutcome {
  if (admission.decision === "blocked") return "blocked";
  if (admission.decision === "confirmation_required") return "confirm";
  return admission.enqueue_failures.length > 0 ? "interrupted" : "queued";
}
