import type { CallType, UsageDailyBucket } from "@/types";
import { MEDIA_META, formatCalendarDay } from "./usage-record-format";

/** 趋势图的两种指标：调用次数按结果堆叠，参考费用按媒体类型堆叠。 */
export type TrendMetric = "calls" | "cost";

/** 日桶多于这个数就把相邻若干天并成一桶，否则一天一桶。 */
export const DAILY_POINT_LIMIT = 90;

/** 合并时一桶覆盖的天数。 */
export const MERGE_DAYS = 7;

/** 堆叠顺序：费用视图从下往上按图片、音频、视频、文本。 */
const MEDIA_ORDER: readonly CallType[] = ["image", "audio", "video", "text"];

/** 一根柱；按天分桶时 `from` 与 `to` 相同，`days` 为 1。 */
export interface TrendBucket {
  /** 桶内第一天，本地日 `YYYY-MM-DD`。 */
  from: string;
  to: string;
  days: number;
  success: number;
  failed: number;
  cancelled: number;
  /** 只含主币种金额，其余币种不折算也不混入。 */
  cost: Record<CallType, number>;
}

function accumulate(days: UsageDailyBucket[]): TrendBucket {
  const cost: Record<CallType, number> = { image: 0, audio: 0, video: 0, text: 0 };
  let success = 0;
  let failed = 0;
  let cancelled = 0;
  for (const day of days) {
    success += day.success;
    failed += day.failed;
    cancelled += day.cancelled;
    for (const media of MEDIA_ORDER) cost[media] += day.cost_by_media_type[media] ?? 0;
  }
  return {
    from: days[0].date,
    to: days[days.length - 1].date,
    days: days.length,
    success,
    failed,
    cancelled,
    cost,
  };
}

/**
 * 日桶铺成柱。点数超过上限时从最新一天往回每 7 天并一桶——最右一桶恒是完整一周，
 * 不足一周的余数落在最早那桶，图的右端才不会因为「今天还没过完」而矮一截。
 */
export function buildTrendBuckets(daily: UsageDailyBucket[]): {
  buckets: TrendBucket[];
  weekly: boolean;
} {
  const weekly = daily.length > DAILY_POINT_LIMIT;
  if (!weekly) return { buckets: daily.map((day) => accumulate([day])), weekly };
  const buckets: TrendBucket[] = [];
  for (let end = daily.length; end > 0; end -= MERGE_DAYS) {
    buckets.unshift(accumulate(daily.slice(Math.max(0, end - MERGE_DAYS), end)));
  }
  return { buckets, weekly };
}

export function bucketCalls(bucket: TrendBucket): number {
  return bucket.success + bucket.failed + bucket.cancelled;
}

export function bucketCost(bucket: TrendBucket): number {
  return MEDIA_ORDER.reduce((sum, media) => sum + bucket.cost[media], 0);
}

/** 桶的成功率：对桶内各天求和后再算，不是逐日成功率的平均。分母 0 时为 null。 */
export function bucketSuccessRate(bucket: TrendBucket): number | null {
  const settled = bucket.success + bucket.failed;
  return settled === 0 ? null : bucket.success / settled;
}

/**
 * 计数轴刻度：步长取 1 / 2 / 5 × 10ⁿ 里第一个能把最大值压进 4 段的整数，
 * 半根调用没有意义，刻度不出现小数。
 */
export function countTicks(max: number): number[] {
  if (max <= 0) return [0, 1];
  for (let power = 0; power < 12; power += 1) {
    for (const factor of [1, 2, 5]) {
      const step = factor * 10 ** power;
      if (max / step > 4) continue;
      const ticks: number[] = [];
      for (let value = 0; value <= Math.ceil(max / step) * step; value += step) {
        ticks.push(value);
      }
      return ticks;
    }
  }
  return [0, max];
}

/** 金额轴刻度：顶端取一个好读的整数倍，四等分。 */
export function amountTicks(max: number): number[] {
  if (max <= 0) return [0, 1];
  const power = 10 ** Math.floor(Math.log10(max));
  const leading = max / power;
  const rounded = leading <= 1 ? 1 : leading <= 2 ? 2 : leading <= 2.5 ? 2.5 : leading <= 5 ? 5 : 10;
  const top = rounded * power;
  return [0, 0.25, 0.5, 0.75, 1].map((ratio) => ratio * top);
}

export function trendTicks(metric: TrendMetric, max: number): number[] {
  return metric === "calls" ? countTicks(max) : amountTicks(max);
}

/** 一条堆叠序列：`color` 用于图形填充，`swatchClass` 用于图例与提示里的色块，两者是同一个 token。 */
export interface TrendSeries {
  key: string;
  labelKey: string;
  color: string;
  swatchClass: string;
  value: (bucket: TrendBucket) => number;
}

const CALL_SERIES: readonly TrendSeries[] = [
  {
    key: "success",
    labelKey: "usage_status_success",
    color: "var(--primary)",
    swatchClass: "bg-primary",
    value: (bucket) => bucket.success,
  },
  {
    key: "failed",
    labelKey: "usage_status_failed",
    color: "var(--destructive)",
    swatchClass: "bg-destructive",
    value: (bucket) => bucket.failed,
  },
  {
    key: "cancelled",
    labelKey: "usage_status_cancelled",
    color: "var(--muted-foreground)",
    swatchClass: "bg-muted-foreground",
    value: (bucket) => bucket.cancelled,
  },
];

const COST_SERIES: readonly TrendSeries[] = MEDIA_ORDER.map((media) => ({
  key: media,
  labelKey: MEDIA_META[media].labelKey,
  color: MEDIA_META[media].color,
  swatchClass: MEDIA_META[media].swatchClass,
  value: (bucket: TrendBucket) => bucket.cost[media],
}));

export function seriesFor(metric: TrendMetric): readonly TrendSeries[] {
  return metric === "calls" ? CALL_SERIES : COST_SERIES;
}

export function bucketTotal(metric: TrendMetric, bucket: TrendBucket): number {
  return metric === "calls" ? bucketCalls(bucket) : bucketCost(bucket);
}

/** 轴与 tooltip 的日期：只留月/日，年份在这个尺度上没有信息量；月日次序按语言。 */
export function shortDay(date: string, language: string): string {
  return formatCalendarDay(date, language, { month: "numeric", day: "numeric" });
}
