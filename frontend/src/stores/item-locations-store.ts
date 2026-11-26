import { useEffect, useMemo } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { parseItemIdEpisode } from "@/utils/episode-display";
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
  snapshotRevision: number;
  projectName: string | null;
  locations: ReadonlyMap<string, ItemLocation>;
  reset: (projectName?: string | null) => void;
  recordLocations: (projectName: string, items: readonly ItemLocation[]) => void;
  resumePending: (projectName: string) => void;
  invalidateEpisodes: (projectName: string, episodes?: readonly number[]) => void;
  notifySnapshotReady: (projectName: string, fingerprintChanged: boolean) => void;
  registerVisible: (projectName: string, itemIds: readonly string[]) => () => void;
  locateNow: (projectName: string, itemIds: readonly string[], signal?: AbortSignal) => Promise<ReadonlyMap<string, ItemLocation>>;
  getLocation: (projectName: string, itemId: string) => ItemLocation | undefined;
}

export const useItemLocationsStore = create<ItemLocationsState>((set, get) => {
  let scope = new AbortController();
  let generation = 0;
  let visibleCounts = new Map<string, number>();
  let pendingBatch = new Set<string>();
  let inflightIds = new Map<string, symbol>();
  let flushScheduled = false;

  const rotateScope = (nextProjectName: string | null) => {
    scope.abort();
    scope = new AbortController();
    generation += 1;
    visibleCounts = new Map();
    pendingBatch = new Set();
    inflightIds = new Map();
    flushScheduled = false;
    set({ projectName: nextProjectName, locations: new Map() });
  };

  const ensureProject = (projectName: string) => {
    if (get().projectName !== projectName) {
      rotateScope(projectName);
    }
  };

  const commitItems = (projectName: string, gen: number, items: readonly ItemLocation[], ids?: readonly string[]) => {
    if (gen !== generation || get().projectName !== projectName || items.length === 0) return;
    set((s) => {
      if (s.projectName !== projectName) return s;
      const next = new Map(s.locations);
      let changed = false;
      for (const [index, item] of items.entries()) {
        const id = ids?.[index] ?? item.item_id;
        const prev = next.get(id);
        if (
          !prev ||
          prev.item_id !== item.item_id ||
          prev.status !== item.status ||
          prev.episode !== item.episode ||
          prev.position !== item.position
        ) {
          next.set(id, item);
          changed = true;
        }
      }
      return changed ? { locations: next } : s;
    });
  };

  const fetchChunk = async (projectName: string, ids: string[], gen: number, signal: AbortSignal) => {
    if (ids.length === 0 || isDemoProject(projectName)) return;
    const request = Symbol();
    for (const id of ids) {
      if (inflightIds.has(id) && visibleCounts.has(id)) pendingBatch.add(id);
      inflightIds.set(id, request);
    }
    try {
      const result = await API.locateItems(projectName, ids, { signal });
      if (signal.aborted || gen !== generation) return;
      const items: ItemLocation[] = [];
      const requestedIds: string[] = [];
      for (const [index, item] of result.items.entries()) {
        const id = ids[index];
        if (inflightIds.get(id) === request && !pendingBatch.has(id)) {
          items.push(item);
          requestedIds.push(id);
        }
      }
      commitItems(projectName, gen, items, requestedIds);
    } catch {
      return;
    } finally {
      if (gen === generation) {
        for (const id of ids) {
          if (inflightIds.get(id) !== request) continue;
          inflightIds.delete(id);
          if (signal.aborted && visibleCounts.has(id)) pendingBatch.add(id);
        }
        if (pendingBatch.size > 0) scheduleFlush(projectName);
      }
    }
  };

  const fetchBatch = (projectName: string, ids: string[], gen: number, signal: AbortSignal) =>
    Promise.all(chunkIds(ids).map((chunk) => fetchChunk(projectName, chunk, gen, signal)));

  const scheduleFlush = (projectName: string) => {
    if (flushScheduled) return;
    flushScheduled = true;
    const gen = generation;
    const signal = scope.signal;
    queueMicrotask(() => {
      flushScheduled = false;
      if (gen !== generation || signal.aborted || get().projectName !== projectName) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      const batch = [...pendingBatch].filter((id) => !inflightIds.has(id));
      for (const id of batch) pendingBatch.delete(id);
      if (batch.length === 0) return;
      void fetchBatch(projectName, batch, gen, signal);
    });
  };

  return {
    snapshotRevision: 0,
    projectName: null,
    locations: new Map(),

    resumePending: (projectName) => {
      if (get().projectName === projectName && pendingBatch.size) scheduleFlush(projectName);
    },

    reset: (projectName = null) => {
      rotateScope(projectName);
    },

    recordLocations: (projectName, items) => {
      if (!projectName || items.length === 0) return;
      ensureProject(projectName);
      const updates = new Map(items.map((item) => [item.item_id, item]));
      for (const [alias, cached] of get().locations) {
        if (alias === cached.item_id) continue;
        const latest = updates.get(cached.item_id);
        if (latest) updates.set(alias, latest);
      }
      for (const id of updates.keys()) {
        inflightIds.delete(id);
        pendingBatch.delete(id);
      }
      commitItems(projectName, generation, [...updates.values()], [...updates.keys()]);
    },

    invalidateEpisodes: (projectName, episodes) => {
      if (!projectName || get().projectName !== projectName || episodes?.length === 0) return;
      const affected = episodes && new Set(episodes);
      for (const id of inflightIds.keys()) {
        const episode = parseItemIdEpisode(id);
        if (!affected || episode === null || affected.has(episode)) inflightIds.delete(id);
      }
      set((s) => {
        if (s.projectName !== projectName) return s;
        const next = new Map(s.locations);
        let changed = false;
        for (const [id, loc] of next) {
          if (!affected || parseItemIdEpisode(id) === null || loc.episode === null || affected.has(loc.episode)) {
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
      if (fingerprintChanged) inflightIds.clear();
      set((s) => ({
        snapshotRevision: s.snapshotRevision + 1,
        locations: fingerprintChanged ? new Map() : s.locations,
      }));
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
        if ((!cached.has(id) || (visibleCounts.get(id) === 1 && cached.get(id)?.status === "unloaded")) && !inflightIds.has(id)) {
          pendingBatch.add(id);
          queued = true;
        }
      }
      if (queued) scheduleFlush(projectName);

      return () => {
        if (gen !== generation) return;
        for (const id of uniqueIds) {
          const next = (visibleCounts.get(id) ?? 0) - 1;
          if (next <= 0) {
            visibleCounts.delete(id);
            pendingBatch.delete(id);
          } else visibleCounts.set(id, next);
        }
      };
    },

    locateNow: async (projectName, itemIds, signal) => {
      if (!projectName || isDemoProject(projectName) || itemIds.length === 0) return EMPTY_LOCATIONS;
      ensureProject(projectName);
      const uniqueIds = [...new Set(itemIds.filter(Boolean))];
      const missing = uniqueIds.filter((id) => {
        const entry = get().locations.get(id);
        return !entry || entry.status === "unloaded";
      });
      if (missing.length > 0) {
        const combined = signal ? AbortSignal.any([scope.signal, signal]) : scope.signal;
        await fetchBatch(projectName, missing, generation, combined);
      }
      return get().projectName === projectName ? get().locations : EMPTY_LOCATIONS;
    },

    getLocation: (projectName, itemId) =>
      get().projectName === projectName ? get().locations.get(itemId) : undefined,
  };
});

export function onNextLocationSnapshot(
  projectName: string,
  revision: number,
  signal: AbortSignal,
  callback: () => void,
): void {
  const state = useItemLocationsStore.getState();
  if (signal.aborted || state.projectName !== projectName) return;
  if (state.snapshotRevision !== revision) {
    callback();
    return;
  }
  const unsubscribe = useItemLocationsStore.subscribe((next) => {
    if (next.projectName !== projectName || next.snapshotRevision === revision) return;
    unsubscribe();
    signal.removeEventListener("abort", unsubscribe);
    callback();
  });
  signal.addEventListener("abort", unsubscribe, { once: true });
}

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
