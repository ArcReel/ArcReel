import { useEffect, useRef } from "react";
import {
  defaultTaskStats,
  selectNeedsFastPolling,
  useTasksStore,
} from "@/stores/tasks-store";
import { voidCall } from "@/utils/async";

/** Polling interval with in-flight tasks (or prior fetch failures), determining queued→running/intermediate-state visibility delay. */
const FAST_POLL_INTERVAL_MS = 3000;
/** Idle backoff without in-flight tasks: no states change; polling only detects tasks newly queued elsewhere (other clients/skills). */
const IDLE_POLL_INTERVAL_MS = 30000;

/**
 * Task-queue state refresh hook.
 *
 * Project-event SSE is the main terminal-state channel: backend `/projects/{name}/events/stream`
 * pushes `task_*` changes; `useProjectEventsSSE` immediately calls `refreshTasks`, without polling delays.
 * This hook handles two other responsibilities: refresh scopes (which project's tasks) and fallback polling.
 *
 * Switch polling frequency by **in-flight task presence**, never SSE connectivity:
 * - Backends emit terminal events only; intermediate queued→running states lack events and still require polling;
 *   low-frequency polling with in-flight tasks would delay execution-start/progress visibility versus before.
 * - SSE failures extend beyond `onError`: intermediaries may keep connections alive without delivering events, triggering no callbacks.
 *   In-flight task predicates retain high-frequency polling during these silent stalls, preserving fallback coverage.
 * Without in-flight tasks, states do not change; low-frequency polling only detects externally enqueued tasks.
 *
 * `null` `projectName` means no project filter (global tasks), never disabled:
 * disabling requires explicit `enabled: false`, or callers assuming null disables polling
 * actually keep fetching global task lists.
 */
export function useTaskRefresh(projectName?: string | null, enabled = true): void {
  // Select actions individually: selectorless useTasksStore() subscribes to entire stores, and this hook
  // lives at top-level StudioLayout, so each tasks/stats refresh would rerender the entire subtree.
  const setTasks = useTasksStore((s) => s.setTasks);
  const setStats = useTasksStore((s) => s.setStats);
  const setConnected = useTasksStore((s) => s.setConnected);
  const setRefreshScope = useTasksStore((s) => s.setRefreshScope);
  const refreshTasks = useTasksStore((s) => s.refreshTasks);
  const needsFastPolling = useTasksStore(selectNeedsFastPolling);
  // Initial true: scope effects reconcile initial mounts; frequency effects must not duplicate it.
  const prevNeedsFastPollingRef = useRef(true);

  useEffect(() => {
    if (!enabled) {
      // Disabling (for example readonly demo projects) means more than stopping fetches: old project tasks/stats
      // remaining in stores keep consumers displaying previous-project data unless cleared.
      // Clear scopes too, preventing late in-flight refresh responses from writing back.
      setRefreshScope(null);
      setTasks([]);
      setStats(defaultTaskStats);
      setConnected(false);
      return;
    }

    setRefreshScope({ projectName: projectName ?? null });
    return () => {
      setRefreshScope(null);
      setConnected(false);
    };
  }, [projectName, enabled, setTasks, setStats, setConnected, setRefreshScope]);

  // Fetch immediately on scope changes, without waiting for polling intervals. Separate from frequency effects below so each has
  // one rerun reason, avoiding effects having to infer whether projects switched or only frequencies changed.
  useEffect(() => {
    if (!enabled) return;
    voidCall(refreshTasks());
  }, [projectName, enabled, refreshTasks]);

  useEffect(() => {
    if (!enabled) return;

    // Idle→high frequency signals new work (typically optimistic occupancy markers), so fetch immediately
    // without waiting for intervals. Reverse high→idle has nothing to fetch and needs no reconciliation. Initial ref true
    // avoids duplicate initial-mount reconciliation here; scope effects above handle that.
    const wasFast = prevNeedsFastPollingRef.current;
    prevNeedsFastPollingRef.current = needsFastPolling;
    if (needsFastPolling && !wasFast) {
      voidCall(refreshTasks());
    }

    const intervalMs = needsFastPolling ? FAST_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
    const timer = setInterval(() => {
      voidCall(refreshTasks());
    }, intervalMs);

    return () => {
      clearInterval(timer);
    };
  }, [enabled, needsFastPolling, refreshTasks]);
}
