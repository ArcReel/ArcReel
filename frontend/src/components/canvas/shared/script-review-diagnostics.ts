import type { ScriptReviewViolation } from "@/types";

export interface ReviewItemJump {
  index: number;
  label: string;
  count: number;
}

export function withUniqueKeys<T>(
  items: readonly T[],
  baseKey: (item: T) => string,
): Array<{ item: T; key: string; index: number }> {
  const seen = new Map<string, number>();
  return items.map((item, index) => {
    const raw = baseKey(item);
    const count = (seen.get(raw) ?? 0) + 1;
    seen.set(raw, count);
    return { item, key: count === 1 ? raw : `${raw}#${String(count)}`, index };
  });
}

export function scriptPlanFixRequestText(
  t: (key: string, options?: Record<string, unknown>) => string,
  episodeRef: string,
  blockingIssues: ScriptReviewViolation[],
): string {
  return [
    t("dashboard:review_hand_to_agent_prefill_header", { episodeRef, count: blockingIssues.length }),
    ...blockingIssues.map((issue, index) => `${index + 1}. ${issue.message}`),
  ].join("\n");
}
