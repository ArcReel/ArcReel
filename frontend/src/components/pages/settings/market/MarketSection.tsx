import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, Search, Settings2 } from "lucide-react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { formatRelativeTime } from "@/utils/date-format";
import {
  GHOST_BTN_CLS,
  INPUT_CLS,
  ambientGlowStyle,
  posterGridStyle,
} from "@/components/shared/darkroom-tokens";
import { PillSwitch } from "@/components/legacy/PillSwitch";
import type {
  MarketEntry,
  MarketEntryAggregate,
  MarketSourceInfo,
  MarketSubmission,
  OfficialServiceState,
} from "@/types";
import { MarketEntryCard } from "./MarketEntryCard";
import { MarketInstallDialog } from "./MarketInstallDialog";
import { MARKET_CONTRIBUTING_URL } from "./market-links";
import { KICKER_ACCENT_CLS, KICKER_CLS, SourceStatusDot } from "./market-source-status";
import { MarketSourcesDialog } from "./MarketSourcesDialog";
import { MarketSubmissionList } from "./MarketSubmissionList";
import { OfficialServiceNotice } from "./OfficialServiceNotice";

const ENTRY_TYPES = [
  { id: "endpoint", labelKey: "market_type_endpoint", available: true },
  { id: "prompt", labelKey: "market_type_prompt", available: false },
  { id: "style", labelKey: "market_type_style", available: false },
] as const;

const MEDIA_FILTERS = [
  { id: "all", labelKey: "market_media_all" },
  { id: "image", labelKey: "media_type_image" },
  { id: "video", labelKey: "media_type_video" },
] as const;

type MediaFilter = (typeof MEDIA_FILTERS)[number]["id"];

/**
 * 按 id 合并刷新结果，只改写刷新产出的字段、保持原顺序；显示名与启停以本地为准，
 * 迟到的刷新响应不会覆盖期间完成的修改。
 */
function mergeRefreshed(
  current: MarketSourceInfo[],
  updated: MarketSourceInfo[],
): MarketSourceInfo[] {
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
    .map((source) =>
      [source.id, source.is_enabled, source.display_name, source.fetched_at, source.updated_at].join(":"),
    )
    .join("|");
}

/** Darkroom kicker 固定英文，不进 i18n；只按数量切换单复数。 */
function marketKicker(entryCount: number, sourceCount: number): string {
  const endpoints = entryCount === 1 ? "endpoint" : "endpoints";
  const sources = sourceCount === 1 ? "source" : "sources";
  return `Market · ${entryCount} ${endpoints} from ${sourceCount} ${sources}`;
}

const NO_AGGREGATES: ReadonlyMap<string, MarketEntryAggregate> = new Map();

function aggregateKey(sourceId: number, slug: string): string {
  return `${sourceId}/${slug}`;
}

function matchesQuery(entry: MarketEntry, query: string): boolean {
  if (!query) return true;
  const haystack = `${entry.name}\n${entry.author}\n${entry.description ?? ""}`.toLocaleLowerCase();
  return haystack.includes(query.toLocaleLowerCase());
}

/**
 * 市场小节：hero 头部、失败横幅、筛选行、条目网格与市场源管理弹窗。打开时先渲染缓存的
 * 源列表与条目，再在后台刷新距上次成功刷新超过 1 小时的启用源；源有变化时重新拉取条目。
 * 官方服务开启时另拉官方市场源条目的安装量与评分，首次进入显示一次说明；关闭或读不到状态时不展示任何官方服务元素。
 */
