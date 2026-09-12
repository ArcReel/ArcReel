import { AxisBottom, AxisLeft } from "@visx/axis";
import { GridRows } from "@visx/grid";
import { Group } from "@visx/group";
import { PatternLines } from "@visx/pattern";
import { ParentSize } from "@visx/responsive";
import { scaleBand, scaleLinear } from "@visx/scale";
import { BarRounded, BarStack } from "@visx/shape";
import { Tooltip, useTooltip } from "@visx/tooltip";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatRatio } from "./usage-record-format";
import type { TrendBucket, TrendMetric, TrendSeries } from "./usage-trend";
import {
  HATCH_STROKE,
  bucketSuccessRate,
  bucketTotal,
  seriesFor,
  shortDay,
  tickEvery,
  trendTicks,
} from "./usage-trend";

/** 斜纹填充的 pattern id；同一页只画一张趋势图，固定 id 即可。 */
const HATCH_ID = "usage-trend-hatch";

const CHART_HEIGHT = 200;
const MARGIN = { top: 10, right: 8, bottom: 22, left: 48 };
/** 柱宽上限与顶端圆角，按 Darkroom 的标记规范。 */
const MAX_BAR_WIDTH = 24;
const BAR_RADIUS = 4;
/** 段与段之间留出的面色间隙，用留白而不是描边分隔。 */
const SEGMENT_GAP = 2;

const AXIS_LABEL_FILL = "var(--color-text-4)";
const TICK_LABEL_PROPS = {
  fill: AXIS_LABEL_FILL,
  fontSize: 10,
  fontFamily: "var(--font-mono)",
} as const;

export interface UsageTrendChartProps {
  buckets: TrendBucket[];
  metric: TrendMetric;
  /** 图表的可读名称，同时作为表格替代的标题。 */
  name: string;
  formatValue: (value: number) => string;
  /** 桶的区间文案；按天时是单个日期，按周时是「M/D – M/D」。 */
  bucketLabel: (bucket: TrendBucket) => string;
}

interface HoveredBucket {
  bucket: TrendBucket;
  label: string;
}

function fillOf(series: TrendSeries): string {
  return series.hatched ? `url(#${HATCH_ID})` : series.color;
}

/**
 * 按天（或按周）堆叠的柱图。图本身对辅助技术是一张带名字的图片，等价数据由紧随其后的
 * 表格替代承担——屏幕阅读器读表比读柱子的坐标可靠得多。
 */
