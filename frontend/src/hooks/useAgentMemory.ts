import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { API } from "@/api";
import type { AgentMemoryOverview, AgentMemoryScope } from "@/types/agent-memory";
import { errMsg } from "@/utils/async";

export interface AgentMemoryState {
  overview: AgentMemoryOverview | null;
  loading: boolean;
  error: string | null;
  scope: AgentMemoryScope;
  reload: () => Promise<void>;
}

interface LoadedMemory {
  target: AgentMemoryScope;
  data: AgentMemoryOverview | null;
  error: string | null;
}

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
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const next = await API.getAgentMemory(target, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setLoaded({ target, data: next, error: null });
    } catch (err) {
      if (controller.signal.aborted) return;
      setLoaded({ target, data: null, error: errMsg(err) });
    } finally {
      // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- 复位已在 finally 中；条件只让被作废的一轮把复位交给接管方
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [target]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 挂载时拉一次，reload 同步点亮加载态，属于受控的初始化加载
    void reload();
    return () => abortRef.current?.abort();
  }, [reload]);

  const current = loaded !== null && loaded.target === target ? loaded : null;
  return {
    overview: current?.data ?? null,
    loading: current === null || loading,
    error: current?.error ?? null,
    scope: target,
    reload,
  };
}
