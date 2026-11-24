import { create } from "zustand";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { ReferenceUnitCapabilityMap, ReferenceVideoUnit } from "@/types";

interface AddUnitPayload {
  prompt: string;
  duration_seconds?: number;
  note?: string | null;
  after_unit_id?: string;
}

interface PatchUnitPayload {
  prompt?: string;
  duration_seconds?: number;
  note?: string | null;
}

export function referenceVideoCacheKey(projectName: string, episode: number): string {
  return `${projectName}::${episode}`;
}

interface ReferenceVideoStore {
  unitsByEpisode: Record<string, ReferenceVideoUnit[]>;
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

const _loadSeqByKey = new Map<string, number>();
let _loadSeq = 0;

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