export function UsageTrendChart({
  buckets,
  metric,
  name,
  formatValue,
  bucketLabel,
}: UsageTrendChartProps) {
  const { t, i18n } = useTranslation("dashboard");
  const series = seriesFor(metric);
  const tooltip = useTooltip<HoveredBucket>();

  return (
    <div className="relative">
      <ParentSize
        className="w-full"
        initialSize={{ width: 640, height: CHART_HEIGHT }}
        style={{ height: CHART_HEIGHT }}
      >
        {({ width }) => (
          <TrendPlot
            width={width}
            buckets={buckets}
            metric={metric}
            name={name}
            series={series}
            formatValue={formatValue}
            bucketLabel={bucketLabel}
            onHover={tooltip.showTooltip}
            onLeave={tooltip.hideTooltip}
          />
        )}
      </ParentSize>

      {tooltip.tooltipOpen && tooltip.tooltipData && (
        <Tooltip
          unstyled
          applyPositionStyle
          left={tooltip.tooltipLeft}
          top={tooltip.tooltipTop}
          className="pointer-events-none z-10 w-[184px] -translate-x-1/2 rounded-[8px] border border-hairline px-2.5 py-2 text-[11px] shadow-xl"
          style={{ background: "oklch(0.18 0.011 265 / 0.96)" }}
        >
          <div className="font-mono text-[10px] text-text-4">{tooltip.tooltipData.label}</div>
          <ul className="mt-1 space-y-px">
            {series.map((entry) => (
              <li key={entry.key} className="flex items-center gap-2">
                <SeriesSwatch series={entry} />
                <span className="text-text-3">{t(entry.labelKey)}</span>
                <span className="num ml-auto text-text">
                  {formatValue(entry.value(tooltip.tooltipData!.bucket))}
                </span>
              </li>
            ))}
          </ul>
          <div className="mt-1 flex items-center justify-between border-t border-hairline-soft pt-1 text-text-2">
            <span>{t("usage_trend_total")}</span>
            <span className="num">
              {formatValue(bucketTotal(metric, tooltip.tooltipData.bucket))}
            </span>
          </div>
          {metric === "calls" && (
            <div className="flex items-center justify-between text-text-3">
              <span>{t("usage_kpi_success_rate")}</span>
              <span className="num">
                {formatRatio(bucketSuccessRate(tooltip.tooltipData.bucket), i18n.language)}
              </span>
            </div>
          )}
        </Tooltip>
      )}

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

export function SeriesSwatch({ series }: { series: TrendSeries }) {
  return (
    <span
      aria-hidden="true"
      className="h-[9px] w-[9px] shrink-0 rounded-[2px]"
      style={{
        background: series.hatched
          ? `repeating-linear-gradient(45deg, ${HATCH_STROKE} 0 1.5px, transparent 1.5px 4px)`
          : series.color,
      }}
    />
  );
}

interface TrendPlotProps extends Omit<UsageTrendChartProps, "metric"> {
  width: number;
  metric: TrendMetric;
  series: readonly TrendSeries[];
  onHover: (args: { tooltipLeft: number; tooltipTop: number; tooltipData: HoveredBucket }) => void;
  onLeave: () => void;
}

function TrendPlot({
  width,
  buckets,
  metric,
  name,
  series,
  formatValue,
  bucketLabel,
  onHover,
  onLeave,
}: TrendPlotProps) {
  const { i18n } = useTranslation("dashboard");
  const [hovered, setHovered] = useState<string | null>(null);
  const innerWidth = Math.max(0, width - MARGIN.left - MARGIN.right);
  const innerHeight = CHART_HEIGHT - MARGIN.top - MARGIN.bottom;

  const keys = useMemo(() => series.map((entry) => entry.key), [series]);
  const byKey = useMemo(
    () => new Map(series.map((entry) => [entry.key, entry])),
    [series],
  );

  const ticks = useMemo(
    () => trendTicks(metric, Math.max(0, ...buckets.map((b) => bucketTotal(metric, b)))),
    [metric, buckets],
  );

  const xScale = useMemo(
    () =>
      scaleBand<string>({
        domain: buckets.map((bucket) => bucket.from),
        range: [0, innerWidth],
        padding: 0.2,
      }),
    [buckets, innerWidth],
  );
  const yScale = useMemo(
    () =>
      scaleLinear<number>({
        domain: [0, ticks[ticks.length - 1]],
        range: [innerHeight, 0],
      }),
    [ticks, innerHeight],
  );

  // 每根柱最上面那段圆角，中间段方角：圆角只属于数据端。
  const topKeyOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const bucket of buckets) {
      const top = [...series].reverse().find((entry) => entry.value(bucket) > 0);
      if (top) map.set(bucket.from, top.key);
    }
    return map;
  }, [buckets, series]);

  const slot = xScale.bandwidth();
  const barWidth = Math.min(MAX_BAR_WIDTH, slot);
  const labelEvery = tickEvery(buckets.length, innerWidth);
  const visibleDays = useMemo(
    () => new Set(buckets.filter((_, index) => index % labelEvery === 0).map((b) => b.from)),
    [buckets, labelEvery],
  );

  return (
    <svg
      width={width}
      height={CHART_HEIGHT}
      role="img"
      aria-label={name}
      className="block overflow-visible"
    >
      <PatternLines
        id={HATCH_ID}
        width={6}
        height={6}
        stroke={HATCH_STROKE}
        strokeWidth={1.5}
        orientation={["diagonal"]}
      />
      <Group left={MARGIN.left} top={MARGIN.top}>
        <GridRows
          scale={yScale}
          width={innerWidth}
          tickValues={ticks}
          stroke="var(--color-hairline-soft)"
          strokeWidth={1}
        />
        <AxisLeft
          scale={yScale}
          tickValues={ticks}
          tickFormat={(value) => formatValue(Number(value))}
          hideAxisLine
          hideTicks
          tickLabelProps={() => ({ ...TICK_LABEL_PROPS, dx: -6, dy: 3, textAnchor: "end" })}
        />
        {innerWidth > 0 && (
          <BarStack<TrendBucket, string>
            data={buckets}
            keys={keys}
            x={(bucket) => bucket.from}
            xScale={xScale}
            yScale={yScale}
            value={(bucket, key) => byKey.get(String(key))?.value(bucket) ?? 0}
            color={(key) => {
              const entry = byKey.get(String(key));
              return entry ? fillOf(entry) : "transparent";
            }}
          >
            {(stacks) =>
              // BarStack 按序列分组，这里换成按柱分组：一根柱的各段要一起淡出，
              // 圆角也只有知道同一根柱里谁在最上面才定得下来。每个 stack 的 bars 与
              // data 同序，取同一个下标即是同一根柱。
              buckets.map((bucket, index) => (
                <g
                  key={bucket.from}
                  // 悬停时其余柱退到背景；减少动态效果偏好下直接切换，不做淡入淡出。
                  opacity={hovered === null || hovered === bucket.from ? 1 : 0.55}
                  className="motion-safe:transition-opacity motion-safe:duration-150"
                >
                  {stacks.map((stack) => {
                    const bar = stack.bars[index];
                    if (!bar || bar.height <= 0) return null;
                    // 上方还有可见段时从顶边让出 2px，间隙就落在两段之间。
                    const isTop = topKeyOf.get(bucket.from) === stack.key;
                    const gap = isTop ? 0 : SEGMENT_GAP;
                    return (
                      <BarRounded
                        key={stack.key}
                        x={bar.x + (slot - barWidth) / 2}
                        y={bar.y + gap}
                        width={barWidth}
                        height={Math.max(1, bar.height - gap)}
                        radius={BAR_RADIUS}
                        top={isTop}
                        fill={bar.color}
                      />
                    );
                  })}
                </g>
              ))
            }
          </BarStack>
        )}
        {buckets.map((bucket) => {
          const left = xScale(bucket.from) ?? 0;
          const step = xScale.step();
          return (
            <rect
              key={bucket.from}
              x={left - (step - slot) / 2}
              y={0}
              width={step}
              height={innerHeight}
              fill="transparent"
              onMouseMove={() => {
                setHovered(bucket.from);
                onHover({
                  tooltipLeft: MARGIN.left + left + slot / 2,
                  tooltipTop: 0,
                  tooltipData: { bucket, label: bucketLabel(bucket) },
                });
              }}
              onMouseLeave={() => {
                setHovered(null);
                onLeave();
              }}
            />
          );
        })}
        <AxisBottom
          top={innerHeight}
          scale={xScale}
          tickValues={buckets.filter((b) => visibleDays.has(b.from)).map((b) => b.from)}
          tickFormat={(value) => shortDay(String(value), i18n.language)}
          stroke="var(--color-hairline-strong)"
          hideTicks
          tickLabelProps={() => ({ ...TICK_LABEL_PROPS, dy: 4, textAnchor: "middle" })}
        />
      </Group>
    </svg>
  );
}
