import { create } from "zustand";

import { API } from "@/api";
import type { UsageRecordsQuery, UsageSessionsQuery, UsageSummaryQuery } from "@/api";
import type {
  CallType,
  UsageAgentSession,
  UsageRecord,
  UsageRecordDetail,
  UsageSummary,
  UsageView,
} from "@/types";

/** Time-range segments; `all` omits since, letting servers find earliest record dates. */
export type UsageTimeRange = "7d" | "30d" | "90d" | "all";

/** Status filters for records/running sections only, never summary. */
export type UsageStatusFilter =
  | "all"
  | "pending"
  | "success"
  | "failed"
  | "cancelled";

/** Page-wide filters; dimensions besides `status` apply to both summary and record requests. */
export interface UsageRecordsFilters {
  /** Generation calls exclude Agent records; Agent sessions aggregate Agent records by session. */
  view: UsageView;
  range: UsageTimeRange;
  /** Empty strings filter endpoint trials; null disables project filters. */
  project: string | null;
  provider: string | null;
  model: string | null;
  mediaType: CallType | null;
  /** Storyboard id, written only for needs-attention consecutive failures; summary APIs exclude this dimension. */
  segment: string | null;
  status: UsageStatusFilter;
}

export const USAGE_PAGE_SIZE = 20;

export const DEFAULT_USAGE_FILTERS: UsageRecordsFilters = {
  view: "generation",
  range: "30d",
  project: null,
  provider: null,
  model: null,
  mediaType: null,
  segment: null,
  status: "all",
};

/**
 * View-switch filter patches: provider/model candidates differ across views; media types/storyboards/status belong only to generation calls,
 * so clear them together rather than carry filters yielding empty results.
 */
export function viewPatch(view: UsageView): Partial<UsageRecordsFilters> {
  return { view, provider: null, model: null, mediaType: null, segment: null, status: "all" };
}

const RANGE_DAYS: Record<Exclude<UsageTimeRange, "all">, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
};

const MEDIA_TYPES: readonly CallType[] = ["image", "video", "text", "audio"];

const STATUS_FILTERS: readonly UsageStatusFilter[] = [
  "all",
  "pending",
  "success",
  "failed",
  "cancelled",
];

/** URL query key prefixes, distinct from other settings-section parameters. */
const QUERY_PREFIX = "u_";

function isRange(value: string | null): value is UsageTimeRange {
  return value === "7d" || value === "30d" || value === "90d" || value === "all";
}

/** Read filters from URL queries; missing/unrecognized values use defaults. */
export function parseUsageFilters(search: string): UsageRecordsFilters {
  const params = new URLSearchParams(search);
  const get = (key: string) => params.get(`${QUERY_PREFIX}${key}`);
  const range = get("range");
  const mediaType = get("media");
  const status = get("status");
  const project = get("project");
  return {
    view: get("view") === "agent" ? "agent" : DEFAULT_USAGE_FILTERS.view,
    range: isRange(range) ? range : DEFAULT_USAGE_FILTERS.range,
    // Endpoint-trial project names are empty strings, distinct from no project filters; check key presence.
    project: project === null ? null : project,
    provider: get("provider"),
    model: get("model"),
    mediaType: MEDIA_TYPES.find((type) => type === mediaType) ?? null,
    segment: get("segment"),
    status:
      STATUS_FILTERS.find((value) => value === status) ??
      DEFAULT_USAGE_FILTERS.status,
  };
}

/**
 * Write filters into URL queries, deleting default-equivalent keys for clean links.
 * Mutate supplied params in place, preserving other-section parameters.
 */
export function writeUsageFilters(
  params: URLSearchParams,
  filters: UsageRecordsFilters,
): URLSearchParams {
  const set = (key: string, value: string | null) => {
    if (value === null) params.delete(`${QUERY_PREFIX}${key}`);
    else params.set(`${QUERY_PREFIX}${key}`, value);
  };
  set("view", filters.view === DEFAULT_USAGE_FILTERS.view ? null : filters.view);
  set("range", filters.range === DEFAULT_USAGE_FILTERS.range ? null : filters.range);
  set("project", filters.project);
  set("provider", filters.provider);
  set("model", filters.model);
  set("media", filters.mediaType);
  set("segment", filters.segment);
  set(
    "status",
    filters.status === DEFAULT_USAGE_FILTERS.status ? null : filters.status,
  );
  return params;
}

