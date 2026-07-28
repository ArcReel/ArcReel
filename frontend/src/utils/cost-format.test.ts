import { describe, expect, it } from "vitest";
import {
  costEntries,
  formatCost,
  formatCostOrZero,
  formatCurrencyAmount,
  totalBreakdown,
} from "./cost-format";

describe("cost-format", () => {
  it("formats multi-currency breakdowns in a stable order", () => {
    expect(costEntries({ USD: 1.2, CNY: 3.4, EUR: 5.6 })).toEqual([
      ["CNY", 3.4],
      ["USD", 1.2],
      ["EUR", 5.6],
    ]);
    expect(formatCost({ USD: 1.2, CNY: 3.4 })).toBe("CN¥3.40 + $1.20");
    expect(formatCurrencyAmount("CNY", 1.234)).toBe("CN¥1.23");
  });

  it("filters zero costs and falls back to em-dash", () => {
    expect(formatCost({ USD: 0, CNY: 0 })).toBe("—");
    expect(formatCostOrZero({ USD: 0 })).toBe("—");
    expect(formatCostOrZero(undefined)).toBe("—");
  });

  it("falls back for unknown currency codes", () => {
    expect(formatCurrencyAmount("POINTS", 12.345)).toBe("POINTS 12.35");
  });

  it("supports higher precision for line-item costs", () => {
    expect(formatCurrencyAmount("USD", 0.0006)).toBe("$0.00");
    expect(formatCurrencyAmount("USD", 0.0006, { maximumFractionDigits: 6 })).toBe("$0.0006");
    expect(formatCurrencyAmount("POINTS", 0.0006, { maximumFractionDigits: 6 })).toBe("POINTS 0.0006");
  });

  it("normalizes invalid fraction digit ranges", () => {
    expect(formatCurrencyAmount("USD", 1.2345, { minimumFractionDigits: 4, maximumFractionDigits: 2 })).toBe(
      "$1.2345",
    );
    expect(formatCurrencyAmount("POINTS", 1.2345, { minimumFractionDigits: 4, maximumFractionDigits: 2 })).toBe(
      "POINTS 1.2345",
    );
  });

  it("totals cost breakdowns by type", () => {
    expect(
      totalBreakdown({
        image: { CNY: 1.11115, USD: 2 },
        video: { CNY: 3 },
      }),
    ).toEqual({ CNY: 4.1112, USD: 2 });
  });
});
