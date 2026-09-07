import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import type { UsageStatsBlock, UsageSummary } from "@/types";
import { makeUsageSummary } from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

function stats(overrides: Partial<UsageStatsBlock> = {}): UsageStatsBlock {
  return {
    calls: 20,
    success: 18,
    failed: 2,
    cancelled: 0,
    success_rate: 0.9,
    cost: { CNY: 12 },
    ...overrides,
  };
}

const BREAKDOWN: UsageSummary["breakdown"] = {
  project: {
    rows: [
      { project_name: "星海列车", ...stats() },
      { project_name: "", ...stats({ calls: 4, success: 4, failed: 0, success_rate: 1 }) },
    ],
    other: null,
  },
  provider: {
    rows: [
      { provider: "minimax", ...stats({ calls: 30, success: 20, failed: 10, success_rate: 0.667 }) },
      { provider: "google", ...stats() },
    ],
    other: { groups: 3, ...stats({ calls: 6, cost: { CNY: 1, USD: 0.5 } }) },
  },
  model: {
    rows: [
      { provider: "minimax", model: "hailuo-02", ...stats() },
      { provider: "google", model: "imagen-4", ...stats() },
    ],
    other: null,
  },
};

/** 构成表与筛选行都会出现供应商名，按区块取值才不会撞上 chip 的清除按钮。 */
function breakdownRow(name: RegExp): HTMLElement {
  const section = screen.getByRole("heading", { name: "构成" }).closest("section");
  return within(section as HTMLElement).getByRole("button", { name });
}

/** 断言写进 URL 的筛选时按键读，避免依赖 query 串里的键序。 */
function lastQuery(history: readonly string[]): URLSearchParams {
  return new URLSearchParams(history.at(-1)?.split("?")[1] ?? "");
}

describe("UsageRecordsSection breakdown", () => {
  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageRecords").mockResolvedValue({
      items: [],
      next_cursor: null,
      total: 0,
    });
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(
      makeUsageSummary({ breakdown: BREAKDOWN }),
    );
  });

  it("writes the clicked provider into the URL and marks the row pressed", async () => {
    const { location } = renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());
    const row = breakdownRow(/MiniMax/);
    expect(row).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(row);

    await waitFor(() => expect(lastQuery(location.history).get("u_provider")).toBe("minimax"));
    expect(breakdownRow(/MiniMax/)).toHaveAttribute("aria-pressed", "true");
  });

  it("sizes the share bar against all calls rather than the largest row", async () => {
    vi.mocked(API.getUsageSummary).mockResolvedValue(
      makeUsageSummary({ breakdown: BREAKDOWN, kpi: stats({ calls: 100 }) }),
    );
    renderUsageRecordsSection();

    const row = await screen.findByRole("button", { name: /MiniMax/ });
    expect(row.querySelector('[aria-hidden="true"]')).toHaveStyle({ width: "30%" });
  });

  it("clears the provider when the pressed row is clicked again", async () => {
    const { location } = renderUsageRecordsSection("section=usage&u_provider=minimax");
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());

    await userEvent.click(breakdownRow(/MiniMax/));

    await waitFor(() => expect(lastQuery(location.history).has("u_provider")).toBe(false));
  });

  it("writes the provider alongside the model when a model row is clicked", async () => {
    const { location } = renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "模型" }));
    await userEvent.click(breakdownRow(/hailuo-02/));

    await waitFor(() => {
      const query = lastQuery(location.history);
      expect(query.get("u_model")).toBe("hailuo-02");
      expect(query.get("u_provider")).toBe("minimax");
    });
  });

  it("labels an empty project name and filters on the empty string", async () => {
    const { location } = renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "项目" }));
    await userEvent.click(breakdownRow(/未命名/));

    // 端点试跑记录的项目名是空串，它与「不筛项目」不同，键要在但值为空。
    await waitFor(() => {
      const query = lastQuery(location.history);
      expect(query.has("u_project")).toBe(true);
      expect(query.get("u_project")).toBe("");
    });
  });

  it("renders the other row as plain text rather than a filter action", async () => {
    renderUsageRecordsSection();

    expect(await screen.findByText("其他 3 项")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /其他 3 项/ })).not.toBeInTheDocument();
  });
});

describe("UsageRecordsSection attention", () => {
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

  function mockAttention(attention: UsageSummary["attention"]) {
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(
      makeUsageSummary({ breakdown: BREAKDOWN, attention }),
    );
  }

  it("stays hidden and lets the breakdown span the full row when nothing is wrong", async () => {
    mockAttention([]);
    renderUsageRecordsSection();

    const heading = await screen.findByRole("heading", { name: "构成" });
    expect(screen.queryByRole("heading", { name: "需要关注" })).not.toBeInTheDocument();
    expect(heading.closest("section")).toHaveClass("col-span-12");
    expect(heading.closest("section")).not.toHaveClass("lg:col-span-7");
  });

  it("filters to the failing provider and model with the status switched to failed", async () => {
    mockAttention([
      {
        type: "failure_rate",
        provider: "minimax",
        model: "hailuo-02",
        success: 4,
        failed: 11,
        failure_rate: 0.733,
        overall_failure_rate: 0.12,
      },
    ]);
    const { location } = renderUsageRecordsSection();

    await userEvent.click(
      await screen.findByRole("button", { name: /MiniMax · hailuo-02 失败率偏高/ }),
    );

    await waitFor(() => {
      const query = lastQuery(location.history);
      expect(query.get("u_provider")).toBe("minimax");
      expect(query.get("u_model")).toBe("hailuo-02");
      expect(query.get("u_status")).toBe("failed");
    });
  });

  it("filters to the repeatedly failing target without touching the status", async () => {
    mockAttention([
      {
        type: "consecutive_failures",
        project_name: "星海列车",
        media_type: "video",
        segment_id: "E1S10",
        count: 4,
        first_failed_at: "2026-03-15T02:00:00+00:00",
        last_failed_at: "2026-03-15T08:00:00+00:00",
        last_error_code: "timeout",
      },
    ]);
    const { location } = renderUsageRecordsSection("section=usage&u_status=success");

    await userEvent.click(
      await screen.findByRole("button", { name: /星海列车 · 分镜 E1S10 连续失败/ }),
    );

    await waitFor(() => {
      const query = lastQuery(location.history);
      expect(query.get("u_project")).toBe("星海列车");
      expect(query.get("u_media")).toBe("video");
      expect(query.get("u_segment")).toBe("E1S10");
      // 状态不在这条规则要写的三维里，原来选的什么就还是什么。
      expect(query.get("u_status")).toBe("success");
    });
  });

  it("sends the segment filter to the records request and offers a chip to clear it", async () => {
    mockAttention([]);
    const { location } = renderUsageRecordsSection("section=usage&u_segment=E1S10");

    await waitFor(() =>
      expect(vi.mocked(API.getUsageRecords).mock.calls[0][0]).toMatchObject({
        segmentIds: ["E1S10"],
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "清除筛选：分镜 E1S10" }));

    await waitFor(() => expect(lastQuery(location.history).has("u_segment")).toBe(false));
  });
});
