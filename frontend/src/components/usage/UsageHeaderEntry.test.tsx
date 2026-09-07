import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import type { UsageSummary } from "@/types";
import { makeUsageSummary } from "./usage-fixtures";
import {
  HEADER_PROJECT,
  renderUsageHeaderEntry,
  resetHeaderStores,
} from "./usage-header-test-utils";

function stubUsageApi(summary: UsageSummary | null) {
  vi.spyOn(API, "getUsageRecords").mockResolvedValue({
    items: [],
    next_cursor: null,
    total: 0,
  });
  const summarySpy = vi.spyOn(API, "getUsageSummary");
  if (summary) summarySpy.mockResolvedValue(summary);
  else summarySpy.mockRejectedValue(new Error("unavailable"));
  return summarySpy;
}

function seedSummary(summary: UsageSummary) {
  useUsageHeaderStore.setState({ projectName: HEADER_PROJECT, summary });
}

describe("UsageHeaderEntry", () => {
  beforeEach(() => {
    resetHeaderStores();
    vi.restoreAllMocks();
  });

  it("lists every currency with the primary one first", () => {
    stubUsageApi(null);
    seedSummary(
      makeUsageSummary({
        primary_currency: "CNY",
        kpi: {
          calls: 3,
          success: 3,
          failed: 0,
          cancelled: 0,
          success_rate: 1,
          cost: { CNY: 75.3, USD: 4.59 },
        },
      }),
    );

    renderUsageHeaderEntry();

    expect(screen.getByText("CN¥75.30")).toBeInTheDocument();
    expect(screen.getByText("$4.59")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAccessibleName(
      "使用记录 · 参考费用 CN¥75.30 + $4.59",
    );
  });

  it("shows a zero amount before any call is recorded", () => {
    stubUsageApi(null);

    renderUsageHeaderEntry();

    expect(screen.getByText("$0.00")).toBeInTheDocument();
  });

  it("adds the running badge and breathing glow only while tasks are active", () => {
    stubUsageApi(null);
    const { container } = renderUsageHeaderEntry();

    expect(screen.queryByText("3")).not.toBeInTheDocument();
    expect(container.querySelector(".animate-breathe")).toBeNull();

    act(() => {
      useTasksStore.setState({
        stats: {
          queued: 1,
          running: 2,
          cancelling: 0,
          succeeded: 0,
          failed: 0,
          cancelled: 0,
          total: 3,
        },
      });
    });

    expect(screen.getByText("3")).toBeInTheDocument();
    expect(container.querySelector(".animate-breathe")).not.toBeNull();
    expect(screen.getByRole("button")).toHaveAccessibleName(
      "使用记录 · 参考费用 $0.00 · 3 个任务进行中",
    );
  });

  it("tracks the popover state on aria-expanded and refetches when opened", async () => {
    const summarySpy = stubUsageApi(makeUsageSummary());
    useUsageHeaderStore.setState({ projectName: HEADER_PROJECT });

    renderUsageHeaderEntry();

    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(summarySpy).not.toHaveBeenCalled();

    fireEvent.click(button);

    expect(screen.getByRole("button", { name: "关闭使用记录面板" })).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
    await waitFor(() =>
      expect(summarySpy).toHaveBeenCalledWith(
        { projectName: HEADER_PROJECT },
        expect.anything(),
      ),
    );
  });
});
