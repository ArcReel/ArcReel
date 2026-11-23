import { useCallback, useEffect, useState } from "react";
import { API } from "@/api";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { CharacterDerivativeStatus } from "@/types";

export function useCharacterDerivativeSheets(
  projectName: string,
  characterName: string,
  revision: string,
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
        /* 读取失败（含被作废）保留上一次的状态，衍生的登记与写入不受影响 */
      });
    return () => controller.abort();
  }, [enabled, projectName, characterName, revision, activeCount, reloadToken]);

  return { statuses, refresh };
}