/** Detail-record ids share filter queries without `u_` prefixes. */
export function parseUsageRecordId(search: string): number | null {
  const raw = new URLSearchParams(search).get("record");
  if (raw === null) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * Preset starts use local midnight then send instants, preventing early-morning calls falling into prior days.
 * `all` omits starts, letting servers choose earliest records.
 */
export function rangeSince(range: UsageTimeRange, now: Date): string | undefined {
  if (range === "all") return undefined;
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - (RANGE_DAYS[range] - 1));
  return start.toISOString();
}

/** Browser timezones defining server day boundaries. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** Generation calls are server-default views, omitting parameters to retain existing request strings. */
function viewParam(filters: UsageRecordsFilters): "agent" | undefined {
  return filters.view === "agent" ? "agent" : undefined;
}

function statusesFor(status: UsageStatusFilter): readonly string[] {
  if (status === "all") return ["success", "failed", "cancelled"];
  if (status === "pending") return ["pending"];
  return [status];
}

/** Shared summary/record dimensions, compared for summary refetch decisions. */
function sharedDimensions(filters: UsageRecordsFilters): string {
  return [
    // Views select project/provider/model filter candidates.
    filters.view,
    filters.range,
    // null no-project filters differ from empty endpoint trials; keys distinguish both.
    filters.project === null ? "-" : `p:${filters.project}`,
    filters.provider ?? "",
    filters.model ?? "",
    filters.mediaType ?? "",
    // Exclude storyboards from this key: summaries reject segment_id, so storyboard-only filters need no refetch.
  ].join("|");
}

interface UsageRecordsState {
  filters: UsageRecordsFilters;

  summary: UsageSummary | null;
  summaryLoading: boolean;
  summaryFailed: boolean;

  records: UsageRecord[];
  /** Current Agent-session pages share `records` loading/errors/totals/pagination. */
  sessions: UsageAgentSession[];
  recordsLoading: boolean;
  recordsFailed: boolean;
  /** Filtered total counts, records or sessions by view, independent of pages. */
  total: number;
  /** Visited page cursors; `[0]` is always null for initial pages. */
  cursors: (string | null)[];
  pageIndex: number;
  nextCursor: string | null;

  /** Pending calls without task representatives; task stores cover task-bound calls. */
  pendingRecords: UsageRecord[];

  detailId: number | null;
  detail: UsageRecordDetail | null;
  detailLoading: boolean;
  detailFailed: boolean;

  /**
   * Apply filters: first-page records/pending refetch, summary only when shared dimensions change;
   * opening sections always supplies `refetchSummary`, reflecting calls completed while away in KPIs/charts.
   */
  applyFilters: (
    filters: UsageRecordsFilters,
    options?: { refetchSummary?: boolean },
  ) => Promise<void>;
  /** Manual refresh of all three datasets using current filters. */
  refresh: () => Promise<void>;
  /** Summary-only refetch after language changes, since servers localize provider names per request. */
  refreshSummary: () => Promise<void>;
  /** Pending-only refetch for fallback polling while running sections are nonempty. */
  refreshPending: () => Promise<void>;
  goToPage: (direction: "prev" | "next") => Promise<void>;
  openDetail: (recordId: number) => Promise<void>;
  closeDetail: () => void;
}

const INITIAL = {
  filters: DEFAULT_USAGE_FILTERS,
  summary: null,
  summaryLoading: false,
  summaryFailed: false,
  records: [],
  sessions: [],
  recordsLoading: false,
  recordsFailed: false,
  total: 0,
  cursors: [null] as (string | null)[],
  pageIndex: 0,
  nextCursor: null,
  pendingRecords: [],
  detailId: null,
  detail: null,
  detailLoading: false,
  detailFailed: false,
};

interface RefreshRequest<T> {
  key: string;
  target: T;
  resolvers: Array<() => void>;
}

