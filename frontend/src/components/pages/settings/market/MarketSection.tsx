import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { endpointSettingsPath, marketSettingsPath, type MarketTab } from "@/app-routes";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type {
  MarketEntry,
  MarketEntryAggregate,
  MarketSourceInfo,
  MarketSubmission,
  OfficialServiceState,
} from "@/types";
import { MarketBrowseTab } from "./MarketBrowseTab";
import { aggregateKey } from "./market-keys";
import { MarketInstallDialog } from "./MarketInstallDialog";
import { MarketSettingsTab } from "./MarketSettingsTab";
import { MarketSharedTab } from "./MarketSharedTab";
import { OfficialServiceNotice } from "./OfficialServiceNotice";

const TABS: readonly MarketTab[] = ["browse", "shared", "settings"];

/**
 * 按 id 合并刷新结果，只改写刷新产出的字段、保持原顺序；显示名与启停以本地为准，
 * 迟到的刷新响应不会覆盖期间完成的修改。
 */
function mergeRefreshed(current: MarketSourceInfo[], updated: MarketSourceInfo[]): MarketSourceInfo[] {
  const byId = new Map(updated.map((source) => [source.id, source]));
  return current.map((source) => {
    const refreshed = byId.get(source.id);
    if (!refreshed) return source;
    return {
      ...source,
      status: refreshed.status,
      last_error: refreshed.last_error,
      fetched_at: refreshed.fetched_at,
      entry_count: refreshed.entry_count,
      index: refreshed.index,
      updated_at: refreshed.updated_at,
    };
  });
}

/** 影响条目列表的源字段：顺序、启停、显示名与快照时间。任一变化即重新拉取条目。 */
function entriesKey(sources: MarketSourceInfo[]): string {
  return sources
    .map((source) => [source.id, source.is_enabled, source.display_name, source.fetched_at, source.updated_at].join(":"))
    .join("|");
}

const NO_AGGREGATES: ReadonlyMap<string, MarketEntryAggregate> = new Map();

/**
 * 市场分区：顶部 Tabs 分「浏览」「我的分享」「设置」，当前 Tab 记在地址的 `tab` 参数里。
 * 打开时先渲染缓存的源列表与条目，再在后台刷新距上次成功刷新超过 1 小时的启用源；源有变化时重新拉取条目。
 * 官方服务开启时另拉官方市场源条目的安装量与评分和我的分享，首次进入显示一次说明；
 * 关闭或读不到状态时「浏览」不展示任何官方服务元素，开关本身常驻在「设置」。
 */
