import { useEffect, useRef } from "react";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";

const POLL_INTERVAL_MS = 3000;

/**
 * Hook that polls the task queue status.
 * Fetches immediately on mount, then polls every 3 seconds, and cleans up on unmount.
 *
 * Replaces the original EventSource SSE long connection to free up browser connection slots
 * (Chrome HTTP/1.1 same-origin 6 connection limit).
 */
export function useTasksSSE(projectName?: string | null): void {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const { setTasks, setStats, setConnected } = useTasksStore();

  useEffect(() => {
    let disposed = false;

    async function poll() {
      try {
        const [tasksRes, statsRes] = await Promise.all([
          API.listTasks({
            projectName: projectName ?? undefined,
            pageSize: 200,
          }),
          API.getTaskStats(projectName ?? null),
        ]);
        if (disposed) return;
        setTasks(tasksRes.items);
        // REST returns { stats: {...} }
        const stats = (statsRes as any).stats ?? statsRes;
        setStats(stats);
        setConnected(true);
      } catch {
        if (disposed) return;
        setConnected(false);
      }
    }

    // Initial fetch
    poll();

    // Periodic polling
    timerRef.current = setInterval(() => {
      if (!disposed) poll();
    }, POLL_INTERVAL_MS);

    return () => {
      disposed = true;
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      setConnected(false);
    };
  }, [projectName, setTasks, setStats, setConnected]);
}
