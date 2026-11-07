import type { CostBreakdown, CostByType } from "@/types";

const formatterCache = new Map<string, Intl.NumberFormat>();
// Display order for known currencies. Currencies not listed here fall back to
// alphabetical order. Adjust this list to match the deployment's primary audience.
const CURRENCY_ORDER = ["CNY", "USD"];
const EMPTY_COST_PLACEHOLDER = "\u2014";
const DEFAULT_FRACTION_DIGITS = 2;

interface CurrencyFormatOptions {
  minimumFractionDigits?: number;
  maximumFractionDigits?: number;
}

function getFormatter(
  currency: string,
  minimumFractionDigits: number,
  maximumFractionDigits: number,
): Intl.NumberFormat {
  const cacheKey = `${currency}:${minimumFractionDigits}:${maximumFractionDigits}`;
  let fmt = formatterCache.get(cacheKey);
  if (!fmt) {
    fmt = new Intl.NumberFormat("en", {
      style: "currency",
      currency,
      currencyDisplay: "symbol",
      minimumFractionDigits,
      maximumFractionDigits,
    });
    formatterCache.set(cacheKey, fmt);
  }
  return fmt;
}

export function costEntries(breakdown: CostBreakdown | undefined): [string, number][] {
  return Object.entries(breakdown ?? {})
    .filter(([, amount]) => amount > 0)
    .sort(([left], [right]) => {
      const leftIndex = CURRENCY_ORDER.indexOf(left);
      const rightIndex = CURRENCY_ORDER.indexOf(right);
      if (leftIndex !== -1 || rightIndex !== -1) {
        return (leftIndex === -1 ? CURRENCY_ORDER.length : leftIndex) -
          (rightIndex === -1 ? CURRENCY_ORDER.length : rightIndex);
      }
      return left.localeCompare(right);
    });
}

export function formatCurrencyAmount(
  currency: string,
  amount: number,
  options: CurrencyFormatOptions = {},
): string {
  const minimumFractionDigits = options.minimumFractionDigits ?? DEFAULT_FRACTION_DIGITS;
  const maximumFractionDigits = options.maximumFractionDigits ?? DEFAULT_FRACTION_DIGITS;
  const normalizedMinimumFractionDigits = Math.min(minimumFractionDigits, maximumFractionDigits);
  const normalizedMaximumFractionDigits = Math.max(minimumFractionDigits, maximumFractionDigits);

  try {
    return getFormatter(currency, normalizedMinimumFractionDigits, normalizedMaximumFractionDigits).format(amount);
  } catch {
    return `${currency} ${amount.toLocaleString("en", {
      minimumFractionDigits: normalizedMinimumFractionDigits,
      maximumFractionDigits: normalizedMaximumFractionDigits,
    })}`;
  }
}

export function formatCost(breakdown: CostBreakdown | undefined): string {
  const entries = costEntries(breakdown);
  if (entries.length === 0) return EMPTY_COST_PLACEHOLDER;
  return entries.map(([cur, amt]) => formatCurrencyAmount(cur, amt)).join(" + ");
}

/**
 * For empty totals, distinguish actual zero from unknown: show `—` when `complete` is false due to unpriced portions or no call records,
 * otherwise 0. Format nonempty amounts normally; they may cover only priced portions, which callers explain separately.
 */
export function formatCostTotal(breakdown: CostBreakdown | undefined, complete: boolean): string {
  if (costEntries(breakdown).length > 0) return formatCost(breakdown);
  return complete ? "0" : EMPTY_COST_PLACEHOLDER;
}

/**
 * Same as {@link formatCost}, kept for callers that explicitly want a non-empty
 * placeholder when no cost has been recorded yet. Now also returns the em-dash
 * placeholder so multi-currency deployments don't see a stray `$0.00`.
 */
export function formatCostOrZero(breakdown: CostBreakdown | undefined): string {
  return formatCost(breakdown);
}

/**
 * Total for the current script, excluding historical spend not attributed to it.
 *
 * Historical spend is real cost left by deleted/changed entries, not current-script work. Deducting it as completed work
 * from remaining estimates invents a smaller balance: a $10 estimate and $10 historical spend would incorrectly show nothing left to pay.
 */
export function currentScriptBreakdown(byType: CostByType): CostBreakdown {
  const withoutHistory: CostByType = { ...byType };
  delete withoutHistory.unassigned;
  return totalBreakdown(withoutHistory);
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
