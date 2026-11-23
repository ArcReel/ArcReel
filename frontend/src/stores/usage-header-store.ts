import { create } from "zustand";

import { API } from "@/api";
import type { UsageRecord, UsageRecordDetail, UsageSummary } from "@/types";

export const HEADER_RECENT_LIMIT = 10;

const FINISHED_STATUSES = ["success", "failed", "cancelled"] as const;

interface UsageHeaderState {
  projectName: string | null;
  summary: UsageSummary | null;
  recent: UsageRecord[];
  pending: UsageRecord[];
  loadFailed: boolean;

  detailId: number | null;
  detail: UsageRecordDetail | null;
  detailLoading: boolean;
  detailFailed: boolean;

  setProject: (projectName: string | null) => Promise<void>;
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
