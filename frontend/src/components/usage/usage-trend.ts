import type { CallType, UsageDailyBucket } from "@/types";
import { MEDIA_META, formatCalendarDay } from "./usage-record-format";

export type TrendMetric = "calls" | "cost";

export const DAILY_POINT_LIMIT = 90;

export const MERGE_DAYS = 7;

const MEDIA_ORDER: readonly CallType[] = ["image", "audio", "video", "text"];

export interface TrendBucket {
  from: string;
  to: string;
  days: number;
  success: number;
  failed: number;
  cancelled: number;
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

export function bucketSuccessRate(bucket: TrendBucket): number | null {
  const settled = bucket.success + bucket.failed;
  return settled === 0 ? null : bucket.success / settled;
}

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

export function shortDay(date: string, language: string): string {
  return formatCalendarDay(date, language, { month: "numeric", day: "numeric" });
}
