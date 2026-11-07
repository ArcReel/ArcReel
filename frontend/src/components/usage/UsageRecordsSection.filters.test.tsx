import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import i18n from "@/i18n";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import { makeUsageRecord, makeUsageSummary } from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";


/** Read record-request statuses only; running-region fetching shares client methods but omits time ranges. */
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

  afterEach(async () => {
    vi.useRealTimers();
    await i18n.changeLanguage("zh");
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
    // Default 30 days include today; start is local midnight 2026-02-14, not UTC day boundaries.
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

  it("drops the previous summary and shows a notice when the next summary request fails", async () => {
    renderUsageRecordsSection();
    await waitFor(() => expect(useUsageRecordsStore.getState().summary).not.toBeNull());
    vi.mocked(API.getUsageSummary).mockRejectedValue(new Error("boom"));

    await userEvent.click(screen.getByRole("button", { name: "7 天" }));

    expect(await screen.findByRole("status")).toHaveTextContent("加载失败");
    // Previous-filter summaries must not impersonate current-filter results.
    expect(useUsageRecordsStore.getState().summary).toBeNull();
  });

  it("refetches the summary when the section is opened again with the same filters", async () => {
    const first = renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));
    first.unmount();

    renderUsageRecordsSection();

    // Stores persist across mounts; reopenings refetch summaries so calls ended while absent enter KPIs.
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(2));
  });

  it("shows the load failure instead of the empty state when the records request fails", async () => {
    vi.mocked(API.getUsageRecords).mockRejectedValue(new Error("boom"));

    renderUsageRecordsSection();

    expect(await screen.findByRole("status")).toHaveTextContent("加载失败");
    expect(screen.queryByText(/还没有使用记录/)).not.toBeInTheDocument();
  });

  it("refetches only the summary after the language changes", async () => {
    renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(useUsageRecordsStore.getState().summary).not.toBeNull());
    const recordCalls = vi.mocked(API.getUsageRecords).mock.calls.length;

    await act(() => i18n.changeLanguage("en"));

    // Servers render provider names by request language; refetch summaries only.
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(2));
    expect(vi.mocked(API.getUsageRecords).mock.calls.length).toBe(recordCalls);
  });

  it("filters by a model whose ID looks like a placeholder value and clears it from the all option", async () => {
    // Custom-provider model IDs are unrestricted; any strings may be real models.
    vi.mocked(API.getUsageSummary).mockResolvedValue(
      makeUsageSummary({
        filter_options: { projects: [], providers: [], models: [{ provider: "custom-1", model: "__all__" }] },
      }),
    );
    const { location } = renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("combobox", { name: "模型" }));
    await userEvent.click(await screen.findByRole("option", { name: "__all__" }));
    await waitFor(() =>
      expect(location.history.at(-1)).toBe("/app/settings?section=usage&u_model=__all__"),
    );

    await userEvent.click(screen.getByRole("combobox", { name: "模型" }));
    await userEvent.click(await screen.findByRole("option", { name: "全部模型" }));
    await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=usage"));
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
    // Every post-filter-change record request restarts first pages without previous-page cursors.
    for (const [query] of vi.mocked(API.getUsageRecords).mock.calls.slice(seen)) {
      expect(query?.cursor).toBeUndefined();
    }
  });
});
