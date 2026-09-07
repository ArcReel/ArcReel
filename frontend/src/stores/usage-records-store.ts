import { create } from "zustand";

import { API } from "@/api";
import type { UsageRecordsQuery, UsageSummaryQuery } from "@/api";
import type {
  CallType,
  UsageRecord,
  UsageRecordDetail,
  UsageSummary,
} from "@/types";

/** 时间范围分段；`all` 不传 since，由服务端取最早记录日。 */
export type UsageTimeRange = "7d" | "30d" | "90d" | "all";

/** 记录表与进行中区的状态筛选；不作用于 summary。 */
export type UsageStatusFilter =
  | "all"
  | "pending"
  | "success"
  | "failed"
  | "cancelled";

/** 一屏筛选；`status` 之外的维度同时作用于 summary 与记录请求。 */
export interface UsageRecordsFilters {
  range: UsageTimeRange;
  /** 空串筛选端点试跑记录，null 表示不按项目筛。 */
  project: string | null;
  provider: string | null;
  model: string | null;
  mediaType: CallType | null;
  /** 分镜 id；只有「需要关注」的连续失败条目会写入它，汇总接口不收这一维。 */
  segment: string | null;
  status: UsageStatusFilter;
}

export const USAGE_PAGE_SIZE = 20;

export const DEFAULT_USAGE_FILTERS: UsageRecordsFilters = {
  range: "30d",
  project: null,
  provider: null,
  model: null,
  mediaType: null,
  segment: null,
  status: "all",
};

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

/** URL query 的键前缀，与设置页其他 section 的参数区分开。 */
const QUERY_PREFIX = "u_";

function isRange(value: string | null): value is UsageTimeRange {
  return value === "7d" || value === "30d" || value === "90d" || value === "all";
}

