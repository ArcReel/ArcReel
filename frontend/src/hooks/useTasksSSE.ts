import { useEffect, useRef } from "react";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";

/**
 * 连接任务队列 SSE 流的 Hook。
 * 挂载时建立连接，处理 snapshot/task/heartbeat 事件，
 * 断开后 3 秒自动重连，卸载时清理。
 */
export function useTasksSSE(projectName?: string | null): void {
  const sourceRef = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { setTasks, upsertTask, setStats, setConnected } = useTasksStore();

  useEffect(() => {
    let disposed = false;

    function connect() {
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }

      const es = API.openTaskStream({
        projectName: projectName ?? undefined,
        onSnapshot(payload) {
          if (disposed) return;
          setTasks(payload.tasks);
          setStats(payload.stats);
          setConnected(true);
        },
        onTask(payload) {
          if (disposed) return;
          upsertTask(payload.task);
          setStats(payload.stats);
        },
        onHeartbeat() {
          if (disposed) return;
          setConnected(true);
        },
        onError() {
          if (disposed) return;
          setConnected(false);
          if (sourceRef.current) {
            sourceRef.current.close();
            sourceRef.current = null;
          }
          reconnectTimer.current = setTimeout(() => {
            if (!disposed) connect();
          }, 3000);
        },
      });

      sourceRef.current = es;
    }

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
      setConnected(false);
    };
  }, [projectName, setTasks, upsertTask, setStats, setConnected]);
}
