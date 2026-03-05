import { useEffect, useRef } from "react";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";

const POLL_INTERVAL_MS = 3000;

/**
 * 轮询任务队列状态的 Hook。
 * 挂载时立即拉取一次，之后每 3 秒轮询，卸载时清理。
 *
 * 替代原先的 EventSource SSE 长连接，释放浏览器连接槽位
 * （Chrome HTTP/1.1 同域名 6 连接限制）。
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
