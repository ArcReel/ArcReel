import type { ReferenceBatchAdmission } from "@/types";

/**
 * 一次批量入队的五种结局。服务端的 `decision` 只分到三种：入队中断不撤销已建的任务，
 * 那一路的 decision 仍是 `admitted`，与「整批都建上了」只差有没有单元没排上。中断落在
 * 第一个目标上时一个任务也没建成，`interrupted` 与 `none_queued` 的差别就在这里：前者
 * 还有任务在跑，后者没有，两者对用户的意味不同，不能用同一句话陈述。
 */
export type ReferenceBatchOutcome =
  | "queued"
  | "confirm"
  | "blocked"
  | "interrupted"
  | "none_queued";

/**
 * 判定收在这一处：画布据此决定要不要留下这份结论，弹窗据此决定开合与形态。两处问的是
 * 同一件事的正反面，各写一遍就会在改判时静默失配。
 */
export function referenceBatchOutcome(admission: ReferenceBatchAdmission): ReferenceBatchOutcome {
  if (admission.decision === "blocked") return "blocked";
  if (admission.decision === "confirmation_required") return "confirm";
  if (admission.enqueue_failures.length === 0) return "queued";
  return admission.task_ids.length > 0 ? "interrupted" : "none_queued";
}
