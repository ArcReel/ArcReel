import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";

const EMPTY_IDS: ReadonlySet<string> = new Set();

interface UseScriptEntryCurrencyOptions {
  projectName: string;
  episode: number;
  /** 为 false 时不取数（广告/短片没有脚本规划；剧本未生成时也没有可比对的条目）。 */
  enabled: boolean;
  /** 剧本对象本身：换一份剧本（保存 / 转换 / Agent 改写后重取）就重新比对。 */
  scriptRevision: unknown;
}

/**
 * 正式剧本条目相对脚本规划的时效：返回内容已落后的条目 id 集合。
 *
 * 取数走内容确认状态（`GET script-review`）里的 `script_entry_currency`：那是无准入的「内容是否
 * 变了」，草稿在场、时长档位或发声准入不满足时照样给出；不走机械转换预演——预演过与生成同一组
 * 准入断言，任一不满足即整体 422，用它驱动这里会让整条时间线的提示随草稿的出现而消失又恢复。
 *
 * 请求失败时保留上一次的结果并记警告，不清空：清空会把「读不出」呈现成「全部一致」。项目不适用
 * （服务端给 null）才视为没有失效条目。
 */
export function useScriptEntryCurrency({
  projectName,
  episode,
  enabled,
  scriptRevision,
}: UseScriptEntryCurrencyOptions): { staleIds: ReadonlySet<string>; reload: () => Promise<void> } {
  const [staleIds, setStaleIds] = useState<ReadonlySet<string>>(EMPTY_IDS);
  const inflight = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    try {
      const state = await API.getScriptReview(projectName, episode, { signal: controller.signal });
      if (controller.signal.aborted || inflight.current !== controller) return;
      setStaleIds(new Set(state.script_entry_currency?.stale ?? []));
    } catch (err) {
      if (controller.signal.aborted || inflight.current !== controller) return;
      console.warn(`[script-entry-currency] 读取第 ${episode} 集条目时效失败，沿用上一次结果`, err);
    }
  }, [projectName, episode]);

  useEffect(() => {
    if (!enabled) {
      inflight.current?.abort();
      return;
    }
    // 取数在回调里落 state，effect 体本身只发请求。
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 取数在异步回调里落 state，effect 体只发请求
    void load();
    return () => inflight.current?.abort();
  }, [enabled, load, scriptRevision]);

  return { staleIds: enabled ? staleIds : EMPTY_IDS, reload: load };
}
