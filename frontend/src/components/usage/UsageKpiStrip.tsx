import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { UsageSummary } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { formatCalendarDay, formatCount, formatRatio } from "./usage-record-format";

const DASH = "—";

/** 范围副行的日期粒度：跨度以月计，年份省不掉——筛选可以选到去年。 */
const RANGE_DAY_OPTIONS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
};

function Cell({ label, value, sub }: { label: string; value: string; sub: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 px-4 py-3">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="num text-2xl leading-tight text-foreground">{value}</dd>
      <dd className="truncate text-xs text-muted-foreground">{sub}</dd>
    </div>
  );
}

/** KPI 只读 summary，不随状态筛选变化：口径要在整段界面里保持一致。 */
export function UsageKpiStrip({ summary }: { summary: UsageSummary | null }) {
  const { t, i18n } = useTranslation("dashboard");
  const language = i18n.language;

  const kpi = summary?.kpi;
  const primary = summary?.primary_currency ?? null;
  const costEntries = Object.entries(kpi?.cost ?? {}).filter(([, amount]) => amount > 0);
  const primaryAmount = primary ? (kpi?.cost[primary] ?? 0) : 0;
  const others = costEntries.filter(([currency]) => currency !== primary);

  return (
    <dl className="grid grid-cols-2 divide-border rounded-xl border border-border bg-card @2xl/page:grid-cols-4 @2xl/page:divide-x">
      <Cell
        label={t("usage_kpi_calls")}
        value={kpi ? formatCount(kpi.calls, language) : DASH}
        sub={
          summary?.range
            ? `${formatCalendarDay(summary.range.since, language, RANGE_DAY_OPTIONS)} – ${formatCalendarDay(summary.range.until, language, RANGE_DAY_OPTIONS)}`
            : DASH
        }
      />
      <Cell
        label={t("usage_kpi_success_rate")}
        value={kpi ? formatRatio(kpi.success_rate, language) : DASH}
        sub={kpi ? t("usage_kpi_success_count", { count: kpi.success }) : DASH}
      />
      <Cell
        label={t("usage_kpi_failed")}
        value={kpi ? formatCount(kpi.failed, language) : DASH}
        sub={kpi && kpi.cancelled > 0 ? t("usage_kpi_cancelled_count", { count: kpi.cancelled }) : DASH}
      />
      <Cell
        // 多币种不折算：主币种进大字，其余在副行原样列出。
        label={primary ? `${t("usage_kpi_cost")} · ${primary}` : t("usage_kpi_cost")}
        value={primary ? formatCurrencyAmount(primary, primaryAmount) : DASH}
        sub={
          others.length > 0
            ? others.map(([currency, amount]) => `+ ${formatCurrencyAmount(currency, amount)}`).join("  ")
            : DASH
        }
      />
    </dl>
  );
}
