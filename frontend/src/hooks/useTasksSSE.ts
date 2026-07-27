import { useEffect } from "react";
import { useAppStore } from "@/stores/app-store";
import { defaultTaskStats, useTasksStore } from "@/stores/tasks-store";
import { voidCall } from "@/utils/async";

/** 项目事件 SSE 断线时的兜底轮询间隔——此时轮询是唯一的状态来源。 */
const FALLBACK_POLL_INTERVAL_MS = 3000;
/** SSE 在线时的空闲退避间隔——终态由事件即时推来，轮询只做漏事件的兜底对账。 */
const IDLE_POLL_INTERVAL_MS = 30000;

/**
 * 任务队列状态的刷新 Hook。
 *
 * 主通道是项目事件 SSE：任务进入终态时后端经 `/projects/{name}/events/stream` 推来
 * `task_*` 变更，`useProjectEventsSSE` 收到即调 `refreshTasks`，无轮询间隔延迟。
 * 本 Hook 只负责两件事：登记刷新作用域（拉哪个项目的任务），以及按 SSE 在线状态
 * 自适应地兜底轮询——SSE 断线期间回到 3 秒高频轮询，保证状态仍可恢复；在线期间退到
 * 30 秒低频对账，捕捉事件漏发或断连瞬间错过的终态。
 *
 * `projectName` 传 `null` 只是「不按项目过滤」（拉全局任务），不是「停用」——
 * 停用必须用 `enabled: false` 显式声明，否则调用方以为传 null 能关掉轮询，
 * 实际仍会持续拉取全局任务列表。
 */
export function useTasksSSE(projectName?: string | null, enabled = true): void {
  const { setTasks, setStats, setConnected, setRefreshScope, refreshTasks } = useTasksStore();
  const projectEventsConnected = useAppStore((s) => s.projectEventsConnected);

  useEffect(() => {
    if (!enabled) {
      // 停用（如切到只读演示项目）不只是不再拉取——store 里残留的上一项目 tasks/stats
      // 若不清空，无条件挂载的 GlobalHeader 任务角标/TaskHud 会继续展示旧项目数据。
      // 作用域一并清空，让在途刷新的迟到响应写不回来。
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

    // 立即对账一次：挂载、切项目、以及 SSE 刚断线转入高频兜底时都需要当场取一次状态，
    // 不等下一个间隔。
    voidCall(refreshTasks());

    const intervalMs = projectEventsConnected ? IDLE_POLL_INTERVAL_MS : FALLBACK_POLL_INTERVAL_MS;
    const timer = setInterval(() => {
      voidCall(refreshTasks());
    }, intervalMs);

    return () => {
      clearInterval(timer);
    };
  }, [projectName, enabled, projectEventsConnected, refreshTasks]);
}
