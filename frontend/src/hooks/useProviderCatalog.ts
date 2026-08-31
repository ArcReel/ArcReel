import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { CustomProviderInfo, ProviderInfo } from "@/types";
import { errMsg, voidCall } from "@/utils/async";

export interface ProviderCatalog {
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  loading: boolean;
  error: string | null;
  /** 全量重取并回到 loading / 错误面板口径——错误面板的重试入口。 */
  reload: () => void;
  /**
   * 静默重取整份目录；resolve 出最新的自定义供应商列表，供调用方按它改选中项。
   * 在途的那次被作废时 resolve 出空列表——此时接管方会写下更新的目录。
   */
  refresh: () => Promise<CustomProviderInfo[]>;
}

/**
 * 供应商目录的读取与刷新。预置与自定义两张表恒一同取，四个写入点（首次拉取、语言重取、保存后
 * 重取、失败重试）因此只有一处实现，也只有一个取消域。
 *
 * 取消域只有一个 controller，任何一次读取接管时先作废在途的那次：保存后的重取与语言切换触发的
 * 重取会互相覆盖，慢的那次回来时写下的是过期目录。作废方不回写共享状态，收尾由接管方负责。
 *
 * ``language`` 变化即重取：供应商与模型名由后端按 Accept-Language 成文，不重取则目录停留在
 * 切换前的语言。这一类重取是静默的——不置 loading，列表与详情面板保持挂载，详情表单里未保存的
 * 输入不被卸载丢弃；失败也保留上一份目录，译名停留在旧语言远好过丢输入。但首次拉取尚未成功过
 * 时没有可留的目录，那一轮仍走常规的 loading / 错误面板，否则会留下一个空列表且没有重试入口。
 */
export function useProviderCatalog(language: string): ProviderCatalog {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const hasLoadedRef = useRef(false);

  const load = useCallback(async (silent: boolean): Promise<CustomProviderInfo[]> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const [presetRes, customRes] = await Promise.all([
        API.getProviders({ signal: controller.signal }),
        API.listCustomProviders({ signal: controller.signal }),
      ]);
      if (controller.signal.aborted) return [];
      setProviders(presetRes.providers);
      setCustomProviders(customRes.providers);
      hasLoadedRef.current = true;
      setError(null);
      void useConfigStatusStore.getState().refresh();
      return customRes.providers;
    } catch (err) {
      if (!controller.signal.aborted && !silent) setError(errMsg(err));
      return [];
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => load(true), [load]);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  const loadedKeyRef = useRef<number | null>(null);
  useEffect(() => {
    // 同一 reloadKey 下的重跑只可能由语言变化触发，此时静默刷新。
    const silent = hasLoadedRef.current && loadedKeyRef.current === reloadKey;
    loadedKeyRef.current = reloadKey;
    voidCall(load(silent));
    return () => abortRef.current?.abort();
  }, [reloadKey, language, load]);

  return { providers, customProviders, loading, error, reload, refresh };
}
