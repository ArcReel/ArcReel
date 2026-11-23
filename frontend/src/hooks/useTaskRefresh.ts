import { useEffect, useRef } from "react";
import {
  defaultTaskStats,
  selectNeedsFastPolling,
  useTasksStore,
} from "@/stores/tasks-store";
import { voidCall } from "@/utils/async";

const FAST_POLL_INTERVAL_MS = 3000;
const IDLE_POLL_INTERVAL_MS = 30000;

export function useTaskRefresh(projectName?: string | null, enabled = true): void {
  const setTasks = useTasksStore((s) => s.setTasks);
  const setStats = useTasksStore((s) => s.setStats);
  const setConnected = useTasksStore((s) => s.setConnected);
  const setRefreshScope = useTasksStore((s) => s.setRefreshScope);
  const refreshTasks = useTasksStore((s) => s.refreshTasks);
  const needsFastPolling = useTasksStore(selectNeedsFastPolling);
  const prevNeedsFastPollingRef = useRef(true);

  useEffect(() => {
    if (!enabled) {
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

  useEffect(() => {
    if (!enabled) return;
    voidCall(refreshTasks());
  }, [projectName, enabled, refreshTasks]);

  useEffect(() => {
    if (!enabled) return;

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
