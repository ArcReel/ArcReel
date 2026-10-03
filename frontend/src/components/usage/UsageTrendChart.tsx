import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { cn } from "cn";

import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { formatRatio } from "./usage-record-format";
import type { TrendBucket, TrendMetric, TrendSeries } from "./usage-trend";
import { bucketSuccessRate, bucketTotal, seriesFor, shortDay, trendTicks } from "./usage-trend";

/** 柱宽上限与顶端圆角。 */
const MAX_BAR_WIDTH = 24;
const BAR_RADIUS = 4;

export interface UsageTrendChartProps {
  buckets: TrendBucket[];
  metric: TrendMetric;
  /** 图表的可读名称，同时作为表格替代的标题。 */
  name: string;
  formatValue: (value: number) => string;
  /** 桶的区间文案；按天时是单个日期，按周时是「M/D – M/D」。 */
  bucketLabel: (bucket: TrendBucket) => string;
}

/** 一根柱的数据行：各序列的值按序列 key 展开，另带原桶供提示使用。 */
type TrendRow = Record<string, number | string | TrendBucket> & { from: string; bucket: TrendBucket };

export function SeriesSwatch({ series }: { series: TrendSeries }) {
  return <span aria-hidden="true" className={cn("size-2.5 shrink-0 rounded-xs", series.swatchClass)} />;
}

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
 * 按天（或按周）堆叠的柱图。图本身对辅助技术是一张带名字的图片，等价数据由紧随其后的
 * 表格替代承担——屏幕阅读器读表比读柱子的坐标可靠得多。
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
                // 只有最上面一段带圆角；中间段方角，段与段之间不出现缺口。
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
