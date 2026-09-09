import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { UsageSummary } from "@/types";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import { SeriesSwatch, UsageTrendChart } from "./UsageTrendChart";
import type { TrendBucket, TrendMetric } from "./usage-trend";
import { buildTrendBuckets, seriesFor, shortDay } from "./usage-trend";

const METRICS: { value: TrendMetric; labelKey: string }[] = [
  { value: "calls", labelKey: "usage_trend_metric_calls" },
  { value: "cost", labelKey: "usage_trend_metric_cost" },
];

const NAME_KEYS: Record<TrendMetric, { daily: string; weekly: string }> = {
  calls: { daily: "usage_trend_name_calls_daily", weekly: "usage_trend_name_calls_weekly" },
  cost: { daily: "usage_trend_name_cost_daily", weekly: "usage_trend_name_cost_weekly" },
};

export function UsageTrendCard({ summary }: { summary: UsageSummary | null }) {
  const { t, i18n } = useTranslation("dashboard");
  const [metric, setMetric] = useState<TrendMetric>("calls");

  const daily = useMemo(() => summary?.daily ?? [], [summary]);
  const { buckets, weekly } = useMemo(() => buildTrendBuckets(daily), [daily]);
  const primary = summary?.primary_currency ?? null;
  // 图上只有主币种，其余币种在脚注里原样列出，不折算也不叠加。
  const excluded = costEntries(summary?.kpi.cost).filter(([currency]) => currency !== primary);

  const formatValue = (value: number) =>
    metric === "calls"
      ? value.toLocaleString()
      : formatCurrencyAmount(primary ?? "USD", value, {
          minimumFractionDigits: 0,
          maximumFractionDigits: 2,
        });

  const bucketLabel = (bucket: TrendBucket) =>
    bucket.days > 1
      ? `${shortDay(bucket.from, i18n.language)} – ${shortDay(bucket.to, i18n.language)} · ${t("usage_trend_merged_days", { count: bucket.days })}`
      : shortDay(bucket.from, i18n.language);

  const name = t(NAME_KEYS[metric][weekly ? "weekly" : "daily"]);

  return (
    <section className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {t("usage_trend_title")}
        </h4>
        {weekly && (
          <span className="rounded-full border border-hairline-soft px-1.5 py-px font-mono text-[9.5px] uppercase tracking-[0.12em] text-text-4">
            {t("usage_trend_weekly_chip")}
          </span>
        )}
        <div
          role="group"
          aria-label={t("usage_trend_metric_label")}
          className="ml-auto flex items-center gap-1"
        >
          {METRICS.map((option) => {
            const active = metric === option.value;
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={active}
                onClick={() => setMetric(option.value)}
                className={
                  "focus-ring rounded-full px-2 py-0.5 text-[11px] transition-colors " +
                  (active ? "bg-accent-dim text-accent-2" : "text-text-3 hover:text-text")
                }
              >
                {t(option.labelKey)}
              </button>
            );
          })}
        </div>
      </div>

      {buckets.length === 0 ? (
        <p className="py-8 text-center text-[12px] text-text-3">{t("usage_trend_empty")}</p>
      ) : (
        <UsageTrendChart
          buckets={buckets}
          metric={metric}
          name={name}
          formatValue={formatValue}
          bucketLabel={bucketLabel}
        />
      )}

      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-3">
          {seriesFor(metric).map((entry) => (
            <li key={entry.key} className="inline-flex items-center gap-1.5">
              <SeriesSwatch series={entry} />
              {t(entry.labelKey)}
            </li>
          ))}
        </ul>
        {metric === "cost" && primary && (
          <p className="text-[11px] text-text-4">
            {excluded.length > 0
              ? t("usage_trend_cost_footnote_excluded", {
                  currency: primary,
                  amounts: excluded
                    .map(([currency, amount]) => formatCurrencyAmount(currency, amount))
                    .join(" + "),
                })
              : t("usage_trend_cost_footnote", { currency: primary })}
          </p>
        )}
      </div>
    </section>
  );
}
