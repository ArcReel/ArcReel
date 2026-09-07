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
 * 取数走机械转换的只读预演端点；请求失败或项目不适用时视为没有失效条目，时间线只是少一条
 * 提示，不阻断编辑。
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
      const preview = await API.previewScriptPlanConversion(projectName, episode, { signal: controller.signal });
      if (controller.signal.aborted || inflight.current !== controller) return;
      setStaleIds(new Set(preview.stale));
    } catch {
      if (controller.signal.aborted || inflight.current !== controller) return;
      setStaleIds(EMPTY_IDS);
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