/** 从 URL query 读筛选；缺失或不认识的取值回落到默认。 */
export function parseUsageFilters(search: string): UsageRecordsFilters {
  const params = new URLSearchParams(search);
  const get = (key: string) => params.get(`${QUERY_PREFIX}${key}`);
  const range = get("range");
  const mediaType = get("media");
  const status = get("status");
  const project = get("project");
  return {
    range: isRange(range) ? range : DEFAULT_USAGE_FILTERS.range,
    // 端点试跑记录的项目名是空串，它与「不筛项目」是两回事，故按是否有该键区分。
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
 * 把筛选写回一份 URL query；等于默认值的维度删键，让干净的视图有干净的链接。
 * 就地改写传入的 params，其他 section 的参数原样保留。
 */
export function writeUsageFilters(
  params: URLSearchParams,
  filters: UsageRecordsFilters,
): URLSearchParams {
  const set = (key: string, value: string | null) => {
    if (value === null) params.delete(`${QUERY_PREFIX}${key}`);
    else params.set(`${QUERY_PREFIX}${key}`, value);
  };
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

/** 详情弹窗的记录 id 与筛选同住一份 query，但不带 `u_` 前缀。 */
export function parseUsageRecordId(search: string): number | null {
  const raw = new URLSearchParams(search).get("record");
  if (raw === null) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * 预设范围的起点：按本地日零点算好再发时刻，凌晨的调用才不会被算进前一天。
 * `all` 不给起点，由服务端取最早记录日。
 */
export function rangeSince(range: UsageTimeRange, now: Date): string | undefined {
  if (range === "all") return undefined;
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - (RANGE_DAYS[range] - 1));
  return start.toISOString();
}

/** 浏览器时区；服务端按它切天。 */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

function statusesFor(status: UsageStatusFilter): readonly string[] {
  if (status === "all") return ["success", "failed", "cancelled"];
  if (status === "pending") return ["pending"];
  return [status];
}

/** summary 与记录共用的维度；比较它是否变化，决定要不要重取 summary。 */
function sharedDimensions(filters: UsageRecordsFilters): string {
  return [
    filters.range,
    // null（不筛项目）与空串（端点试跑）是两回事，键里要能分开。
    filters.project === null ? "-" : `p:${filters.project}`,
    filters.provider ?? "",
    filters.model ?? "",
    filters.mediaType ?? "",
    // 分镜不进这个键：汇总接口不收 segment_id，只筛分镜时 summary 不必重取。
  ].join("|");
}

interface UsageRecordsState {
  filters: UsageRecordsFilters;

  summary: UsageSummary | null;
  summaryLoading: boolean;
  summaryFailed: boolean;

  records: UsageRecord[];
  recordsLoading: boolean;
  recordsFailed: boolean;
  /** 筛选后的总数，与当前页无关。 */
  total: number;
  /** 已翻过的页游标，`[0]` 恒为 null（第一页）。 */
  cursors: (string | null)[];
  pageIndex: number;
  nextCursor: string | null;

  /** 无任务代表的 pending 调用；有任务的那部分由任务 store 提供。 */
  pendingRecords: UsageRecord[];

  detailId: number | null;
  detail: UsageRecordDetail | null;
  detailLoading: boolean;
  detailFailed: boolean;

  /** 应用一组筛选：回第一页重取记录与 pending，仅共用维度变化时重取 summary。 */
  applyFilters: (filters: UsageRecordsFilters) => Promise<void>;
  /** 手动刷新：按当前筛选重取全部三项。 */
  refresh: () => Promise<void>;
  /** 只重取 pending 调用，供进行中区非空时的兜底轮询使用。 */
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

/** 同一目标的重叠刷新合并补跑一次；目标变化则作废旧请求，只保留最新目标。 */
function createRefreshCoordinator<T>(
  run: (target: T, signal: AbortSignal) => Promise<void>,
): (key: string, target: T) => Promise<void> {
  let active: RefreshRequest<T> | null = null;
  let queued: RefreshRequest<T> | null = null;
  let controller: AbortController | null = null;

  const drain = async () => {
    while (active !== null) {
      const current = active;
      controller = new AbortController();
      await run(current.target, controller.signal);
      current.resolvers.forEach((resolve) => resolve());
      active = queued;
      queued = null;
    }
    controller = null;
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

interface RecordsTarget {
  query: UsageRecordsQuery;
  pageIndex: number;
  cursors: (string | null)[];
}

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
  });

  const runSummary = async (query: UsageSummaryQuery, signal: AbortSignal) => {
    set({ summaryLoading: true, summaryFailed: false });
    try {
      const summary = await API.getUsageSummary(query, { signal });
      if (signal.aborted) return;
      set({ summary, summaryLoading: false });
    } catch {
      // 被接管方作废时不碰共享状态：loading 归属新一轮，复位会打断它。
      if (signal.aborted) return;
      set({ summaryLoading: false, summaryFailed: true });
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
    };
    return scheduleSummary(JSON.stringify(query), query);
  };

  const runRecords = async (
    { query, pageIndex, cursors }: RecordsTarget,
    signal: AbortSignal,
  ) => {
    set({ recordsLoading: true, recordsFailed: false });
    try {
      const page = await API.getUsageRecords(query, { signal });
      if (signal.aborted) return;
      set({
        records: page.items,
        total: page.total,
        nextCursor: page.next_cursor,
        pageIndex,
        cursors,
        recordsLoading: false,
      });
    } catch {
      if (signal.aborted) return;
      set({ records: [], total: 0, nextCursor: null, recordsLoading: false, recordsFailed: true });
    }
  };
  const scheduleRecords = createRefreshCoordinator(runRecords);
  const fetchRecords = (
    filters: UsageRecordsFilters,
    cursor: string | null,
    pageIndex: number,
    cursors: (string | null)[],
  ) => {
    const query = recordQuery(filters, cursor);
    const target = { query, pageIndex, cursors };
    return scheduleRecords(JSON.stringify({ query, pageIndex }), target);
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
    // 进行中区不受时间范围影响：正在跑的调用无论何时排队都该看得见。
    const query: UsageRecordsQuery = {
      projectName: filters.project ?? undefined,
      providers: filters.provider ? [filters.provider] : undefined,
      models: filters.model ? [filters.model] : undefined,
      mediaTypes: filters.mediaType ? [filters.mediaType] : undefined,
      segmentIds: filters.segment ? [filters.segment] : undefined,
      statuses: ["pending"],
      limit: USAGE_PAGE_SIZE,
    };
    return schedulePending(JSON.stringify(query), query);
  };

  let detailAbort: AbortController | null = null;

  return {
    ...INITIAL,

    applyFilters: async (filters) => {
      const previous = get().filters;
      const summaryStale = sharedDimensions(previous) !== sharedDimensions(filters);
      const first = get().summary === null && !get().summaryLoading;
      set({ filters });
      await Promise.all([
        summaryStale || first ? fetchSummary(filters) : Promise.resolve(),
        // 筛选变化一律回第一页：停在旧游标上会翻到一个已经不存在的位置。
        fetchRecords(filters, null, 0, [null]),
        fetchPending(filters),
      ]);
    },

    refresh: async () => {
      const filters = get().filters;
      await Promise.all([
        fetchSummary(filters),
        fetchRecords(filters, get().cursors[get().pageIndex] ?? null, get().pageIndex, get().cursors),
        fetchPending(filters),
      ]);
    },

    refreshPending: async () => {
      await fetchPending(get().filters);
    },

    goToPage: async (direction) => {
      const { filters, cursors, pageIndex, nextCursor } = get();
      if (direction === "prev") {
        if (pageIndex === 0) return;
        await fetchRecords(filters, cursors[pageIndex - 1] ?? null, pageIndex - 1, cursors);
        return;
      }
      if (nextCursor === null) return;
      const grown = cursors.slice(0, pageIndex + 1).concat(nextCursor);
      await fetchRecords(filters, nextCursor, pageIndex + 1, grown);
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