export function MarketSection() {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const [sources, setSources] = useState<MarketSourceInfo[]>([]);
  const [sourcesLoaded, setSourcesLoaded] = useState(false);
  const [entries, setEntries] = useState<MarketEntry[] | null>(null);
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [refreshingIds, setRefreshingIds] = useState<ReadonlySet<number>>(new Set());
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hiddenSourceIds, setHiddenSourceIds] = useState<ReadonlySet<number>>(new Set());
  const [onlyInstalled, setOnlyInstalled] = useState(false);
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all");
  const [installationRevision, setInstallationRevision] = useState(0);
  const [selected, setSelected] = useState<MarketEntry | null>(null);
  const [official, setOfficial] = useState<OfficialServiceState | null>(null);
  const [officialBusy, setOfficialBusy] = useState(false);
  const [aggregates, setAggregates] = useState<ReadonlyMap<string, MarketEntryAggregate>>(new Map());
  const [aggregatesRevision, setAggregatesRevision] = useState(0);
  const [submissions, setSubmissions] = useState<MarketSubmission[]>([]);
  const [location, navigate] = useLocation();
  const onlyInstalledId = useId();
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
        const { sources: refreshed } = await API.refreshMarketSources({
          staleOnly: true,
          signal: controller.signal,
        });
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
        if (controller.signal.aborted) return;
        setEntries(response.entries);
        setAppVersion(response.app_version);
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

  const openEndpoint = (endpointKey: string) => {
    const params = new URLSearchParams({ section: "endpoints", endpoint: endpointKey });
    navigate(`${location}?${params}`);
  };

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
      if (!mounted.current) return;
      setSources((current) => mergeRefreshed(current, refreshed));
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

  const toggleSourceFilter = (id: number) => {
    setHiddenSourceIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const enabled = sources.filter((source) => source.is_enabled);
  const failing = enabled.filter((source) => source.status !== "ok" && source.status !== "never_fetched");
  const sourcesById = useMemo(() => new Map(sources.map((source) => [source.id, source])), [sources]);
  const allEntries = entries ?? [];
  // 关闭后不再展示上一轮取到的数字；重新开启时由拉取 effect 覆盖。
  const shownAggregates = officialEnabled ? aggregates : NO_AGGREGATES;
  const trimmedQuery = query.trim();
  const visible = allEntries.filter(
    (entry) =>
      sourcesById.get(entry.source_id)?.is_enabled === true &&
      !hiddenSourceIds.has(entry.source_id) &&
      matchesQuery(entry, trimmedQuery) &&
      (mediaFilter === "all" || entry.media_type === mediaFilter) &&
      (!onlyInstalled || !!entry.installation),
  );

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
          <div className="min-w-[16rem] flex-1">
            <div className={KICKER_ACCENT_CLS}>
              {marketKicker(allEntries.length, enabled.length)}
            </div>
            <h2 className="mt-1 font-editorial text-[32px] leading-none text-text">
              {t("market_section_title")}
            </h2>
          </div>
          <label className="relative w-full sm:w-72">
            <span className="sr-only">{t("market_search_label")}</span>
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-4"
              aria-hidden
            />
            <input
              type="search"
              className={`${INPUT_CLS} pl-8`}
              placeholder={t("market_search_placeholder")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
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

        {officialEnabled && official?.notice_seen === false && (
          <OfficialServiceNotice
            busy={officialBusy}
            onAcknowledge={() => void updateOfficial({ notice_seen: true })}
            onTurnOff={() => void updateOfficial({ enabled: false, notice_seen: true })}
          />
        )}

        {failing.length > 0 && (
          <div
            role="status"
            className="mb-5 rounded-[8px] border border-warn/30 bg-warn/8 px-3 py-2 text-[12px] text-text-2"
          >
            {failing.map((source) => {
              const snapshotTime = formatRelativeTime(source.fetched_at, i18n.language);
              const status = t(`market_status_${source.status}`);
              return (
                <div key={source.id}>
                  <strong className="text-text">{source.display_name}</strong>
                  {source.last_error
                    ? t("market_banner_detail", { status, error: source.last_error })
                    : t("market_banner_detail_no_error", { status })}
                  {snapshotTime !== null && t("market_banner_snapshot", { time: snapshotTime })}
                </div>
              );
            })}
          </div>
        )}

        <div className="mb-6 flex flex-wrap items-center gap-x-5 gap-y-3">
          <div role="group" aria-label={t("market_type_filter_label")} className="flex items-center gap-2">
            {ENTRY_TYPES.map((type) => (
              <button
                key={type.id}
                type="button"
                disabled={!type.available}
                aria-pressed={type.available}
                className={`rounded-full border px-3 py-1 text-[12px] ${
                  type.available
                    ? "border-accent/45 bg-accent-dim text-text"
                    : "cursor-not-allowed border-hairline-soft text-text-4"
                }`}
              >
                {t(type.labelKey)}
                {!type.available && (
                  <span className="ml-1.5 font-mono text-[9.5px] uppercase tracking-[0.1em]">
                    {t("market_type_soon")}
                  </span>
                )}
              </button>
            ))}
          </div>
          <span className="hidden h-4 w-px bg-hairline sm:block" aria-hidden />
          <div
            role="group"
            aria-label={t("market_media_filter_label")}
            className="flex flex-wrap items-center gap-1.5"
          >
            <span className={`${KICKER_CLS} mr-1`} aria-hidden>
              Media
            </span>
            {MEDIA_FILTERS.map((filter) => {
              const on = mediaFilter === filter.id;
              return (
                <button
                  key={filter.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setMediaFilter(filter.id)}
                  className={`rounded-full border px-2.5 py-1 text-[11.5px] transition-colors ${
                    on
                      ? "border-accent/45 bg-accent-dim text-text"
                      : "border-hairline-soft text-text-3 hover:text-text"
                  }`}
                >
                  {t(filter.labelKey)}
                </button>
              );
            })}
          </div>
          <span className="hidden h-4 w-px bg-hairline sm:block" aria-hidden />
          <div
            role="group"
            aria-label={t("market_source_filter_label")}
            className="flex flex-wrap items-center gap-1.5"
          >
            <span className={`${KICKER_CLS} mr-1`} aria-hidden>
              Source
            </span>
            {enabled.map((source) => {
              const on = !hiddenSourceIds.has(source.id);
              return (
                <button
                  key={source.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggleSourceFilter(source.id)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] transition-colors ${
                    on
                      ? "border-accent/45 bg-accent-dim text-text"
                      : "border-hairline-soft text-text-3 hover:text-text"
                  }`}
                >
                  <SourceStatusDot source={source} refreshing={refreshingIds.has(source.id)} />
                  {source.display_name}
                </button>
              );
            })}
          </div>
          <div className="ml-auto flex items-center gap-2 text-[12px] text-text-3">
            <span id={onlyInstalledId}>{t("market_only_installed")}</span>
            <PillSwitch
              checked={onlyInstalled}
              onToggle={() => setOnlyInstalled((value) => !value)}
              labelledBy={onlyInstalledId}
            />
          </div>
        </div>

        {entries !== null &&
          (visible.length === 0 ? (
            <p className="rounded-[10px] border border-dashed border-hairline-soft px-4 py-12 text-center text-[12.5px] text-text-4">
              {allEntries.length === 0 ? t("market_no_entries") : t("market_no_matching_entries")}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
              {visible.map((entry) => {
                const source = sourcesById.get(entry.source_id);
                return (
                  <MarketEntryCard
                    key={`${entry.source_id}/${entry.slug}`}
                    entry={entry}
                    sourceName={source?.display_name ?? entry.source_display_name}
                    sourceKind={source?.kind ?? null}
                    appVersion={appVersion}
                    aggregate={shownAggregates.get(aggregateKey(entry.source_id, entry.slug)) ?? null}
                    onOpen={() => setSelected(entry)}
                    onInstalledOpen={() => {
                      if (entry.installation) openEndpoint(entry.installation.endpoint_key);
                    }}
                  />
                );
              })}
            </div>
          ))}

        {officialEnabled && submissions.length > 0 && (
          <MarketSubmissionList submissions={submissions} onOpenEndpoint={openEndpoint} />
        )}

        <div className="mt-14 rounded-[12px] border border-dashed border-hairline px-6 py-5 text-center">
          <div className={KICKER_CLS}>Contribute</div>
          <p className="mt-1.5 text-[13px] text-text-2">{t("market_contribute_body")}</p>
          <a
            href={MARKET_CONTRIBUTING_URL}
            target="_blank"
            rel="noreferrer"
            className={`${GHOST_BTN_CLS} mt-3`}
          >
            {t("market_contribute_link")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </div>
      </div>

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
