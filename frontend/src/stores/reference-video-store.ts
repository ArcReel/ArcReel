import { create } from "zustand";
import { API } from "@/api";
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
  generate: (projectName: string, episode: number, unitId: string) => Promise<{ task_id: string; deduped: boolean }>;
  select: (unitId: string | null) => void;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const useReferenceVideoStore = create<ReferenceVideoStore>((set) => ({
  unitsByEpisode: {},
  selectedUnitId: null,
  loading: false,
  error: null,

  loadUnits: async (projectName, episode) => {
    set({ loading: true, error: null });
    try {
      const { units } = await API.listReferenceVideoUnits(projectName, episode);
      set((s) => ({
        unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
        loading: false,
      }));
    } catch (e) {
      set({ loading: false, error: errMsg(e) });
    }
  },

  addUnit: async (projectName, episode, payload) => {
    const { unit } = await API.addReferenceVideoUnit(projectName, episode, payload);
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
    set((s) => ({
      unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
    }));
  },

  generate: async (projectName, episode, unitId) => {
    return API.generateReferenceVideoUnit(projectName, episode, unitId);
  },

  select: (unitId) => set({ selectedUnitId: unitId }),
}));
