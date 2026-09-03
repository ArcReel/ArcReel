// PROTOTYPE — wayfinder #2290 趋势图：手绘 SVG 堆叠柱，无图表库。
// 指标切换（调用次数 = 成功/失败/已取消堆叠；参考费用 = 按媒体类型堆叠、只画主币种），点数 > 90 时前端按 7 天合并成周。
// 标记规范按 dataviz skill：柱 ≤ 24px、顶端 4px 圆角、段间 2px 面色间隙、hairline 网格、悬停整桶 tooltip。评审后整目录删除。
import { useLayoutEffect, useRef, useState } from "react";

import { MEDIA_LABELS, type DailyBucket, type MediaType, money } from "./usage-prototype-data";
import { MEDIA_TONE } from "./usage-prototype-shared";

export type TrendMetric = "calls" | "cost";

interface Series {
  key: string;
  label: string;
  color: string;
  hatch?: boolean;
  value: (b: DailyBucket) => number;
}

const CALL_SERIES: Series[] = [
  { key: "success", label: "成功", color: "oklch(0.62 0.10 295)", value: (b) => b.success },
  { key: "failed", label: "失败", color: "var(--color-danger)", value: (b) => b.failed },
  { key: "cancelled", label: "已取消", color: "oklch(0.55 0.01 265)", hatch: true, value: (b) => b.cancelled },
];

const COST_ORDER: MediaType[] = ["image", "audio", "video", "text"];
const COST_SERIES: Series[] = COST_ORDER.map((m) => ({ key: m, label: MEDIA_LABELS[m], color: MEDIA_TONE[m], value: (b) => b.cost_by_media_type[m] }));

interface Bucket {
  label: string;
  tip: string;
  data: DailyBucket;
}

function mergeWeeks(daily: DailyBucket[]): Bucket[] {
  const out: Bucket[] = [];
  // 从最新一天往回每 7 天一组，最早一组可能不足 7 天
  for (let end = daily.length; end > 0; end -= 7) {
    const start = Math.max(0, end - 7);
    const xs = daily.slice(start, end);
    const acc: DailyBucket = { date: xs[0].date, success: 0, failed: 0, cancelled: 0, cost_by_media_type: { image: 0, video: 0, text: 0, audio: 0 } };
    for (const b of xs) {
      acc.success += b.success;
      acc.failed += b.failed;
      acc.cancelled += b.cancelled;
      for (const m of COST_ORDER) acc.cost_by_media_type[m] += b.cost_by_media_type[m];
    }
    out.unshift({ label: shortDate(xs[0].date), tip: `${shortDate(xs[0].date)} – ${shortDate(xs[xs.length - 1].date)} · ${xs.length} 天合并`, data: acc });
  }
  return out;
}

function shortDate(d: string) {
  const [, m, day] = d.split("-");
  return `${Number(m)}/${Number(day)}`;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  const n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return n * p;
}

interface Props {
  daily: DailyBucket[];
  metric: TrendMetric;
  primaryCurrency: string | null;
  height?: number;
  /** 周合并标注的渲染位置由调用方决定；此处只回报是否合并。 */
  onBucketing?: (weekly: boolean) => void;
}

