import { useEffect, useRef } from "react";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { voidCall } from "@/utils/async";

const POLL_INTERVAL_MS = 3000;

/**
 * 轮询任务队列状态的 Hook。
 * 挂载时立即拉取一次，之后每 3 秒轮询，卸载时清理。
 *
 * 替代原先的 EventSource SSE 长连接，释放浏览器连接槽位
 * （Chrome HTTP/1.1 同域名 6 连接限制）。
 *
 * `projectName` 传 `null` 只是「不按项目过滤」（拉全局任务），不是「停用」——
 * 停用必须用 `enabled: false` 显式声明，否则调用方以为传 null 能关掉轮询，
 * 实际仍会持续拉取全局任务列表。
 */
export function useTasksSSE(projectName?: string | null, enabled = true): void {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const { setTasks, setStats, setConnected } = useTasksStore();

  useEffect(() => {
    if (!enabled) return;
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
        setStats(statsRes.stats);
        setConnected(true);
      } catch {
        if (disposed) return;
        setConnected(false);
      }
    }

    // Initial fetch
    voidCall(poll());

    // Periodic polling
    timerRef.current = setInterval(() => {
      if (!disposed) voidCall(poll());
    }, POLL_INTERVAL_MS);

    return () => {
      disposed = true;
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      setConnected(false);
    };
  }, [projectName, enabled, setTasks, setStats, setConnected]);
}
