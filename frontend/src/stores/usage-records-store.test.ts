import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import type { UsageRecordsQuery } from "@/api";
import {
  makeUsageAgentSession,
  makeUsageRecord,
  makeUsageSummary,
} from "@/components/usage/usage-fixtures";
import { createDeferred } from "@/test/deferred";
import type { UsageAgentSession, UsageRecord } from "@/types";
import { DEFAULT_USAGE_FILTERS, rangeSince, useUsageRecordsStore, viewPatch } from "./usage-records-store";

interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
}

const GENERATION = DEFAULT_USAGE_FILTERS;
const AGENT = { ...DEFAULT_USAGE_FILTERS, ...viewPatch("agent") };

const EMPTY_RECORDS: Page<UsageRecord> = { items: [], next_cursor: null, total: 0 };

function recordPage(nextCursor: string | null, total = 73): Page<UsageRecord> {
  return { items: [makeUsageRecord()], next_cursor: nextCursor, total };
}

function sessionPage(nextCursor: string | null, total = 1): Page<UsageAgentSession> {
  return { items: [makeUsageAgentSession()], next_cursor: nextCursor, total };
}

function isPending(query: UsageRecordsQuery = {}): boolean {
  return query.statuses?.length === 1 && query.statuses[0] === "pending";
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    promise.then(resolve, reject);
  });
}

function listCursors(spy: typeof API.getUsageRecords | typeof API.getUsageSessions) {
  return vi
    .mocked(spy)
    .mock.calls.filter(([query]) => !isPending(query))
    .map(([query]) => query?.cursor);
}

describe("usage-records-store", () => {
  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
  });

  it("refreshes from page one when the pending cursor belongs to yesterday's date window", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      const before = new Date(2026, 10, 24, 23, 59);
      const after = new Date(2026, 10, 25, 0, 1);
      vi.setSystemTime(before);
      const filters = { ...GENERATION, range: "7d" as const };
      const nextPage = createDeferred<Page<UsageRecord>>();
      const records = vi.spyOn(API, "getUsageRecords").mockImplementation((query, options) => {
        if (query?.cursor) return abortable(nextPage.promise, options?.signal);
        return Promise.resolve(isPending(query) ? EMPTY_RECORDS : recordPage("page-2"));
      });
      const store = useUsageRecordsStore.getState();
      await store.applyFilters(filters);
      const page = store.goToPage("next");

      vi.setSystemTime(after);
      const refresh = store.refresh();
      nextPage.resolve(recordPage(null));
      await Promise.all([page, refresh]);

      expect(useUsageRecordsStore.getState().pageIndex).toBe(0);
      const query = records.mock.calls.filter(([value]) => !isPending(value)).at(-1)?.[0];
      expect(query).toMatchObject({ since: rangeSince(filters.range, after), cursor: undefined });
      expect(vi.mocked(API.getUsageSummary).mock.lastCall?.[0]?.since).toBe(query?.since);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the Agent view's paging when a generation request lands after the switch", async () => {
    const lateRecords = createDeferred<Page<UsageRecord>>();
    vi.spyOn(API, "getUsageRecords").mockImplementation((query = {}, options) =>
      isPending(query) ? Promise.resolve(EMPTY_RECORDS) : abortable(lateRecords.promise, options?.signal),
    );
    vi.spyOn(API, "getUsageSessions").mockResolvedValue(sessionPage(null));

    const generation = useUsageRecordsStore.getState().applyFilters(GENERATION);
    await useUsageRecordsStore.getState().applyFilters(AGENT);
    lateRecords.resolve(recordPage("rec-cursor-2"));
    await generation;

    const state = useUsageRecordsStore.getState();
    expect(state.filters.view).toBe("agent");
    expect(state.total).toBe(1);
    expect(state.nextCursor).toBeNull();
    expect(state.pageIndex).toBe(0);
    expect(state.records).toEqual([]);
    expect(state.recordsLoading).toBe(false);
  });

  it("sends each list endpoint its own cursor while switching views back and forth", async () => {
    const lateSecondPage = createDeferred<Page<UsageRecord>>();
    vi.spyOn(API, "getUsageRecords").mockImplementation((query = {}, options) => {
      if (isPending(query)) return Promise.resolve(EMPTY_RECORDS);
      if (query.cursor === undefined) return Promise.resolve(recordPage("rec-cursor-2"));
      return abortable(lateSecondPage.promise, options?.signal);
    });
    vi.spyOn(API, "getUsageSessions").mockImplementation((query = {}) =>
      Promise.resolve(sessionPage(query.cursor === undefined ? "sess-cursor-2" : "sess-cursor-3", 30)),
    );
    const store = useUsageRecordsStore.getState();
    await store.applyFilters(GENERATION);

    const turn = store.goToPage("next");
    await store.applyFilters(AGENT);
    lateSecondPage.resolve(recordPage("rec-cursor-3"));
    await turn;
    expect(useUsageRecordsStore.getState()).toMatchObject({
      total: 30,
      nextCursor: "sess-cursor-2",
      pageIndex: 0,
      cursors: [null],
    });

    await store.goToPage("next");
    await store.applyFilters(GENERATION);
    await store.goToPage("next");

    expect(listCursors(API.getUsageRecords)).toEqual([undefined, "rec-cursor-2", undefined, "rec-cursor-2"]);
    expect(listCursors(API.getUsageSessions)).toEqual([undefined, "sess-cursor-2"]);
  });

  it.each([AGENT, { ...GENERATION, model: "another-model" }])(
    "refreshes the first page while a changed query is still loading (%j)",
    async (filters) => {
      const firstPage = createDeferred<Page<UsageRecord>>();
      const sessions = createDeferred<Page<UsageAgentSession>>();
      vi.spyOn(API, "getUsageRecords").mockImplementation((query = {}, options) => {
        if (isPending(query)) return Promise.resolve(EMPTY_RECORDS);
        if (query.models) return abortable(firstPage.promise, options?.signal);
        return Promise.resolve(recordPage(query.cursor ? null : "rec-cursor-2"));
      });
      vi.spyOn(API, "getUsageSessions").mockImplementation((_query, options) =>
        abortable(sessions.promise, options?.signal),
      );
      const store = useUsageRecordsStore.getState();
      await store.applyFilters(GENERATION);
      await store.goToPage("next");
      expect(useUsageRecordsStore.getState().pageIndex).toBe(1);

      const switching = store.applyFilters(filters);
      const refreshing = store.refresh();
      firstPage.resolve(recordPage(null));
      sessions.resolve(sessionPage(null));
      await Promise.all([switching, refreshing]);

      const requests = filters.view === "agent"
        ? listCursors(API.getUsageSessions)
        : vi.mocked(API.getUsageRecords).mock.calls
          .filter(([query]) => query?.models && !isPending(query))
          .map(([query]) => query?.cursor);
      expect(requests.length).toBeGreaterThan(0);
      expect(requests.every((cursor) => cursor === undefined)).toBe(true);
      expect(useUsageRecordsStore.getState()).toMatchObject({ pageIndex: 0, cursors: [null] });
    },
  );
});
