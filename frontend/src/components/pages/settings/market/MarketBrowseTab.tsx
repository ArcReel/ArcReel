import { useState } from "react";
import { AlertTriangle, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MarketMediaType } from "@/app-routes";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { MarketEntry, MarketEntryAggregate, MarketSourceInfo } from "@/types";
import { formatRelativeTime } from "@/utils/date-format";
import { MarketEntryCard } from "./MarketEntryCard";
import { aggregateKey } from "./market-keys";
import { SourceStatusDot } from "./market-source-status";

const MEDIA_FILTERS = [
  { id: "all", labelKey: "market_media_all" },
  { id: "image", labelKey: "media_type_image" },
  { id: "video", labelKey: "media_type_video" },
] as const;

type MediaFilter = (typeof MEDIA_FILTERS)[number]["id"];

function matchesQuery(entry: MarketEntry, query: string): boolean {
  if (!query) return true;
  const haystack = `${entry.name}\n${entry.author}\n${entry.description ?? ""}`.toLocaleLowerCase();
  return haystack.includes(query.toLocaleLowerCase());
}

/**
 * Browse tabs: search, media filters, sources when multiple enabled sources exist, Installed only, then entry grids.
 * Endpoints are the only entry type, so omit type filters. Failed sources show reasons/snapshot times above filters.
 */
export function MarketBrowseTab({
  initialMedia,
  sources,
  entries,
  aggregates,
  refreshingIds,
  onOpen,
  onOpenEndpoint,
}: {
  /** Initial media filters from URL `media`; null means All. */
  initialMedia: MarketMediaType | null;
  sources: MarketSourceInfo[];
  /** null means still loading. */
  entries: MarketEntry[] | null;
  aggregates: ReadonlyMap<string, MarketEntryAggregate>;
  refreshingIds: ReadonlySet<number>;
  onOpen: (entry: MarketEntry) => void;
  onOpenEndpoint: (endpointKey: string) => void;
}) {
  const { t, i18n } = useTranslation("dashboard");
  const [query, setQuery] = useState("");
  const [hiddenSourceIds, setHiddenSourceIds] = useState<ReadonlySet<number>>(new Set());
  const [onlyInstalled, setOnlyInstalled] = useState(false);
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>(initialMedia ?? "all");

  const enabled = sources.filter((source) => source.is_enabled);
  const failing = enabled.filter((source) => source.status !== "ok" && source.status !== "never_fetched");
  const sourcesById = new Map(sources.map((source) => [source.id, source]));
  // Hide disabled-source entries even before source changes refetch lists.
  const available = (entries ?? []).filter((entry) => sourcesById.get(entry.source_id)?.is_enabled === true);
  const trimmedQuery = query.trim();
  const visible = available.filter(
    (entry) =>
      !hiddenSourceIds.has(entry.source_id) &&
      matchesQuery(entry, trimmedQuery) &&
      (mediaFilter === "all" || entry.media_type === mediaFilter) &&
      (!onlyInstalled || !!entry.installation),
  );

  return (
    <div className="flex flex-col gap-4">
      {failing.length > 0 && (
        <div role="status" className="flex items-start gap-2 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="flex min-w-0 flex-col gap-0.5">
            <p className="font-medium">{t("market_failing_title")}</p>
            {failing.map((source) => {
              const snapshotTime = formatRelativeTime(source.fetched_at, i18n.language);
              const status = t(`market_status_${source.status}`);
              return (
                <p key={source.id} className="break-words">
                  {source.display_name}
                  {source.last_error
                    ? t("market_banner_detail", { status, error: source.last_error })
                    : t("market_banner_detail_no_error", { status })}
                  {snapshotTime !== null && t("market_banner_snapshot", { time: snapshotTime })}
                </p>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <InputGroup className="w-full @md/page:w-72">
          <InputGroupAddon>
            <Search aria-hidden />
          </InputGroupAddon>
          <InputGroupInput
            type="search"
            aria-label={t("market_search_label")}
            placeholder={t("market_search_placeholder")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </InputGroup>
        <ToggleGroup
          aria-label={t("market_media_filter_label")}
          variant="outline"
          size="sm"
          spacing={0}
          value={[mediaFilter]}
          onValueChange={(value: string[]) => {
            // Radio reselection clears values; retain existing filters then.
            const next = MEDIA_FILTERS.find((filter) => filter.id === value[0]);
            if (next) setMediaFilter(next.id);
          }}
        >
          {MEDIA_FILTERS.map((filter) => (
            <ToggleGroupItem key={filter.id} value={filter.id}>
              {t(filter.labelKey)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        {enabled.length > 1 && (
          <ToggleGroup
            aria-label={t("market_source_filter_label")}
            variant="outline"
            size="sm"
            multiple
            className="flex-wrap"
            value={enabled.filter((source) => !hiddenSourceIds.has(source.id)).map((source) => String(source.id))}
            onValueChange={(value: string[]) => {
              const shown = new Set(value);
              setHiddenSourceIds(
                new Set(enabled.filter((source) => !shown.has(String(source.id))).map((source) => source.id)),
              );
            }}
          >
            {enabled.map((source) => (
              <ToggleGroupItem key={source.id} value={String(source.id)}>
                <SourceStatusDot source={source} refreshing={refreshingIds.has(source.id)} />
                {source.display_name}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        )}
        <div className="ml-auto">
          <Label>
            <Switch checked={onlyInstalled} onCheckedChange={setOnlyInstalled} />
            {t("market_only_installed")}
          </Label>
        </div>
      </div>

      {entries !== null && (
        <>
          <p className="text-sm text-muted-foreground tabular-nums">
            {visible.length === available.length
              ? t("market_entry_count", { count: available.length })
              : t("market_entry_count_filtered", { shown: visible.length, count: available.length })}
          </p>
          {visible.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
              {available.length === 0 ? t("market_no_entries") : t("market_no_matching_entries")}
            </p>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3">
              {visible.map((entry) => {
                const source = sourcesById.get(entry.source_id);
                return (
                  <MarketEntryCard
                    key={`${entry.source_id}/${entry.slug}`}
                    entry={entry}
                    sourceName={source?.display_name ?? entry.source_display_name}
                    sourceKind={source?.kind ?? null}
                    aggregate={aggregates.get(aggregateKey(entry.source_id, entry.slug)) ?? null}
                    onOpen={() => onOpen(entry)}
                    onInstalledOpen={() => {
                      if (entry.installation) onOpenEndpoint(entry.installation.endpoint_key);
                    }}
                  />
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
