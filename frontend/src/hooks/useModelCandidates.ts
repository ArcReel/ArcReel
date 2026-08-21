import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import type { ModelCandidatesResponse } from "@/types/system";

/**
 * 任务类型桶候选（docs/adr/0054）的加载态：全局设置与项目设置两处都消费同一份全局数据，
 * 加载与失败叙事收在这里，调用点只拿结果。
 *
 * 三态互不重叠，因为「细分区静默降级」与「显式报错」的分岔全靠它们区分：
 *   candidates=null, error=false  → 仍在加载（或尚未触发），沿用静默降级
 *   candidates!=null              → 成功；桶为空也走这支，不报错
 *   error=true                    → 拉取失败，调用点渲染错误文案与重试入口
 */
export interface ModelCandidatesState {
  candidates: ModelCandidatesResponse | null;
  error: boolean;
  /** 重试在途；重试按钮据此灰化，否则慢响应下点击没有任何反馈。 */
  retrying: boolean;
  /** 拉取一次；组件卸载与下一次调用都会作废在途请求。 */
  reload: () => Promise<void>;
}

/**
 * 不在挂载时自动拉取：两处调用点的触发时机不同——项目设置只在挂载时取一次，全局设置把它
 * 并进保存后的整页重取里。由调用点决定何时 `reload()`，hook 只保证不会有过期响应回写。
 */
export function useModelCandidates(): ModelCandidatesState {
  const [candidates, setCandidates] = useState<ModelCandidatesResponse | null>(null);
  const [error, setError] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    // 接管方轮换 controller（见 .claude/rules/frontend-async-race.md）：重试可能在上一轮
    // 请求尚未回来时触发，旧响应回来时已经过期。
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRetrying(true);
    try {
      const next = await API.getModelCandidates({ signal: controller.signal });
      // 网络 await 之后的写 state 断点：abort 可能发生在响应已 resolve 之后。
      if (controller.signal.aborted) return;
      setCandidates(next);
      setError(false);
    } catch {
      if (controller.signal.aborted) return;
      setCandidates(null);
      setError(true);
    } finally {
      // 被接管方让位：作废后不复位共享状态，否则会灭掉接管方刚点亮的在途标记。
      if (!controller.signal.aborted) setRetrying(false);
    }
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { candidates, error, retrying, reload };
}
