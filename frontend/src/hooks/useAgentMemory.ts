import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { API } from "@/api";
import type { AgentMemoryOverview, AgentMemoryScope } from "@/types/agent-memory";
import { errMsg } from "@/utils/async";

export interface AgentMemoryState {
  /** Current scope directory contents; null before loading or on failure, reset to null immediately after scope switches. */
  overview: AgentMemoryOverview | null;
  loading: boolean;
  /** Current scope fetch-failure explanation, cleared on success. */
  error: string | null;
  /** Memory directory identity, passed unchanged by callers to write APIs so both levels share the same calls. */
  scope: AgentMemoryScope;
  /** Refetch lists, invalidating in-flight requests and cancelling them on unmount. */
  reload: () => Promise<void>;
}

/** One loaded result paired with its originating scope; `useMemo` stabilizes `target`, allowing reference comparison. */
interface LoadedMemory {
  target: AgentMemoryScope;
  data: AgentMemoryOverview | null;
  error: string | null;
}

/**
 * One memory directory's loading state: fetch once on mount; callers invoke `reload()` after writes.
 *
 * No polling/window-focus listeners: creators and Agents both write memories without server conflict detection; external refreshes
 * would replace editor contents while creators edit.
 *
 * Destructure `level` and project names before rebuilding scopes: callers commonly supply literal JSX objects, whose direct
 * inclusion in dependencies would refetch on every render.
 *
 * Pair results with originating scopes, exposing only matching pairs: after project switches, before new directory requests return,
 * old directory paths/file lists would otherwise appear beneath new project titles, and opening them sends old filenames to new directories.
 */
export function useAgentMemory(scope: AgentMemoryScope): AgentMemoryState {
  const level = scope.level;
  const projectName = scope.level === "project" ? scope.projectName : null;
  const target = useMemo<AgentMemoryScope>(
    () => (level === "user" ? { level: "user" } : { level: "project", projectName: projectName ?? "" }),
    [level, projectName],
  );

  const [loaded, setLoaded] = useState<LoadedMemory | null>(null);
  const [loading, setLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    // Taking-over requests rotate controllers (see docs/standards/frontend.md).
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const next = await API.getAgentMemory(target, { signal: controller.signal });
      // State-write checkpoint after network awaits: abort may follow already-resolved responses.
      if (controller.signal.aborted) return;
      setLoaded({ target, data: next, error: null });
    } catch (err) {
      if (controller.signal.aborted) return;
      setLoaded({ target, data: null, error: errMsg(err) });
    } finally {
      // Superseded requests yield: never reset shared state after invalidation, or newly activated successor loading state disappears.
      // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- reset is already in finally; the condition delegates invalidated-request resets to successors only
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [target]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch once on mount; reload synchronously activates loading state as controlled initialization
    void reload();
    return () => abortRef.current?.abort();
  }, [reload]);

  // Results from previous scopes count as unloaded; loading continues until current-scope responses arrive.
  const current = loaded !== null && loaded.target === target ? loaded : null;
  return {
    overview: current?.data ?? null,
    loading: current === null || loading,
    error: current?.error ?? null,
    scope: target,
    reload,
  };
}
