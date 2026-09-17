import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import {
  GHOST_BTN_CLS,
  ambientGlowStyle,
  posterGridStyle,
} from "@/components/ui/darkroom-tokens";
import type { MarketSourceInfo } from "@/types";
import { KICKER_ACCENT_CLS } from "./market-source-status";
import { MarketSourcesDialog } from "./MarketSourcesDialog";

/** 按 id 用刷新结果替换列表里的行，保持原顺序。 */
function mergeSources(
  current: MarketSourceInfo[],
  updated: MarketSourceInfo[],
): MarketSourceInfo[] {
  const byId = new Map(updated.map((source) => [source.id, source]));
  return current.map((source) => byId.get(source.id) ?? source);
}

/**
 * 市场小节：hero 头部与市场源管理弹窗。打开时先渲染缓存的源列表，再在后台刷新距上次
 * 成功刷新超过 1 小时的启用源。
 */
export function MarketSection() {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const [sources, setSources] = useState<MarketSourceInfo[]>([]);
  const [refreshingIds, setRefreshingIds] = useState<ReadonlySet<number>>(new Set());
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const { sources: cached } = await API.listMarketSources({ signal: controller.signal });
        if (!mounted.current) return;
        setSources(cached);
        const { sources: refreshed } = await API.refreshMarketSources({ staleOnly: true });
        if (mounted.current && refreshed.length > 0) {
          setSources((current) => mergeSources(current, refreshed));
        }
      } catch (err) {
        if (mounted.current && !controller.signal.aborted) {
          pushToast(t("market_action_failed", { message: errMsg(err) }), "error");
        }
      }
    })();
    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, [pushToast, t]);

  const refreshAll = useCallback(async () => {
    const targets = sources.filter((source) => source.is_enabled).map((source) => source.id);
    setRefreshingAll(true);
    setRefreshingIds((current) => new Set([...current, ...targets]));
    try {
      const { sources: refreshed } = await API.refreshMarketSources();
      if (!mounted.current) return;
      setSources((current) => mergeSources(current, refreshed));
      pushToast(t("market_refresh_all_done", { count: refreshed.length }), "success");
    } catch (err) {
      if (mounted.current) pushToast(t("market_action_failed", { message: errMsg(err) }), "error");
    } finally {
      if (mounted.current) {
        setRefreshingAll(false);
        setRefreshingIds((current) => new Set([...current].filter((id) => !targets.includes(id))));
      }
    }
  }, [pushToast, sources, t]);

  const refreshOne = useCallback(
    async (id: number) => {
      setRefreshingIds((current) => new Set(current).add(id));
      try {
        const refreshed = await API.refreshMarketSource(id);
        if (mounted.current) setSources((current) => mergeSources(current, [refreshed]));
      } catch (err) {
        if (mounted.current) pushToast(t("market_action_failed", { message: errMsg(err) }), "error");
      } finally {
        if (mounted.current) {
          setRefreshingIds((current) => {
            const next = new Set(current);
            next.delete(id);
            return next;
          });
        }
      }
    },
    [pushToast, t],
  );

  const enabled = sources.filter((source) => source.is_enabled);
  const entryCount = enabled.reduce((sum, source) => sum + source.entry_count, 0);

  return (
    <div className="relative">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-56"
        style={ambientGlowStyle({ at: "30% 0%", intensity: 0.14 })}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-56 opacity-[0.05]"
        style={posterGridStyle({ size: 36, maskShape: "80% 100% at 50% 0%" })}
      />
      <div className="relative mx-auto max-w-6xl px-8 pb-16 pt-10">
        <header className="mb-6 flex flex-wrap items-end gap-4">
          <div className="min-w-0 flex-1">
            <div className={KICKER_ACCENT_CLS}>
              Market · {entryCount} endpoints from {enabled.length} sources
            </div>
            <h2 className="mt-1 font-editorial text-[32px] leading-none text-text">
              {t("market_section_title")}
            </h2>
          </div>
          <button
            type="button"
            className={GHOST_BTN_CLS}
            disabled={refreshingAll || enabled.length === 0}
            onClick={() => void refreshAll()}
          >
            {refreshingAll ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            )}
            {t("market_refresh_all")}
          </button>
          <button type="button" className={GHOST_BTN_CLS} onClick={() => setManageOpen(true)}>
            <Settings2 className="h-3.5 w-3.5" aria-hidden />
            {t("market_manage_sources")}
          </button>
        </header>
      </div>

      <MarketSourcesDialog
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        sources={sources}
        onSourcesChange={setSources}
        refreshingIds={refreshingIds}
        refreshingAll={refreshingAll}
        onRefresh={(id) => void refreshOne(id)}
        onRefreshAll={() => void refreshAll()}
      />
    </div>
  );
}