/** Same-target overlaps merge one follow-up; target changes invalidate old requests and retain latest only. */
function createRefreshCoordinator<T>(
  run: (target: T, signal: AbortSignal) => Promise<void>,
): (key: string, target: T) => Promise<void> {
  let active: RefreshRequest<T> | null = null;
  let queued: RefreshRequest<T> | null = null;
  let controller: AbortController | null = null;

  const drain = async () => {
    try {
      while (active !== null) {
        const current = active;
        controller = new AbortController();
        try {
          await run(current.target, controller.signal);
        } finally {
          current.resolvers.forEach((resolve) => resolve());
        }
        active = queued;
        queued = null;
      }
    } finally {
      // Unexpected run errors must settle queued callers/return idle, or later same-kind requests queue forever.
      queued?.resolvers.forEach((resolve) => resolve());
      active = null;
      queued = null;
      controller = null;
    }
  };

  return (key, target) =>
    new Promise<void>((resolve) => {
      const request = { key, target, resolvers: [resolve] };
      if (active === null) {
        active = request;
        void drain();
        return;
      }
      if (queued?.key === key) {
        queued.target = target;
        queued.resolvers.push(resolve);
        return;
      }
      queued?.resolvers.forEach((settle) => settle());
      queued = request;
      if (active.key !== key) controller?.abort();
    });
}

interface PageSlot {
  pageIndex: number;
  cursors: (string | null)[];
}

/**
 * List-page requests: both views share total/pagination fields, using one coordinator;
 * switching invalidates other-view requests, preventing late writes to current pagination.
 */
type PageTarget =
  | (PageSlot & { view: "generation"; query: UsageRecordsQuery })
  | (PageSlot & { view: "agent"; query: UsageSessionsQuery });

