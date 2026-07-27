import { useEffect, useRef } from "react";
import {
  defaultTaskStats,
  selectNeedsFastPolling,
  useTasksStore,
} from "@/stores/tasks-store";
import { voidCall } from "@/utils/async";

/** 有任务在途（或上轮拉取失败）时的轮询间隔——此间隔决定 queued→running 等中间态的可见延迟。 */
const FAST_POLL_INTERVAL_MS = 3000;
/** 无任务在途时的空闲退避间隔——没有状态会变，轮询只为捕捉外部（别的客户端/skill）新入队的任务。 */
const IDLE_POLL_INTERVAL_MS = 30000;

/**
 * 任务队列状态的刷新 Hook。
 *
 * 终态的主通道是项目事件 SSE：任务进入终态时后端经 `/projects/{name}/events/stream`
 * 推来 `task_*` 变更，`useProjectEventsSSE` 收到即调 `refreshTasks`，无轮询间隔延迟。
 * 本 Hook 负责另外两件事：登记刷新作用域（拉哪个项目的任务），以及兜底轮询。
 *
 * 轮询间隔按**是否有任务在途**切换，而不是按 SSE 是否在线：
 * - 后端只对终态发事件，queued→running 一类中间态没有事件，仍靠轮询才能看见；
 *   有任务在途时若退到低频，任务「开始执行」与进度会比改动前更晚出现。
 * - SSE 断线不止 `onError` 一种形态——连接被中间设备保住但不再送事件时不触发任何回调。
 *   以「有任务在途」为判据时，这种静默失速期间轮询仍在高频档，兜底照常生效。
 * 没有任务在途时状态本就不会变，退到低频只为捕捉别处新入队的任务。
 *
 * `projectName` 传 `null` 只是「不按项目过滤」（拉全局任务），不是「停用」——
 * 停用必须用 `enabled: false` 显式声明，否则调用方以为传 null 能关掉轮询，
 * 实际仍会持续拉取全局任务列表。
 */
export function useTaskRefresh(projectName?: string | null, enabled = true): void {
  const { setTasks, setStats, setConnected, setRefreshScope, refreshTasks } = useTasksStore();
  const needsFastPolling = useTasksStore(selectNeedsFastPolling);
  // 初值 true：挂载首帧的对账归作用域 effect，不在调频 effect 里重复一次。
  const prevNeedsFastPollingRef = useRef(true);

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

  // 作用域切换后当场取一次状态，不等下一个轮询间隔。与下方调频 effect 分开，各自只有
  // 一个重跑理由：合在一起就得在 effect 内反推「这次是切项目还是只是换档」。
  useEffect(() => {
    if (!enabled) return;
    voidCall(refreshTasks());
  }, [projectName, enabled, refreshTasks]);

  useEffect(() => {
    if (!enabled) return;

    // 空闲→高频是「有新工作到来」的信号（典型是乐观占用标记落下的那一刻），当场取一次
    // 状态，不等下一个间隔。反向（高频→空闲）无事可取，不必对账。ref 初值取 true，
    // 使挂载首帧不在此重复对账——挂载那次由上方作用域 effect 负责。
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
