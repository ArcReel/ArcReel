import { useMemo } from "react";
import { useTranslation } from "react-i18next";
// react-doctor-disable-next-line react-doctor/prefer-dynamic-import -- UsageTrendCard lazyNamed loads this module on demand, placing recharts in its separate chunk
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { SeriesSwatch } from "./SeriesSwatch";
import { formatRatio } from "./usage-record-format";
import type { TrendBucket, TrendMetric, TrendSeries } from "./usage-trend";
import { bucketSuccessRate, bucketTotal, seriesFor, shortDay, trendTicks } from "./usage-trend";

/** Bar-width caps/top corner radii. */
const MAX_BAR_WIDTH = 24;
const BAR_RADIUS = 4;

export interface UsageTrendChartProps {
  buckets: TrendBucket[];
  metric: TrendMetric;
  /** Human-readable chart names, also alternative-table titles. */
  name: string;
  formatValue: (value: number) => string;
  /** Bucket-interval copy; daily single dates, weekly M/D – M/D. */
  bucketLabel: (bucket: TrendBucket) => string;
}

/** Bar rows expand series values by keys and retain original buckets for hints. */
type TrendRow = Record<string, number | string | TrendBucket> & { from: string; bucket: TrendBucket };

function TrendTooltip({
  bucket,
  metric,
  series,
  formatValue,
  bucketLabel,
}: {
  bucket: TrendBucket;
  metric: TrendMetric;
  series: readonly TrendSeries[];
  formatValue: (value: number) => string;
  bucketLabel: (bucket: TrendBucket) => string;
}) {
  const { t, i18n } = useTranslation("dashboard");
  return (
    <div className="flex w-48 flex-col gap-1 rounded-lg bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-overlay ring-1 ring-foreground/10">
      <div className="text-muted-foreground">{bucketLabel(bucket)}</div>
      <ul className="flex flex-col gap-0.5">
        {series.map((entry) => (
          <li key={entry.key} className="flex items-center gap-2">
            <SeriesSwatch series={entry} />
            <span className="text-subtle-foreground">{t(entry.labelKey)}</span>
            <span className="num ml-auto">{formatValue(entry.value(bucket))}</span>
          </li>
        ))}
      </ul>
      <div className="flex items-center justify-between border-t border-border pt-1">
        <span className="text-subtle-foreground">{t("usage_trend_total")}</span>
        <span className="num">{formatValue(bucketTotal(metric, bucket))}</span>
      </div>
      {metric === "calls" && (
        <div className="flex items-center justify-between text-muted-foreground">
          <span>{t("usage_kpi_success_rate")}</span>
          <span className="num">{formatRatio(bucketSuccessRate(bucket), i18n.language)}</span>
        </div>
      )}
    </div>
  );
}

/**
 * Daily/weekly stacked bars. Assistive technology sees named images; immediately following tables provide equivalent
 * data, more reliably read by screen readers than bar coordinates.
 */
export function UsageTrendChart({ buckets, metric, name, formatValue, bucketLabel }: UsageTrendChartProps) {
  const { t, i18n } = useTranslation("dashboard");
  const series = seriesFor(metric);

  const config = useMemo<ChartConfig>(
    () => Object.fromEntries(series.map((entry) => [entry.key, { label: t(entry.labelKey) }])),
    [series, t],
  );
  const rows = useMemo<TrendRow[]>(
    () =>
      buckets.map((bucket) => ({
        from: bucket.from,
        bucket,
        ...Object.fromEntries(series.map((entry) => [entry.key, entry.value(bucket)])),
      })),
    [buckets, series],
  );
  const ticks = useMemo(
    () => trendTicks(metric, Math.max(0, ...buckets.map((bucket) => bucketTotal(metric, bucket)))),
    [metric, buckets],
  );

  return (
    <div>
      <div role="img" aria-label={name}>
        <ChartContainer config={config} className="aspect-auto h-52 w-full">
          <BarChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} accessibilityLayer={false}>
            <CartesianGrid vertical={false} />
            <XAxis
              dataKey="from"
              tickLine={false}
              axisLine={false}
              minTickGap={16}
              tickFormatter={(value: string) => shortDay(value, i18n.language)}
            />
            <YAxis
              width={56}
              tickLine={false}
              axisLine={false}
              domain={[0, ticks[ticks.length - 1]]}
              ticks={ticks}
              tickFormatter={(value: number) => formatValue(value)}
            />
            <ChartTooltip
              cursor={false}
              content={({ active, payload }) => {
                const row: unknown = payload?.[0]?.payload;
                if (!active || typeof row !== "object" || row === null || !("bucket" in row)) return null;
                return (
                  <TrendTooltip
                    bucket={(row as TrendRow).bucket}
                    metric={metric}
                    series={series}
                    formatValue={formatValue}
                    bucketLabel={bucketLabel}
                  />
                );
              }}
            />
            {series.map((entry, index) => (
              <Bar
                key={entry.key}
                dataKey={entry.key}
                stackId="trend"
                fill={entry.color}
                maxBarSize={MAX_BAR_WIDTH}
                // Round only top segments; square middle segments preserve gapless stacks.
                radius={index === series.length - 1 ? [BAR_RADIUS, BAR_RADIUS, 0, 0] : 0}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ChartContainer>
      </div>

      <table className="sr-only">
        <caption>{name}</caption>
        <thead>
          <tr>
            <th scope="col">{t("usage_trend_col_bucket")}</th>
            {series.map((entry) => (
              <th key={entry.key} scope="col">
                {t(entry.labelKey)}
              </th>
            ))}
            <th scope="col">{t("usage_trend_total")}</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((bucket) => (
            <tr key={bucket.from}>
              <th scope="row">{bucketLabel(bucket)}</th>
              {series.map((entry) => (
                <td key={entry.key}>{formatValue(entry.value(bucket))}</td>
              ))}
              <td>{formatValue(bucketTotal(metric, bucket))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
