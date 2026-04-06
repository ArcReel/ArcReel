import type { CostBreakdown, CostByType } from "@/types";

const formatterCache = new Map<string, Intl.NumberFormat>();

function getFormatter(currency: string): Intl.NumberFormat {
  let fmt = formatterCache.get(currency);
  if (!fmt) {
    fmt = new Intl.NumberFormat("en", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    });
    formatterCache.set(currency, fmt);
  }
  return fmt;
}

export function formatCost(breakdown: CostBreakdown | undefined): string {
  if (!breakdown || Object.keys(breakdown).length === 0) return "\u2014";
  return Object.entries(breakdown)
    .map(([cur, amt]) => {
      try {
        return getFormatter(cur).format(amt);
      } catch {
        return `${cur} ${amt.toFixed(2)}`;
      }
    })
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
    result[cur] = Math.round(result[cur] * 10000) / 10000;
  }
  return result;
}