export function UsageTrendChart({ daily, metric, primaryCurrency, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(240, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const weekly = daily.length > 90;
  const buckets: Bucket[] = weekly ? mergeWeeks(daily) : daily.map((b) => ({ label: shortDate(b.date), tip: shortDate(b.date), data: b }));
  const series = metric === "calls" ? CALL_SERIES : COST_SERIES;
  const totals = buckets.map((b) => series.reduce((s, x) => s + x.value(b.data), 0));
  const yMax = niceMax(Math.max(...totals, 0));
  const fmt = (v: number) => (metric === "cost" ? money(primaryCurrency ?? "USD", v, v < 10 ? 2 : 0) : String(v));

  const PAD = { l: 44, r: 8, t: 10, b: 22 };
  const plotW = width - PAD.l - PAD.r;
  const plotH = height - PAD.t - PAD.b;
  const slot = plotW / buckets.length;
  const barW = Math.min(24, Math.max(2, slot - 2));
  const y = (v: number) => PAD.t + plotH - (v / yMax) * plotH;
  // 刻度步长取整数：在 5 / 4 / 2 等分里挑第一个整数步长
  const divisions = [5, 4, 2].find((d) => Number.isInteger(yMax / d)) ?? 4;
  const ticks = Array.from({ length: divisions + 1 }, (_, i) => (i / divisions) * yMax);
  const labelEvery = Math.max(1, Math.ceil(buckets.length / Math.floor(plotW / 56)));

  const hovered = hover !== null ? buckets[hover] : null;
  const tipLeft = hover !== null ? Math.min(width - 190, Math.max(0, PAD.l + hover * slot + slot / 2 - 90)) : 0;

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label={metric === "calls" ? "按天堆叠的调用次数" : "按天堆叠的参考费用"} className="block">
        <defs>
          <pattern id="proto-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="oklch(0.75 0.01 265)" strokeWidth="1.5" />
          </pattern>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={width - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--color-hairline-soft)" strokeWidth={1} />
            <text x={PAD.l - 8} y={y(t) + 3.5} textAnchor="end" className="fill-text-4 font-mono" fontSize={10}>
              {fmt(t)}
            </text>
          </g>
        ))}
        {buckets.map((b, i) => {
          const x = PAD.l + i * slot + (slot - barW) / 2;
          let acc = 0;
          const segs = series.map((s) => {
            const v = s.value(b.data);
            const y0 = y(acc);
            const y1 = y(acc + v);
            acc += v;
            return { s, v, top: y1, h: Math.max(0, y0 - y1) };
          });
          const isTop = (idx: number) => segs.slice(idx + 1).every((g) => g.v === 0);
          return (
            <g key={b.data.date} opacity={hover === null || hover === i ? 1 : 0.55}>
              {segs.map((g, idx) => {
                if (g.v <= 0) return null;
                const gap = idx > 0 ? 2 : 0;
                const h = Math.max(1, g.h - gap);
                const top = g.top + (isTop(idx) ? 0 : 0);
                const r = isTop(idx) ? Math.min(4, barW / 2, h) : 0;
                const d = `M${x},${top + h} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${top + h} Z`;
                return <path key={g.s.key} d={d} fill={g.s.hatch ? "url(#proto-hatch)" : g.s.color} />;
              })}
              <rect
                x={PAD.l + i * slot}
                y={PAD.t}
                width={slot}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                tabIndex={-1}
              />
              {i % labelEvery === 0 && (
                <text x={x + barW / 2} y={height - 6} textAnchor="middle" className="fill-text-4 font-mono" fontSize={10}>
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        <line x1={PAD.l} x2={width - PAD.r} y1={y(0)} y2={y(0)} stroke="var(--color-hairline-strong)" strokeWidth={1} />
      </svg>
      {hovered && (
        <div className="pointer-events-none absolute top-0 z-10 w-[180px] rounded-[8px] border border-hairline px-2.5 py-2 text-[11px] shadow-xl" style={{ left: tipLeft, background: "oklch(0.18 0.011 265 / 0.96)" }}>
          <div className="mb-1 font-mono text-[10px] text-text-4">{hovered.tip}</div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center gap-2 py-px">
              <span aria-hidden className="h-[2px] w-3 rounded-full" style={{ background: s.hatch ? "oklch(0.75 0.01 265)" : s.color }} />
              <span className="text-text-3">{s.label}</span>
              <span className="num ml-auto text-text">{fmt(s.value(hovered.data))}</span>
            </div>
          ))}
          <div className="mt-1 flex justify-between border-t border-hairline-soft pt-1 text-text-2">
            <span>合计</span>
            <span className="num">{fmt(totals[hover!])}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function TrendLegend({ metric }: { metric: TrendMetric }) {
  const series = metric === "calls" ? CALL_SERIES : COST_SERIES;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-3">
      {series.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-[9px] w-[9px] rounded-[2px]" style={{ background: s.hatch ? "repeating-linear-gradient(45deg, oklch(0.75 0.01 265) 0 1.5px, transparent 1.5px 4px)" : s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

export function isWeekly(daily: DailyBucket[]) {
  return daily.length > 90;
}
