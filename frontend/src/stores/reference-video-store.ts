// frontend/src/stores/reference-video-store.ts
import { create } from "zustand";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { ReferenceUnitCapabilityMap, ReferenceVideoUnit } from "@/types";

interface AddUnitPayload {
  prompt: string;
  duration_seconds?: number;
  note?: string | null;
  /** Insert after this unit; append when omitted. */
  after_unit_id?: string;
}

interface PatchUnitPayload {
  prompt?: string;
  duration_seconds?: number;
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
  /**
   * Same-key per-unit server results (buckets/tiers/reference splits) arrive with lists, merging write responses
   * by unit. Edited references immediately update buckets/tiers; canvases never infer independently.
   */
  unitCapabilitiesByEpisode: Record<string, ReferenceUnitCapabilityMap>;
  selectedUnitId: string | null;
  loading: boolean;
  error: string | null;

  loadUnits: (projectName: string, episode: number) => Promise<void>;
  addUnit: (projectName: string, episode: number, payload: AddUnitPayload) => Promise<ReferenceVideoUnit>;
  patchUnit: (projectName: string, episode: number, unitId: string, patch: PatchUnitPayload) => Promise<ReferenceVideoUnit>;
  deleteUnit: (projectName: string, episode: number, unitId: string) => Promise<void>;
  moveUnit: (projectName: string, episode: number, unitId: string, afterUnitId: string | null) => Promise<void>;
  select: (unitId: string | null) => void;
}

// Latest loadUnits sequence per cache key: `loadUnits` has canvas project/episode/task-invalidation effects,
// postwrite refetches and manual retries, potentially concurrent on the same keys.
// Without guards, earlier requests arriving later overwrite fresh finished videos with old no-video data
// until next invalidation. Discard responses superseded by newer sequence values, accepting latest only.
const _loadSeqByKey = new Map<string, number>();
let _loadSeq = 0;

/**
 * Invalidate every in-flight load for this cache key.
 *
 * Call after add/edit/delete/reorder writes settle: late GET read prewrite lists, temporarily undoing fresh
 * changes visually until subsequent invalidation.
 *
 * Settle `loading` too: invalidated loads discard responses without resetting. Writes,
 * not successor loads, invalidated them, so nobody else owns cleanup; without settlement here canvases stay loading.
 */
function invalidateInFlightLoads(cacheKey: string, set: (patch: { loading: false }) => void): void {
  _loadSeqByKey.set(cacheKey, ++_loadSeq);
  set({ loading: false });
}

export const useReferenceVideoStore = create<ReferenceVideoStore>((set) => ({
  unitsByEpisode: {},
  unitCapabilitiesByEpisode: {},
  selectedUnitId: null,
  loading: false,
  error: null,

  loadUnits: async (projectName, episode) => {
    const cacheKey = referenceVideoCacheKey(projectName, episode);
    const seq = ++_loadSeq;
    _loadSeqByKey.set(cacheKey, seq);
    set({ loading: true, error: null });
    try {
      const { units, unit_capabilities } = await API.listReferenceVideoUnits(projectName, episode);
      if (_loadSeqByKey.get(cacheKey) !== seq) return;
      set((s) => ({
        unitsByEpisode: { ...s.unitsByEpisode, [cacheKey]: units },
        unitCapabilitiesByEpisode: { ...s.unitCapabilitiesByEpisode, [cacheKey]: unit_capabilities },
        loading: false,
      }));
    } catch (e) {
      if (_loadSeqByKey.get(cacheKey) !== seq) return;
      set({ loading: false, error: errMsg(e) });
    }
  },

  addUnit: async (projectName, episode, payload) => {
    const { unit, unit_capability } = await API.addReferenceVideoUnit(projectName, episode, payload);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      const anchor = payload.after_unit_id === undefined ? -1 : list.findIndex((u) => u.unit_id === payload.after_unit_id);
      const next = anchor === -1 ? [...list, unit] : [...list.slice(0, anchor + 1), unit, ...list.slice(anchor + 1)];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: next },
        unitCapabilitiesByEpisode: {
          ...s.unitCapabilitiesByEpisode,
          [key]: { ...s.unitCapabilitiesByEpisode[key], [unit.unit_id]: unit_capability },
        },
        selectedUnitId: unit.unit_id,
      };
    });
    return unit;
  },

  patchUnit: async (projectName, episode, unitId, patch) => {
    const { unit, unit_capability } = await API.patchReferenceVideoUnit(projectName, episode, unitId, patch);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: {
          ...s.unitsByEpisode,
          [key]: list.map((u) => (u.unit_id === unitId ? unit : u)),
        },
        unitCapabilitiesByEpisode: {
          ...s.unitCapabilitiesByEpisode,
          [key]: { ...s.unitCapabilitiesByEpisode[key], [unitId]: unit_capability },
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
      const { [unitId]: _removed, ...remaining } = s.unitCapabilitiesByEpisode[key] ?? {};
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: list.filter((u) => u.unit_id !== unitId) },
        unitCapabilitiesByEpisode: { ...s.unitCapabilitiesByEpisode, [key]: remaining },
        selectedUnitId: s.selectedUnitId === unitId ? null : s.selectedUnitId,
      };
    });
  },

  moveUnit: async (projectName, episode, unitId, afterUnitId) => {
    const { units } = await API.moveReferenceVideoUnit(projectName, episode, unitId, afterUnitId);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => ({
      unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
    }));
  },

  select: (unitId) => set({ selectedUnitId: unitId }),
}));