export const useUsageRecordsStore = create<UsageRecordsState>((set, get) => {
  const recordQuery = (filters: UsageRecordsFilters, cursor: string | null): UsageRecordsQuery => ({
    projectName: filters.project ?? undefined,
    providers: filters.provider ? [filters.provider] : undefined,
    models: filters.model ? [filters.model] : undefined,
    mediaTypes: filters.mediaType ? [filters.mediaType] : undefined,
    segmentIds: filters.segment ? [filters.segment] : undefined,
    statuses: statusesFor(filters.status),
    since: rangeSince(filters.range, new Date()),
    limit: USAGE_PAGE_SIZE,
    cursor: cursor ?? undefined,
    view: viewParam(filters),
  });

  const sessionQuery = (filters: UsageRecordsFilters, cursor: string | null): UsageSessionsQuery => ({
    projectName: filters.project ?? undefined,
    providers: filters.provider ? [filters.provider] : undefined,
    models: filters.model ? [filters.model] : undefined,
    since: rangeSince(filters.range, new Date()),
    limit: USAGE_PAGE_SIZE,
    cursor: cursor ?? undefined,
  });

  const runSummary = async (query: UsageSummaryQuery, signal: AbortSignal) => {
    set({ summaryLoading: true, summaryFailed: false });
    try {
      const summary = await API.getUsageSummary(query, { signal });
      if (signal.aborted) return;
      set({ summary, summaryLoading: false });
    } catch {
      // Invalidated predecessors never reset shared loading, owned by new rounds.
      if (signal.aborted) return;
      // Failed summaries clear old results belonging to previous filters, never misrepresenting current results.
      set({ summary: null, summaryLoading: false, summaryFailed: true });
    }
  };
  const scheduleSummary = createRefreshCoordinator(runSummary);
  const fetchSummary = (filters: UsageRecordsFilters) => {
    const query: UsageSummaryQuery = {
      projectName: filters.project ?? undefined,
      provider: filters.provider ?? undefined,
      model: filters.model ?? undefined,
      mediaType: filters.mediaType ?? undefined,
      since: rangeSince(filters.range, new Date()),
      tz: browserTimeZone(),
      view: viewParam(filters),
    };
    return scheduleSummary(JSON.stringify(query), query);
  };

  const runPage = async (target: PageTarget, signal: AbortSignal) => {
    set({ recordsLoading: true, recordsFailed: false });
    const paging = (page: { total: number; next_cursor: string | null }) => ({
      total: page.total,
      nextCursor: page.next_cursor,
      pageIndex: target.pageIndex,
      cursors: target.cursors,
      recordsLoading: false,
    });
    try {
      if (target.view === "agent") {
        const page = await API.getUsageSessions(target.query, { signal });
        if (signal.aborted) return;
        set({ sessions: page.items, ...paging(page) });
      } else {
        const page = await API.getUsageRecords(target.query, { signal });
        if (signal.aborted) return;
        set({ records: page.items, ...paging(page) });
      }
    } catch {
      if (signal.aborted) return;
      const failed = { total: 0, nextCursor: null, recordsLoading: false, recordsFailed: true };
      set(target.view === "agent" ? { sessions: [], ...failed } : { records: [], ...failed });
    }
  };
  const schedulePage = createRefreshCoordinator(runPage);
  /** Current-view lists: generation records or Agent sessions. */
  const fetchPage = (
    filters: UsageRecordsFilters,
    cursor: string | null,
    pageIndex: number,
    cursors: (string | null)[],
  ) => {
    const target: PageTarget =
      filters.view === "agent"
        ? { view: "agent", query: sessionQuery(filters, cursor), pageIndex, cursors }
        : { view: "generation", query: recordQuery(filters, cursor), pageIndex, cursors };
    const key = JSON.stringify({ view: target.view, query: target.query, pageIndex });
    return schedulePage(key, target);
  };

  const runPending = async (query: UsageRecordsQuery, signal: AbortSignal) => {
    try {
      const page = await API.getUsageRecords(query, { signal });
      if (signal.aborted) return;
      set({ pendingRecords: page.items });
    } catch {
      if (signal.aborted) return;
      set({ pendingRecords: [] });
    }
  };
  const schedulePending = createRefreshCoordinator(runPending);
  const fetchPending = (filters: UsageRecordsFilters) => {
    // Running sections ignore time filters; ongoing calls remain visible regardless of enqueue times.
    const query: UsageRecordsQuery = {
      projectName: filters.project ?? undefined,
      providers: filters.provider ? [filters.provider] : undefined,
      models: filters.model ? [filters.model] : undefined,
      mediaTypes: filters.mediaType ? [filters.mediaType] : undefined,
      segmentIds: filters.segment ? [filters.segment] : undefined,
      statuses: ["pending"],
      limit: USAGE_PAGE_SIZE,
      view: viewParam(filters),
    };
    return schedulePending(JSON.stringify(query), query);
  };

  let detailAbort: AbortController | null = null;

  return {
    ...INITIAL,

    applyFilters: async (filters, options = {}) => {
      const previous = get().filters;
      const summaryStale = sharedDimensions(previous) !== sharedDimensions(filters);
      const first = get().summary === null && !get().summaryLoading;
      // New queries reset cursors before responses, preventing refreshes using prior pagination.
      set({
        filters, pageIndex: 0, cursors: [null], nextCursor: null,
        ...(previous.view !== filters.view ? { records: [], sessions: [], total: 0, pendingRecords: [] } : {}),
      });
      await Promise.all([
        summaryStale || first || options.refetchSummary
          ? fetchSummary(filters)
          : Promise.resolve(),
        // Filter changes always return first pages; old cursors may no longer exist.
        fetchPage(filters, null, 0, [null]),
        // Agent records backfill terminal rows only, without ongoing calls.
        filters.view === "agent" ? Promise.resolve() : fetchPending(filters),
      ]);
    },

    refresh: async () => {
      const filters = get().filters;
      await Promise.all([
        fetchSummary(filters),
        fetchPage(filters, get().cursors[get().pageIndex] ?? null, get().pageIndex, get().cursors),
        filters.view === "agent" ? Promise.resolve() : fetchPending(filters),
      ]);
    },

    refreshSummary: async () => {
      await fetchSummary(get().filters);
    },

    refreshPending: async () => {
      if (get().filters.view === "agent") return;
      await fetchPending(get().filters);
    },

    goToPage: async (direction) => {
      const { filters, cursors, pageIndex, nextCursor } = get();
      if (direction === "prev") {
        if (pageIndex === 0) return;
        await fetchPage(filters, cursors[pageIndex - 1] ?? null, pageIndex - 1, cursors);
        return;
      }
      if (nextCursor === null) return;
      const grown = cursors.slice(0, pageIndex + 1).concat(nextCursor);
      await fetchPage(filters, nextCursor, pageIndex + 1, grown);
    },

    openDetail: async (recordId) => {
      if (get().detailId === recordId && get().detail !== null) return;
      detailAbort?.abort();
      const controller = new AbortController();
      detailAbort = controller;
      set({ detailId: recordId, detail: null, detailLoading: true, detailFailed: false });
      try {
        const detail = await API.getUsageRecord(recordId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        set({ detail, detailLoading: false });
      } catch {
        if (controller.signal.aborted) return;
        set({ detailLoading: false, detailFailed: true });
      }
    },

    closeDetail: () => {
      detailAbort?.abort();
      detailAbort = null;
      set({ detailId: null, detail: null, detailLoading: false, detailFailed: false });
    },
  };
});
