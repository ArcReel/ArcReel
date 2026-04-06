import type { CostBreakdown, CostByType } from "@/types";

const SYMBOLS: Record<string, string> = { USD: "$", CNY: "¥" };

export function formatCost(breakdown: CostBreakdown | undefined): string {
  if (!breakdown || Object.keys(breakdown).length === 0) return "—";
  return Object.entries(breakdown)
    .map(([cur, amt]) => `${SYMBOLS[cur] ?? cur}${amt.toFixed(2)}`)
    .join(" + ");
}

export function totalBreakdown(byType: CostByType): CostBreakdown {
  const result: CostBreakdown = {};
  for (const costs of Object.values(byType) as (CostBreakdown | undefined)[]) {
    if (!costs) continue;
    for (const [cur, amt] of Object.entries(costs)) {
      result[cur] = (result[cur] ?? 0) + amt;
    }
  }
  for (const cur of Object.keys(result)) {
    result[cur] = Math.round(result[cur] * 100) / 100;
  }
  return result;
}
