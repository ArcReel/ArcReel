import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { API } from "@/api";
import type { AgentMemoryOverview, AgentMemoryScope } from "@/types/agent-memory";
import { errMsg } from "@/utils/async";

export interface AgentMemoryState {
  /** 未加载完成或加载失败时为 null。 */
  overview: AgentMemoryOverview | null;
  loading: boolean;
  /** 列表拉取失败的可读说明；成功后清空。 */
  error: string | null;
  /** 记忆目录标识，调用点把它原样传给写类 API，两级因此共用同一批调用。 */
  scope: AgentMemoryScope;
  /** 重新拉取列表；在途请求作废，卸载时一并取消。 */
  reload: () => Promise<void>;
}

/**
 * 一个记忆目录的列表加载态：挂载时拉一次，写操作后由调用点 `reload()` 重拉。
 *
 * 不轮询、不监听窗口聚焦：记忆由创作者与 Agent 各自写入，服务端无冲突检测，跟随外部更新
 * 会在创作者正在编辑时替换掉编辑器内容。
 *
 * 入参按 `level` 与项目名解构后再重建 scope：调用点惯常在 JSX 里传字面量对象，直接把它列进
 * 依赖会让每次渲染都重新拉取。
 */
export function useAgentMemory(scope: AgentMemoryScope): AgentMemoryState {
  const level = scope.level;
  const projectName = scope.level === "project" ? scope.projectName : null;
  const target = useMemo<AgentMemoryScope>(
    () => (level === "user" ? { level: "user" } : { level: "project", projectName: projectName ?? "" }),
    [level, projectName],
  );

  const [overview, setOverview] = useState<AgentMemoryOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    // 接管方轮换 controller（见 .claude/rules/frontend-async-race.md）。
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const next = await API.getAgentMemory(target, { signal: controller.signal });
      // 网络 await 之后的写 state 断点：abort 可能发生在响应已 resolve 之后。
      if (controller.signal.aborted) return;
      setOverview(next);
      setError(null);
    } catch (err) {
      if (controller.signal.aborted) return;
      setOverview(null);
      setError(errMsg(err));
    } finally {
      // 被接管方让位：作废后不复位共享状态，否则会灭掉接管方刚点亮的加载态。
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [target]);

  useEffect(() => {
    // 挂载时拉一次；reload 同步点亮加载态，属于受控的初始化加载。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
    return () => abortRef.current?.abort();
  }, [reload]);

  return { overview, loading, error, scope: target, reload };
}
