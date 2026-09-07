import type { CSSProperties, ReactNode } from "react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { UsageSummary } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";

const KPI_VALUE_STYLE: CSSProperties = {
  fontSize: 22,
  fontWeight: 400,
  letterSpacing: "-0.01em",
  lineHeight: 1.1,
  color: "var(--color-text)",
};

const LOCALES: Record<string, string> = {
  zh: "zh-CN",
  en: "en-US",
  vi: "vi-VN",
};

const DASH = "—";

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
  const percentFmt = useMemo(() => {
    const lang = i18n.language.split("-")[0];
    return new Intl.NumberFormat(LOCALES[lang] ?? "en-US", {
      style: "percent",
      maximumFractionDigits: 1,
    });
  }, [i18n.language]);

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
        value={kpi ? kpi.calls.toLocaleString() : DASH}
        sub={
          summary?.range ? `${summary.range.since} – ${summary.range.until}` : DASH
        }
      />
      <Cell
        first={false}
        label={t("usage_kpi_success_rate")}
        value={
          kpi && kpi.success_rate !== null ? percentFmt.format(kpi.success_rate) : DASH
        }
        sub={kpi ? t("usage_kpi_success_count", { count: kpi.success }) : DASH}
      />
      <Cell
        first={false}
        label={t("usage_kpi_failed")}
        value={kpi ? kpi.failed.toLocaleString() : DASH}
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
