import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import { makeUsageRecord, makeUsageSummary } from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";


/** 只取记录请求的状态维度：进行中区的取数走同一个客户端方法，但不带时间范围。 */
function recordQueries(): { statuses: readonly string[] }[] {
  return vi
    .mocked(API.getUsageRecords)
    .mock.calls.filter(([query]) => query?.since !== undefined)
    .map(([query]) => ({ statuses: query?.statuses ?? [] }));
}

describe("UsageRecordsSection filters", () => {
  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    vi.spyOn(API, "getUsageRecords").mockResolvedValue({
      items: [makeUsageRecord()],
      next_cursor: null,
      total: 1,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("writes the picked time range into the URL with the u_ prefix", async () => {
    const { location } = renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "7 天" }));

    await waitFor(() =>
      expect(location.history.at(-1)).toBe("/app/settings?section=usage&u_range=7d"),
    );
  });

  it("restores the filters from the URL on load", async () => {
    renderUsageRecordsSection("section=usage&u_range=7d&u_provider=minimax&u_media=video");

    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "7 天" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(vi.mocked(API.getUsageRecords).mock.calls[0][0]).toMatchObject({
      providers: ["minimax"],
      mediaTypes: ["video"],
    });
  });

  it("clears a single filter from its chip and leaves the others in the URL", async () => {
    const { location } = renderUsageRecordsSection("section=usage&u_provider=minimax&u_media=video");
    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "清除筛选：MiniMax" }));

    await waitFor(() =>
      expect(location.history.at(-1)).toBe("/app/settings?section=usage&u_media=video"),
    );
  });

  it("sends a local-midnight since and the browser time zone in the real request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 2, 15, 8, 30));

    renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());

    const query = vi.mocked(API.getUsageSummary).mock.calls[0][0];
    // 默认 30 天含今天，起点是本地日零点的 2026-02-14，而不是 UTC 日切。
    expect(query?.since).toBe(new Date(2026, 1, 14, 0, 0, 0, 0).toISOString());
    expect(query?.tz).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });

  it("keeps the summary request untouched when only the status filter changes", async () => {
    renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole("button", { name: "失败" }));

    await waitFor(() => expect(recordQueries()).toContainEqual({ statuses: ["failed"] }));
    expect(API.getUsageSummary).toHaveBeenCalledTimes(1);
  });

  it("returns to the first page when a filter changes", async () => {
    vi.mocked(API.getUsageRecords).mockResolvedValue({
      items: [makeUsageRecord()],
      next_cursor: "cursor-2",
      total: 40,
    });
    renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() =>
      expect(
        vi.mocked(API.getUsageRecords).mock.calls.some(([query]) => query?.cursor === "cursor-2"),
      ).toBe(true),
    );
    const seen = vi.mocked(API.getUsageRecords).mock.calls.length;

    await userEvent.click(screen.getByRole("button", { name: "7 天" }));

    await waitFor(() =>
      expect(vi.mocked(API.getUsageRecords).mock.calls.length).toBeGreaterThan(seen),
    );
    // 筛选变化后发出的每一次记录请求都从第一页起，不带上一页留下的游标。
    for (const [query] of vi.mocked(API.getUsageRecords).mock.calls.slice(seen)) {
      expect(query?.cursor).toBeUndefined();
    }
  });
});
