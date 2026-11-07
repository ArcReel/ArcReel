import { create } from "zustand";

import { API } from "@/api";
import type { UsageRecord, UsageRecordDetail, UsageSummary } from "@/types";

/** Number of recently completed calls in overlay right columns. */
export const HEADER_RECENT_LIMIT = 10;

/** Right columns include completed calls only; left-column tasks/pending calls cover ongoing work. */
const FINISHED_STATUSES = ["success", "failed", "cancelled"] as const;

interface UsageHeaderState {
  /** Header entry's current project; null for demos/no project, with no requests. */
  projectName: string | null;
  summary: UsageSummary | null;
  /** Latest N completed calls, descending by start time. */
  recent: UsageRecord[];
  /** Pending calls without task representatives; task stores supply task-bound calls. */
  pending: UsageRecord[];
  /** Prior refresh requests failed; panels may contain stale/missing data, requiring notices/retries. */
  loadFailed: boolean;

  detailId: number | null;
  detail: UsageRecordDetail | null;
  detailLoading: boolean;
  detailFailed: boolean;

  /** Switch attached projects: invalidate in-flight requests, clear old data, then fetch once. */
  setProject: (projectName: string | null) => Promise<void>;
  /** Refetch summary/recent records/pending calls. Shared entries merge in-flight calls into one follow-up round. */
  refresh: () => Promise<void>;
  openDetail: (recordId: number) => Promise<void>;
  closeDetail: () => void;
}

const EMPTY_DATA = {
  summary: null,
  recent: [] as UsageRecord[],
  pending: [] as UsageRecord[],
  loadFailed: false,
};

/**
 * Mount, overlay-open and stream-refresh entries share refreshes: merge calls arriving in flight into
 * one follow-up round, settling each caller independently to avoid interleaved writes. Targets always follow
 * currently attached projects, requiring no target buckets.
 */
function createMergedRefresh(run: (signal: AbortSignal) => Promise<void>) {
  let running = false;
  let queued: (() => void)[] = [];
  let controller: AbortController | null = null;

  const drain = async (resolvers: (() => void)[]) => {
    running = true;
    let current = resolvers;
    try {
      for (;;) {
        controller = new AbortController();
        try {
          await run(controller.signal);
        } finally {
          current.forEach((resolve) => resolve());
        }
        if (queued.length === 0) break;
        current = queued;
        queued = [];
      }
    } finally {
      // Unexpected run errors must also return idle and settle queued callers, or later refreshes queue forever without execution.
      queued.forEach((resolve) => resolve());
      queued = [];
      running = false;
      controller = null;
    }
  };

  return {
    call: () =>
      new Promise<void>((resolve) => {
        if (running) {
          queued.push(resolve);
          return;
        }
        void drain([resolve]);
      }),
    /** Invalidate in-flight requests; callers must refetch afterward or UI retains old-project data. */
    abort: () => controller?.abort(),
  };
}

export const useUsageHeaderStore = create<UsageHeaderState>((set, get) => {
  const run = async (signal: AbortSignal) => {
    const projectName = get().projectName;
    if (projectName === null) return;
    const [summary, recent, pending] = await Promise.all([
      API.getUsageSummary({ projectName }, { signal }).catch(() => null),
      API.getUsageRecords(
        { projectName, statuses: FINISHED_STATUSES, limit: HEADER_RECENT_LIMIT },
        { signal },
      ).catch(() => null),
      API.getUsageRecords({ projectName, statuses: ["pending"] }, { signal }).catch(() => null),
    ]);
    // Superseded invalidated requests never write: results belong to previous projects or newer rounds are already running.
    // Invalidation returns here; all three null values below mean actual failures, never aborts.
    if (signal.aborted || get().projectName !== projectName) return;
    set({
      summary: summary ?? get().summary,
      recent: recent?.items ?? get().recent,
      pending: pending?.items ?? get().pending,
      loadFailed: summary === null || recent === null || pending === null,
    });
  };

  const refresher = createMergedRefresh(run);

  let detailAbort: AbortController | null = null;

  return {
    projectName: null,
    ...EMPTY_DATA,
    detailId: null,
    detail: null,
    detailLoading: false,
    detailFailed: false,

    setProject: async (projectName) => {
      // Re-registering identical projects means header remounts (for example returning from settings); refetch events missed while away.
      if (get().projectName === projectName) {
        if (projectName !== null) await refresher.call();
        return;
      }
      refresher.abort();
      detailAbort?.abort();
      detailAbort = null;
      set({
        projectName,
        ...EMPTY_DATA,
        detailId: null,
        detail: null,
        detailLoading: false,
        detailFailed: false,
      });
      if (projectName === null) return;
      await refresher.call();
    },

    refresh: () => refresher.call(),

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