export function MarketSection() {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const [, navigate] = useLocation();
  const search = useSearch();
  const tabParam = new URLSearchParams(search).get("tab");
  const tab = TABS.find((item) => item === tabParam) ?? "browse";
  const [sources, setSources] = useState<MarketSourceInfo[]>([]);
  const [sourcesLoaded, setSourcesLoaded] = useState(false);
  const [entries, setEntries] = useState<MarketEntry[] | null>(null);
  const [refreshingIds, setRefreshingIds] = useState<ReadonlySet<number>>(new Set());
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [installationRevision, setInstallationRevision] = useState(0);
  const [selected, setSelected] = useState<MarketEntry | null>(null);
  const [official, setOfficial] = useState<OfficialServiceState | null>(null);
  const [officialBusy, setOfficialBusy] = useState(false);
  const [aggregates, setAggregates] = useState<ReadonlyMap<string, MarketEntryAggregate>>(new Map());
  const [aggregatesRevision, setAggregatesRevision] = useState(0);
  const [submissions, setSubmissions] = useState<MarketSubmission[] | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const { sources: cached } = await API.listMarketSources({ signal: controller.signal });
        if (!mounted.current) return;
        setSources(cached);
        setSourcesLoaded(true);
        const { sources: refreshed } = await API.refreshMarketSources({ staleOnly: true, signal: controller.signal });
        if (mounted.current && refreshed.length > 0) {
          setSources((current) => mergeRefreshed(current, refreshed));
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

  const sourcesKey = entriesKey(sources);
  useEffect(() => {
    if (!sourcesLoaded) return;
    const controller = new AbortController();
    API.listMarketEntries({ signal: controller.signal })
      .then((response) => {
        if (!controller.signal.aborted) setEntries(response.entries);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          pushToast(t("market_action_failed", { message: errMsg(err) }), "error");
        }
      });
    return () => controller.abort();
  }, [sourcesLoaded, sourcesKey, installationRevision, pushToast, t]);

  useEffect(() => {
    const controller = new AbortController();
    API.getOfficialService({ signal: controller.signal })
      .then((state) => {
        if (!controller.signal.aborted) setOfficial(state);
      })
      .catch(() => {
        // 读不到状态时按关闭处理：市场本身不依赖官方服务。
      });
    return () => controller.abort();
  }, []);

  const officialEnabled = official?.enabled === true;
  useEffect(() => {
    if (!officialEnabled || !sourcesLoaded) return;
    const controller = new AbortController();
    API.listMarketEntryAggregates({ signal: controller.signal })
      .then(({ items }) => {
        if (controller.signal.aborted) return;
        setAggregates(new Map(items.map((item) => [aggregateKey(item.source_id, item.slug), item])));
      })
      .catch(() => {
        // 聚合取不回时不显示数字，不打扰浏览。
        if (!controller.signal.aborted) setAggregates(new Map());
      });
    return () => controller.abort();
  }, [officialEnabled, sourcesLoaded, sourcesKey, installationRevision, aggregatesRevision]);

  useEffect(() => {
    if (!officialEnabled) return;
    const controller = new AbortController();
    API.listMarketSubmissions({ signal: controller.signal })
      .then(({ submissions: listed }) => {
        if (!controller.signal.aborted) setSubmissions(listed);
      })
      .catch(() => {
        // 提交状态取不回时不展示，不打扰浏览。
      });
    return () => controller.abort();
  }, [officialEnabled]);

  const openEndpoint = (endpointKey: string) => navigate(endpointSettingsPath(endpointKey));

  const updateOfficial = async (patch: { enabled?: boolean; notice_seen?: boolean }) => {
    setOfficialBusy(true);
    try {
      const state = await API.updateOfficialService(patch);
      if (mounted.current) setOfficial(state);
    } catch (err) {
      if (mounted.current) pushToast(t("official_service_update_failed", { message: errMsg(err) }), "error");
    } finally {
      if (mounted.current) setOfficialBusy(false);
    }
  };

  const refreshAll = useCallback(async () => {
    const targets = sources.filter((source) => source.is_enabled).map((source) => source.id);
    setRefreshingAll(true);
    setRefreshingIds((current) => new Set([...current, ...targets]));
    try {
      const { sources: refreshed } = await API.refreshMarketSources();
      if (mounted.current) setSources((current) => mergeRefreshed(current, refreshed));
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
        if (mounted.current) setSources((current) => mergeRefreshed(current, [refreshed]));
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

  const sourcesById = useMemo(() => new Map(sources.map((source) => [source.id, source])), [sources]);
  // 关闭后不再展示上一轮取到的数字；重新开启时由拉取 effect 覆盖。
  const shownAggregates = officialEnabled ? aggregates : NO_AGGREGATES;
  const shownSubmissions = officialEnabled ? submissions : null;

  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-lg font-medium">{t("market_section_title")}</h2>

      {officialEnabled && official?.notice_seen === false && (
        <OfficialServiceNotice
          busy={officialBusy}
          onAcknowledge={() => void updateOfficial({ notice_seen: true })}
          onTurnOff={() => void updateOfficial({ enabled: false, notice_seen: true })}
        />
      )}

      <Tabs value={tab} onValueChange={(next: MarketTab) => navigate(marketSettingsPath(next), { replace: true })}>
        <div className="border-b border-border">
          <TabsList variant="line">
            <TabsTrigger value="browse">{t("market_tab_browse")}</TabsTrigger>
            <TabsTrigger value="shared">
              {t("market_tab_shared")}
              {shownSubmissions && shownSubmissions.length > 0 && (
                <span className="text-muted-foreground tabular-nums">{shownSubmissions.length}</span>
              )}
            </TabsTrigger>
            <TabsTrigger value="settings">{t("market_tab_settings")}</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="browse" className="mt-2">
          <MarketBrowseTab
            sources={sources}
            entries={entries}
            aggregates={shownAggregates}
            refreshingIds={refreshingIds}
            onOpen={setSelected}
            onOpenEndpoint={openEndpoint}
          />
        </TabsContent>
        <TabsContent value="shared" className="mt-2">
          <MarketSharedTab officialEnabled={officialEnabled} submissions={shownSubmissions} />
        </TabsContent>
        <TabsContent value="settings" className="mt-2">
          <MarketSettingsTab
            sources={sources}
            onSourcesChange={setSources}
            refreshingIds={refreshingIds}
            refreshingAll={refreshingAll}
            onRefresh={(id) => void refreshOne(id)}
            onRefreshAll={() => void refreshAll()}
            official={official}
            officialBusy={officialBusy}
            onOfficialChange={setOfficial}
            onOfficialUpdate={(patch) => void updateOfficial(patch)}
          />
        </TabsContent>
      </Tabs>

      {selected && (
        <MarketInstallDialog
          key={`${selected.source_id}/${selected.slug}`}
          entry={selected}
          official={
            officialEnabled && sourcesById.get(selected.source_id)?.kind === "official"
              ? {
                  aggregate: shownAggregates.get(aggregateKey(selected.source_id, selected.slug)) ?? null,
                  onRated: () => setAggregatesRevision((revision) => revision + 1),
                }
              : undefined
          }
          onClose={() => setSelected(null)}
          onInstallationChange={(installation) => {
            setInstallationRevision((revision) => revision + 1);
            setEntries(
              (current) =>
                current?.map((item) =>
                  item.source_id === selected.source_id && item.slug === selected.slug ? { ...item, installation } : item,
                ) ?? null,
            );
          }}
        />
      )}
    </div>
  );
}
