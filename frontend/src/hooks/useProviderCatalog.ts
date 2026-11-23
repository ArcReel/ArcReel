import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { CustomProviderInfo, ProviderInfo } from "@/types";
import { errMsg, voidCall } from "@/utils/async";

export type CatalogRefreshResult =
  | { status: "ok"; customProviders: CustomProviderInfo[] }
  | { status: "aborted" }
  | { status: "failed" };

export interface ProviderCatalog {
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  refresh: () => Promise<CatalogRefreshResult>;
}

export function useProviderCatalog(language: string): ProviderCatalog {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const hasLoadedRef = useRef(false);

  const load = useCallback(async (silent: boolean): Promise<CatalogRefreshResult> => {
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
      if (controller.signal.aborted) return { status: "aborted" };
      setProviders(presetRes.providers);
      setCustomProviders(customRes.providers);
      hasLoadedRef.current = true;
      setError(null);
      void useConfigStatusStore.getState().refresh();
      return { status: "ok", customProviders: customRes.providers };
    } catch (err) {
      if (controller.signal.aborted) return { status: "aborted" };
      if (!silent) setError(errMsg(err));
      return { status: "failed" };
    } finally {
      // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- 复位已在 finally 中；被作废的一轮把复位交给接管方，否则会灭掉接管方的加载态
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => load(true), [load]);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  const loadedKeyRef = useRef<number | null>(null);
  useEffect(() => {
    const silent = hasLoadedRef.current && loadedKeyRef.current === reloadKey;
    loadedKeyRef.current = reloadKey;
    voidCall(load(silent));
    return () => abortRef.current?.abort();
  }, [reloadKey, language, load]);

  return { providers, customProviders, loading, error, reload, refresh };
}
