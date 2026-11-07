import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import i18n from "@/i18n";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import { makeUsageDaily, makeUsageSummary } from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

const CALLS_NAME = "按天堆叠的调用次数";
const COST_NAME = "按天堆叠的参考费用";

describe("UsageRecordsSection trend", () => {
  beforeAll(async () => {
    // Numeric assertions await actual charts, excluding cold-chunk loading from query waits.
    await import("./UsageTrendChart");
  });

  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageRecords").mockResolvedValue({
      items: [],
      next_cursor: null,
      total: 0,
    });
  });

  function mockSummary(overrides = {}) {
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary(overrides));
  }

  afterEach(async () => {
    await i18n.changeLanguage("zh");
  });

  it("renders the call counts with the language's thousands separator", async () => {
    // In tooltips/accessible tables, call counts sit beside localized success rates; browser-language
    // `toLocaleString()` would mix separator conventions at the same location.
    for (const [language, expected] of [
      ["zh", "12,340"],
      ["en", "12,340"],
      ["vi", "12.340"],
    ] as const) {
      await i18n.changeLanguage(language);
      mockSummary({ daily: makeUsageDaily(1, "2026-03-14", () => ({ success: 12_340 })) });
      const { unmount } = renderUsageRecordsSection();

      // Success/total columns each have one value, both from identical formatters.
      expect(await screen.findAllByText(expected)).toHaveLength(2);
      unmount();
    }
  });

  it("names the chart and repeats its data as a table", async () => {
    mockSummary({
      daily: makeUsageDaily(2, "2026-03-14", (index) => ({
        success: index === 0 ? 4 : 9,
        failed: index === 0 ? 1 : 0,
      })),
    });
    renderUsageRecordsSection();

    const chart = await screen.findByRole("img", { name: CALLS_NAME });
    expect(chart).toBeInTheDocument();

    const table = screen.getByRole("table", { name: CALLS_NAME });
    const rows = within(table).getAllByRole("row");
    // Headers plus two daily buckets.
    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByRole("rowheader")).toHaveTextContent("3/14");
    expect(within(rows[1]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "4",
      "1",
      "0",
      "5",
    ]);
  });

  it("switches to the reference cost view and footnotes the currencies left out", async () => {
    mockSummary({ daily: makeUsageDaily(3, "2026-03-13") });
    renderUsageRecordsSection();
    await screen.findByRole("img", { name: CALLS_NAME });

    await userEvent.click(screen.getByRole("button", { name: "参考费用" }));

    expect(await screen.findByRole("img", { name: COST_NAME })).toBeInTheDocument();
    // Nonprimary $4.59 is neither converted nor charted, only footnoted alongside primary CNY.
    expect(screen.getByText("只按 CNY 作图，$4.59 未计入")).toBeInTheDocument();
  });

  it("omits the excluded-currency clause when everything is in the primary currency", async () => {
    mockSummary({
      daily: makeUsageDaily(3, "2026-03-13"),
      kpi: { ...makeUsageSummary().kpi, cost: { CNY: 75.3 } },
    });
    renderUsageRecordsSection();
    await screen.findByRole("img", { name: CALLS_NAME });

    await userEvent.click(screen.getByRole("button", { name: "参考费用" }));

    expect(await screen.findByText("只按 CNY 作图")).toBeInTheDocument();
  });

  it("merges into weekly buckets past 90 points and says so", async () => {
    mockSummary({ daily: makeUsageDaily(95, "2026-03-01", () => ({ success: 1 })) });
    renderUsageRecordsSection();

    const table = await screen.findByRole("table", { name: "按周合并堆叠的调用次数" });
    expect(screen.getByText("按周合并")).toBeInTheDocument();
    // 95 days merge into 14 buckets; headers plus 14 rows.
    expect(within(table).getAllByRole("row")).toHaveLength(15);
    expect(within(table).getAllByRole("rowheader")[0]).toHaveTextContent("3/1 – 3/4 · 4 天合并");
  });

  it("stays on daily buckets at 90 points and shows no merge chip", async () => {
    mockSummary({ daily: makeUsageDaily(90, "2026-03-01", () => ({ success: 1 })) });
    renderUsageRecordsSection();

    await screen.findByRole("table", { name: CALLS_NAME });
    expect(screen.queryByText("按周合并")).not.toBeInTheDocument();
  });

  it("shows an empty line instead of an axis when the period has no days", async () => {
    mockSummary({ daily: [] });
    renderUsageRecordsSection();

    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());
    expect(await screen.findByText("这段时间还没有调用。")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: CALLS_NAME })).not.toBeInTheDocument();
  });
});
