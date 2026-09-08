import { create } from "zustand";

import { API } from "@/api";
import type { UsageRecord, UsageRecordDetail, UsageSummary } from "@/types";

/** 悬浮层右栏「最近已结束」的条数。 */
export const HEADER_RECENT_LIMIT = 10;

/** 右栏只看已结束调用；进行中的那部分由左栏的任务与 pending 调用负责。 */
const FINISHED_STATUSES = ["success", "failed", "cancelled"] as const;

interface UsageHeaderState {
  /** 顶栏入口当前挂靠的项目；演示项目与无项目时为 null，此时不发请求。 */
  projectName: string | null;
  summary: UsageSummary | null;
  /** 最近 N 条已结束调用，按开始时刻倒序。 */
  recent: UsageRecord[];
  /** 无任务代表的 pending 调用；有任务的那部分由任务 store 提供。 */
  pending: UsageRecord[];

  detailId: number | null;
  detail: UsageRecordDetail | null;
  detailLoading: boolean;
  detailFailed: boolean;

  /** 切换挂靠项目：作废在途请求、清空上一项目的数据，再取一轮。 */
  setProject: (projectName: string | null) => Promise<void>;
  /** 重取 summary、最近记录与 pending 调用。多入口共享，在途时合并为结束后再跑一轮。 */
  refresh: () => Promise<void>;
  openDetail: (recordId: number) => Promise<void>;
  closeDetail: () => void;
}

const EMPTY_DATA = {
  summary: null,
  recent: [] as UsageRecord[],
  pending: [] as UsageRecord[],
};

/**
 * 多入口（挂载、打开悬浮层、事件流刷新信号）共用一份刷新：在途时把新的调用合并成
 * 「当前这轮结束后再跑一轮」，各调用方各自结算，避免并发刷新交错写回。目标恒为
 * 「当前挂靠项目」，故不需要按目标分桶。
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
      // run 意外抛出时也要回到空闲并结算排队方，否则之后的每次刷新都只会排队、永不执行。
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
    /** 作废在途请求；调用方负责随后补一轮，否则界面停在上一项目的数据上。 */
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
    // 被接管方作废时不写回：这一轮的结果属于上一个项目，或已有更新的一轮在跑。
    if (signal.aborted || get().projectName !== projectName) return;
    set({
      summary: summary ?? get().summary,
      recent: recent?.items ?? get().recent,
      pending: pending?.items ?? get().pending,
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
      // 同一项目再次登记是顶栏重新挂载（如从设置页返回）：离开期间的事件已错过，补一轮重取。
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
