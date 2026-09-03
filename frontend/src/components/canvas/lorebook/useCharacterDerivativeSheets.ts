import { useCallback, useEffect, useState } from "react";
import { API } from "@/api";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { CharacterDerivativeStatus } from "@/types";

/**
 * 按需拉取一个角色名下全部衍生的资产图与过期标记。
 *
 * 过期是产物清单与规范状态的一次比对（读文件、算指纹），不随项目数据下发，故只在浮层
 * 展开时取；衍生生成任务的占用集清空即代表有任务刚结束，据此再取一次拿到新图与新状态。
 * 上一轮在途请求由 AbortSignal 作废，取不到时保留上一次的值——过期标记宁可暂时旧，也好
 * 过闪回「没有图」。
 */
export function useCharacterDerivativeSheets(
  projectName: string,
  characterName: string,
  enabled: boolean,
): { statuses: Record<string, CharacterDerivativeStatus>; refresh: () => void } {
  const [statuses, setStatuses] = useState<Record<string, CharacterDerivativeStatus>>({});
  const [reloadToken, setReloadToken] = useState(0);
  const activeCount = useActiveResourceIds("character_derivative", projectName).size;
  const refresh = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void API.getCharacterDerivativeSheets(projectName, characterName, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setStatuses(res.derivatives ?? {});
      })
      .catch(() => {
        /* 读取失败（含被作废）保留上一次的状态，浮层的登记与写入不受影响 */
      });
    return () => controller.abort();
  }, [enabled, projectName, characterName, activeCount, reloadToken]);

  return { statuses, refresh };
}
