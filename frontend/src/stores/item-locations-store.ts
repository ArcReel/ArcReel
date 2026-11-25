import { useEffect, useMemo } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import type { ItemLocation } from "@/types";

const MAX_LOCATE_BATCH_SIZE = 200;
const EMPTY_LOCATIONS: ReadonlyMap<string, ItemLocation> = new Map();

function chunkIds(ids: readonly string[]): string[][] {
  const chunks: string[][] = [];
  for (let i = 0; i < ids.length; i += MAX_LOCATE_BATCH_SIZE) {
    chunks.push(ids.slice(i, i + MAX_LOCATE_BATCH_SIZE));
  }
  return chunks;
}

interface ItemLocationsState {
  projectName: string | null;
  locations: ReadonlyMap<string, ItemLocation>;
  reset: (projectName?: string | null) => void;
  recordLocations: (projectName: string, items: readonly ItemLocation[]) => void;
  invalidateEpisodes: (projectName: string, episodes: readonly number[]) => void;
  notifySnapshotReady: (projectName: string, fingerprintChanged: boolean) => void;
  registerVisible: (projectName: string, itemIds: readonly string[]) => () => void;
  locateNow: (projectName: string, itemIds: readonly string[]) => Promise<ReadonlyMap<string, ItemLocation>>;
  getLocation: (projectName: string, itemId: string) => ItemLocation | undefined;
}

