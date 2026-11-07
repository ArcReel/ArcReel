import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { CustomProviderInfo, ProviderInfo } from "@/types";
import { errMsg, voidCall } from "@/utils/async";

/**
 * Silent-refetch outcome. Callers distinguish failed refetches (catalogs remain old and users need notice)
 * from requests invalidated by successors (normal concurrency, with successors writing newer catalogs; never report errors).
 */
export type CatalogRefreshResult =
  | { status: "ok"; customProviders: CustomProviderInfo[] }
  | { status: "aborted" }
  | { status: "failed" };

export interface ProviderCatalog {
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  loading: boolean;
  error: string | null;
  /** Fully refetch with loading/error panels, for error-panel retry entries. */
  reload: () => void;
  /**
   * Silently refetch entire catalogs, returning newest custom-provider lists on success for callers to adjust selections.
   * Failures and invalidation by later requests resolve as failed / aborted respectively.
   */
  refresh: () => Promise<CatalogRefreshResult>;
}

/**
 * Read/refresh provider catalogs. Always fetch built-in/custom tables together, sharing one implementation and cancellation domain for four writes:
 * initial fetch, language refetch, post-save refetch and failure retry.
 *
 * One controller defines cancellation; every takeover invalidates prior in-flight reads. Post-save and language-switch refetches
 * can overwrite each other; slower responses write stale catalogs. Invalidated requests never write shared state; successors handle cleanup.
 *
 * Refetch on ``language`` changes: backends render provider/model names by Accept-Language, so otherwise catalogs retain previous
 * languages. These refetches are silent: no loading state, lists/detail panels stay mounted, and unsaved detail-form
 * inputs survive. Failures retain prior catalogs too; old translations are preferable to lost inputs. Before any initial success,
 * however, no catalogs exist to retain, so use regular loading/error panels rather than empty lists without retry entries.
 */
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
      // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- reset is already in finally; invalidated requests delegate reset to successors to avoid clearing successor loading state
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => load(true), [load]);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  const loadedKeyRef = useRef<number | null>(null);
  useEffect(() => {
    // Reruns under identical reloadKey can only follow language changes; refresh silently.
    const silent = hasLoadedRef.current && loadedKeyRef.current === reloadKey;
    loadedKeyRef.current = reloadKey;
    voidCall(load(silent));
    return () => abortRef.current?.abort();
  }, [reloadKey, language, load]);

  return { providers, customProviders, loading, error, reload, refresh };
}
