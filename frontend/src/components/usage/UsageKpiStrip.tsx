import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { UsageSummary } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { formatCalendarDay, formatCount, formatRatio } from "./usage-record-format";

const KPI_VALUE_STYLE: CSSProperties = {
  fontSize: 22,
  fontWeight: 400,
  letterSpacing: "-0.01em",
  lineHeight: 1.1,
  color: "var(--color-text)",
};

const DASH = "—";

/** 范围副行的日期粒度：跨度以月计，年份省不掉——筛选可以选到去年。 */
const RANGE_DAY_OPTIONS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
};

function Cell({
  label,
  value,
  sub,
  first,
}: {
  label: string;
  value: string;
  sub: ReactNode;
  first: boolean;
}) {
  return (
    <div className={"px-5 py-4" + (first ? "" : " border-l border-hairline-soft")}>
      <div className="font-mono text-[9.5px] font-bold uppercase tracking-[0.18em] text-text-4">
        {label}
      </div>
      <div className="font-editorial mt-1" style={KPI_VALUE_STYLE}>
        {value}
      </div>
      <div className="mt-1 text-[10.5px] text-text-4">{sub}</div>
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
    <div
      className="grid grid-cols-2 overflow-hidden rounded-[10px] border border-hairline sm:grid-cols-4"
      style={CARD_STYLE}
    >
      <Cell
        first
        label={t("usage_kpi_calls")}
        value={kpi ? formatCount(kpi.calls, language) : DASH}
        sub={
          summary?.range
            ? `${formatCalendarDay(summary.range.since, language, RANGE_DAY_OPTIONS)} – ${formatCalendarDay(summary.range.until, language, RANGE_DAY_OPTIONS)}`
            : DASH
        }
      />
      <Cell
        first={false}
        label={t("usage_kpi_success_rate")}
        value={kpi ? formatRatio(kpi.success_rate, language) : DASH}
        sub={kpi ? t("usage_kpi_success_count", { count: kpi.success }) : DASH}
      />
      <Cell
        first={false}
        label={t("usage_kpi_failed")}
        value={kpi ? formatCount(kpi.failed, language) : DASH}
        sub={
          kpi && kpi.cancelled > 0
            ? t("usage_kpi_cancelled_count", { count: kpi.cancelled })
            : DASH
        }
      />
      <Cell
        first={false}
        // 多币种不折算：主币种进大字，其余在副行原样列出。
        label={primary ? `${t("usage_kpi_cost")} · ${primary}` : t("usage_kpi_cost")}
        value={primary ? formatCurrencyAmount(primary, primaryAmount) : DASH}
        sub={
          others.length > 0
            ? others
                .map(([currency, amount]) => `+ ${formatCurrencyAmount(currency, amount)}`)
                .join("  ")
            : DASH
        }
      />
    </div>
  );
}
