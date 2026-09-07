import { describe, expect, it } from "vitest";

import { makeUsageDaily } from "./usage-fixtures";
import {
  amountTicks,
  bucketCalls,
  bucketCost,
  bucketSuccessRate,
  buildTrendBuckets,
  countTicks,
  seriesFor,
  shortDay,
  tickEvery,
} from "./usage-trend";

describe("buildTrendBuckets", () => {
  it("keeps one bucket per day up to 90 points", () => {
    const { buckets, weekly } = buildTrendBuckets(makeUsageDaily(90));

    expect(weekly).toBe(false);
    expect(buckets).toHaveLength(90);
    expect(buckets[0]).toMatchObject({ from: "2026-03-01", to: "2026-03-01", days: 1 });
  });

  it("merges 7 days per bucket past 90 points, leaving the remainder in the earliest bucket", () => {
    const { buckets, weekly } = buildTrendBuckets(makeUsageDaily(95));

    expect(weekly).toBe(true);
    // 95 = 13 个整周 + 4 天余数；余数落在最早那桶，最右一桶始终是完整一周。
    expect(buckets).toHaveLength(14);
    expect(buckets[0]).toMatchObject({ from: "2026-03-01", to: "2026-03-04", days: 4 });
    expect(buckets.at(-1)).toMatchObject({ to: "2026-06-03", days: 7 });
  });

  it("sums counts and per-media cost inside a merged bucket", () => {
    const daily = makeUsageDaily(91, "2026-03-01", (index) => ({
      success: index === 90 ? 3 : 0,
      failed: index === 89 ? 2 : 0,
      cancelled: index === 88 ? 1 : 0,
      cost_by_media_type: { image: 1.5, video: 0, text: 0, audio: 0 },
    }));

    const last = buildTrendBuckets(daily).buckets.at(-1)!;

    expect(last).toMatchObject({ success: 3, failed: 2, cancelled: 1 });
    expect(last.cost.image).toBeCloseTo(10.5);
    expect(bucketCalls(last)).toBe(6);
    expect(bucketCost(last)).toBeCloseTo(10.5);
  });
});

describe("bucketSuccessRate", () => {
  it("sums the merged bucket before dividing rather than averaging daily rates", () => {
    const daily = makeUsageDaily(91, "2026-03-01", (index) => ({
      success: index === 90 ? 1 : 0,
      failed: index === 89 ? 3 : 0,
    }));

    // 逐日成功率的平均是 0.5，对桶求和后是 1 / 4。
    expect(bucketSuccessRate(buildTrendBuckets(daily).buckets.at(-1)!)).toBe(0.25);
  });

  it("has no rate when nothing settled", () => {
    const { buckets } = buildTrendBuckets(
      makeUsageDaily(1, "2026-03-01", () => ({ cancelled: 2 })),
    );

    expect(bucketSuccessRate(buckets[0])).toBeNull();
  });
});

describe("countTicks", () => {
  it("only produces whole numbers", () => {
    for (const max of [0, 1, 3, 7, 9, 23, 140, 1999]) {
      expect(countTicks(max).every(Number.isInteger)).toBe(true);
    }
  });

  it("covers the maximum with at most four steps", () => {
    expect(countTicks(3)).toEqual([0, 1, 2, 3]);
    expect(countTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(countTicks(0)).toEqual([0, 1]);
    expect(countTicks(23).at(-1)).toBeGreaterThanOrEqual(23);
  });
});

describe("amountTicks", () => {
  it("rounds the top up to a readable amount and quarters it", () => {
    expect(amountTicks(0.9)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(amountTicks(0)).toEqual([0, 1]);
    expect(amountTicks(42).at(-1)).toBe(50);
  });
});

describe("series", () => {
  it("stacks calls by outcome and cost by media type", () => {
    expect(seriesFor("calls").map((entry) => entry.key)).toEqual([
      "success",
      "failed",
      "cancelled",
    ]);
    expect(seriesFor("cost").map((entry) => entry.key)).toEqual([
      "image",
      "audio",
      "video",
      "text",
    ]);
    expect(seriesFor("calls").find((entry) => entry.key === "cancelled")?.hatched).toBe(true);
  });
});

describe("axis helpers", () => {
  it("thins date labels so they never overlap", () => {
    expect(tickEvery(10, 640)).toBe(1);
    expect(tickEvery(90, 640)).toBe(9);
    expect(tickEvery(0, 0)).toBe(1);
  });

  it("drops the year from bucket labels", () => {
    expect(shortDay("2026-03-05")).toBe("3/5");
    expect(shortDay("2026-11-20")).toBe("11/20");
  });
});
