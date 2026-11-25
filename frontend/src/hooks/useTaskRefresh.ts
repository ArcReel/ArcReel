import { useEffect, useRef } from "react";
import {
  selectNeedsFastPolling,
  useTasksStore,
} from "@/stores/tasks-store";
import { voidCall } from "@/utils/async";

const FAST_POLL_INTERVAL_MS = 3000;
const IDLE_POLL_INTERVAL_MS = 30000;

function isDocumentHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

export function useTaskRefresh(projectName?: string | null, enabled = true): void {
  const setRefreshScope = useTasksStore((s) => s.setRefreshScope);
  const refreshTasks = useTasksStore((s) => s.refreshTasks);
  const needsFastPolling = useTasksStore(selectNeedsFastPolling);
  const prevNeedsFastPollingRef = useRef(true);
  const pendingVisiblePollRef = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setRefreshScope(null);
      pendingVisiblePollRef.current = false;
      return;
    }

    setRefreshScope({ projectName: projectName ?? null });
    return () => {
      setRefreshScope(null);
      pendingVisiblePollRef.current = false;
    };
  }, [projectName, enabled, setRefreshScope]);

  useEffect(() => {
    if (!enabled) return;
    if (isDocumentHidden()) {
      pendingVisiblePollRef.current = true;
      return;
    }
    pendingVisiblePollRef.current = false;
    voidCall(refreshTasks());
  }, [projectName, enabled, refreshTasks]);

  useEffect(() => {
    if (!enabled) return;

    const wasFast = prevNeedsFastPollingRef.current;
    prevNeedsFastPollingRef.current = needsFastPolling;
    if (needsFastPolling && !wasFast) {
      if (isDocumentHidden()) {
        pendingVisiblePollRef.current = true;
      } else {
        voidCall(refreshTasks());
      }
    }

    const intervalMs = needsFastPolling ? FAST_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
    const timer = setInterval(() => {
      if (isDocumentHidden()) {
        pendingVisiblePollRef.current = true;
        return;
      }
      voidCall(refreshTasks());
    }, intervalMs);

    return () => {
      clearInterval(timer);
    };
  }, [enabled, needsFastPolling, refreshTasks]);

  useEffect(() => {
    if (!enabled || typeof document === "undefined") return;
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible" || !pendingVisiblePollRef.current) return;
      pendingVisiblePollRef.current = false;
      voidCall(refreshTasks());
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [enabled, refreshTasks]);
}
