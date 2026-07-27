// frontend/src/stores/reference-video-store.ts
import { create } from "zustand";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { ReferenceResource, ReferenceVideoUnit, TransitionType } from "@/types";

interface AddUnitPayload {
  prompt: string;
  references: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
}

interface PatchUnitPayload {
  prompt?: string;
  references?: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
}

/** Cache key isolating units per (project, episode) — switching projects with
 * the same episode number must not surface the previous project's units. */
export function referenceVideoCacheKey(projectName: string, episode: number): string {
  return `${projectName}::${episode}`;
}

interface ReferenceVideoStore {
  /** Keyed by `${projectName}::${episode}`. */
  unitsByEpisode: Record<string, ReferenceVideoUnit[]>;
  selectedUnitId: string | null;
  loading: boolean;
  error: string | null;

  loadUnits: (projectName: string, episode: number) => Promise<void>;
  addUnit: (projectName: string, episode: number, payload: AddUnitPayload) => Promise<ReferenceVideoUnit>;
  patchUnit: (projectName: string, episode: number, unitId: string, patch: PatchUnitPayload) => Promise<ReferenceVideoUnit>;
  deleteUnit: (projectName: string, episode: number, unitId: string) => Promise<void>;
  reorderUnits: (projectName: string, episode: number, unitIds: string[]) => Promise<void>;
  select: (unitId: string | null) => void;
}

// 每个 cache key 上最新一次 loadUnits 的序号：`loadUnits` 有多个入口（画布 effect 随
// 项目/剧集/任务完成失效重跑、重新派生后重拉、手动重试），同一 key 上可能有多个请求并发
// 在途。没有这道校验时，先发后到的旧响应会盖掉新响应里已经生成的成片，界面停在「无成片」
// 直到下一次失效。响应落定时序号已被更新的请求接管即丢弃，只让最新一次写回。
const _loadSeqByKey = new Map<string, number>();
let _loadSeq = 0;

/**
 * 作废该 cache key 上所有在途加载。
 *
 * 写入口（增删改排序）落定后调用：迟到的 GET 读的是写入之前的列表，直接写回会把用户刚做的
 * 编辑、增删或排序在界面上暂时撤销，直到下一次失效才复原。
 *
 * 一并结算 `loading`：被作废的那次加载会在响应落定时直接丢弃，不再自己复位，而作废它的是
 * 写入口、不是另一次加载，没有接管方来收尾——不在这里落定，画布会一直停在加载态。
 */
function invalidateInFlightLoads(cacheKey: string, set: (patch: { loading: false }) => void): void {
  _loadSeqByKey.set(cacheKey, ++_loadSeq);
  set({ loading: false });
}

export const useReferenceVideoStore = create<ReferenceVideoStore>((set) => ({
  unitsByEpisode: {},
  selectedUnitId: null,
  loading: false,
  error: null,

  loadUnits: async (projectName, episode) => {
    const cacheKey = referenceVideoCacheKey(projectName, episode);
    const seq = ++_loadSeq;
    _loadSeqByKey.set(cacheKey, seq);
    set({ loading: true, error: null });
    try {
      const { units } = await API.listReferenceVideoUnits(projectName, episode);
      if (_loadSeqByKey.get(cacheKey) !== seq) return;
      set((s) => ({
        unitsByEpisode: { ...s.unitsByEpisode, [cacheKey]: units },
        loading: false,
      }));
    } catch (e) {
      if (_loadSeqByKey.get(cacheKey) !== seq) return;
      set({ loading: false, error: errMsg(e) });
    }
  },

  addUnit: async (projectName, episode, payload) => {
    const { unit } = await API.addReferenceVideoUnit(projectName, episode, payload);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: [...list, unit] },
        selectedUnitId: unit.unit_id,
      };
    });
    return unit;
  },

  patchUnit: async (projectName, episode, unitId, patch) => {
    const { unit } = await API.patchReferenceVideoUnit(projectName, episode, unitId, patch);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: {
          ...s.unitsByEpisode,
          [key]: list.map((u) => (u.unit_id === unitId ? unit : u)),
        },
      };
    });
    return unit;
  },

  deleteUnit: async (projectName, episode, unitId) => {
    await API.deleteReferenceVideoUnit(projectName, episode, unitId);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: list.filter((u) => u.unit_id !== unitId) },
        selectedUnitId: s.selectedUnitId === unitId ? null : s.selectedUnitId,
      };
    });
  },

  reorderUnits: async (projectName, episode, unitIds) => {
    const { units } = await API.reorderReferenceVideoUnits(projectName, episode, unitIds);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => ({
      unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
    }));
  },

  select: (unitId) => set({ selectedUnitId: unitId }),
}));