export const useItemLocationsStore = create<ItemLocationsState>((set, get) => {
  let scope = new AbortController();
  let generation = 0;
  let visibleCounts = new Map<string, number>();
  let pendingBatch = new Set<string>();
  let inflightIds = new Set<string>();
  let flushScheduled = false;

  const rotateScope = (nextProjectName: string | null) => {
    scope.abort();
    scope = new AbortController();
    generation += 1;
    visibleCounts = new Map();
    pendingBatch = new Set();
    inflightIds = new Set();
    flushScheduled = false;
    set({ projectName: nextProjectName, locations: new Map() });
  };

  const ensureProject = (projectName: string) => {
    if (get().projectName !== projectName) {
      rotateScope(projectName);
    }
  };

  const commitItems = (projectName: string, gen: number, items: readonly ItemLocation[]) => {
    if (gen !== generation || get().projectName !== projectName || items.length === 0) return;
    set((s) => {
      if (s.projectName !== projectName) return s;
      const next = new Map(s.locations);
      let changed = false;
      for (const item of items) {
        const prev = next.get(item.item_id);
        if (
          !prev ||
          prev.status !== item.status ||
          prev.episode !== item.episode ||
          prev.position !== item.position
        ) {
          next.set(item.item_id, item);
          changed = true;
        }
      }
      return changed ? { locations: next } : s;
    });
  };

  const fetchBatch = async (projectName: string, ids: string[], gen: number, signal: AbortSignal) => {
    if (ids.length === 0 || isDemoProject(projectName)) return;
    for (const id of ids) inflightIds.add(id);
    try {
      const results = await Promise.all(
        chunkIds(ids).map((chunk) => API.locateItems(projectName, chunk, { signal })),
      );
      if (signal.aborted || gen !== generation) return;
      const merged: ItemLocation[] = [];
      for (const res of results) {
        for (const item of res.items ?? []) merged.push(item);
      }
      commitItems(projectName, gen, merged);
    } catch {
      return;
    } finally {
      if (gen === generation) {
        for (const id of ids) inflightIds.delete(id);
      }
    }
  };

  const scheduleFlush = (projectName: string) => {
    if (flushScheduled) return;
    flushScheduled = true;
    const gen = generation;
    const signal = scope.signal;
    queueMicrotask(() => {
      flushScheduled = false;
      if (gen !== generation || signal.aborted || get().projectName !== projectName) return;
      const batch = [...pendingBatch].filter((id) => !inflightIds.has(id));
      pendingBatch.clear();
      if (batch.length === 0) return;
      void fetchBatch(projectName, batch, gen, signal);
    });
  };

  return {
    projectName: null,
    locations: new Map(),

    reset: (projectName = null) => {
      rotateScope(projectName);
    },

    recordLocations: (projectName, items) => {
      if (!projectName || items.length === 0) return;
      ensureProject(projectName);
      commitItems(projectName, generation, items);
    },

    invalidateEpisodes: (projectName, episodes) => {
      if (!projectName || get().projectName !== projectName || episodes.length === 0) return;
      const affected = new Set(episodes);
      set((s) => {
        if (s.projectName !== projectName) return s;
        const next = new Map(s.locations);
        let changed = false;
        for (const [id, loc] of next) {
          if (loc.episode === null || affected.has(loc.episode)) {
            next.delete(id);
            changed = true;
          }
        }
        return changed ? { locations: next } : s;
      });
      const toRequery: string[] = [];
      for (const id of visibleCounts.keys()) {
        if (!get().locations.has(id) && !inflightIds.has(id)) {
          toRequery.push(id);
        }
      }
      if (toRequery.length > 0) {
        for (const id of toRequery) pendingBatch.add(id);
        scheduleFlush(projectName);
      }
    },

    notifySnapshotReady: (projectName, fingerprintChanged) => {
      if (!projectName || isDemoProject(projectName) || get().projectName !== projectName) return;
      if (fingerprintChanged) {
        set((s) => (s.projectName === projectName ? { locations: new Map() } : s));
      }
      const cached = get().locations;
      const toRequery: string[] = [];
      for (const id of visibleCounts.keys()) {
        const entry = cached.get(id);
        if (!entry || entry.status === "unloaded") {
          toRequery.push(id);
        }
      }
      if (toRequery.length > 0) {
        for (const id of toRequery) pendingBatch.add(id);
        scheduleFlush(projectName);
      }
    },

    registerVisible: (projectName, itemIds) => {
      if (!projectName || isDemoProject(projectName) || itemIds.length === 0) return () => {};
      ensureProject(projectName);
      const uniqueIds = [...new Set(itemIds.filter(Boolean))];
      if (uniqueIds.length === 0) return () => {};
      const gen = generation;
      const cached = get().locations;
      let queued = false;
      for (const id of uniqueIds) {
        visibleCounts.set(id, (visibleCounts.get(id) ?? 0) + 1);
        if (!cached.has(id) && !inflightIds.has(id)) {
          pendingBatch.add(id);
          queued = true;
        }
      }
      if (queued) scheduleFlush(projectName);

      return () => {
        if (gen !== generation) return;
        for (const id of uniqueIds) {
          const next = (visibleCounts.get(id) ?? 0) - 1;
          if (next <= 0) visibleCounts.delete(id);
          else visibleCounts.set(id, next);
        }
      };
    },

    locateNow: async (projectName, itemIds) => {
      if (!projectName || isDemoProject(projectName) || itemIds.length === 0) return EMPTY_LOCATIONS;
      ensureProject(projectName);
      const uniqueIds = [...new Set(itemIds.filter(Boolean))];
      const missing = uniqueIds.filter((id) => {
        const entry = get().locations.get(id);
        return !entry || entry.status === "unloaded";
      });
      if (missing.length > 0) {
        await fetchBatch(projectName, missing, generation, scope.signal);
      }
      return get().projectName === projectName ? get().locations : EMPTY_LOCATIONS;
    },

    getLocation: (projectName, itemId) =>
      get().projectName === projectName ? get().locations.get(itemId) : undefined,
  };
});

export function useItemLocations(
  projectName: string | null | undefined,
  itemIds: readonly string[],
): ReadonlyMap<string, ItemLocation> {
  const key = useMemo(() => {
    if (!projectName || itemIds.length === 0) return "";
    return [...new Set(itemIds.filter(Boolean))].sort().join("\0");
  }, [projectName, itemIds]);

  const stableIds = useMemo(() => (key ? key.split("\0") : []), [key]);

  useEffect(() => {
    if (!projectName || stableIds.length === 0) return;
    return useItemLocationsStore.getState().registerVisible(projectName, stableIds);
  }, [projectName, stableIds]);

  return useItemLocationsStore(
    useShallow((s) => {
      if (!projectName || stableIds.length === 0 || s.projectName !== projectName) return EMPTY_LOCATIONS;
      const subset = new Map<string, ItemLocation>();
      for (const id of stableIds) {
        const entry = s.locations.get(id);
        if (entry) subset.set(id, entry);
      }
      return subset.size === 0 ? EMPTY_LOCATIONS : subset;
    }),
  );
}
