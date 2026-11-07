import { useEffect, useId, useState } from "react";
import { AlertTriangle, BadgeCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { MarketEntry, MarketEntryAggregate, MarketSourceKind } from "@/types";
import { MarketEntryStats } from "./MarketEntryStats";
import { MarketInstallBadges } from "./MarketInstallBadges";

const ICON_SIZE = 40;

/** Fetch proxied icons into object URLs; null on failure lets callers show initial-letter placeholders. */
function useEntryIconUrl(entry: MarketEntry): string | null {
  const [url, setUrl] = useState<string | null>(null);
  const { source_id: sourceId, slug, version, icon } = entry;

  useEffect(() => {
    if (icon === null) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    API.getMarketEntryIcon(sourceId, slug, version, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        // Missing icons never disrupt browsing; retain initial placeholders.
      });
    return () => {
      controller.abort();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
      setUrl(null);
    };
  }, [icon, slug, sourceId, version]);

  return url;
}

export function EntryIcon({ entry }: { entry: MarketEntry }) {
  const url = useEntryIconUrl(entry);
  if (url !== null) {
    return <img src={url} alt="" width={ICON_SIZE} height={ICON_SIZE} className="size-10 shrink-0 rounded-md object-contain" />;
  }
  return (
    <div
      aria-hidden
      className="flex size-10 shrink-0 items-center justify-center rounded-md bg-muted text-lg font-medium text-muted-foreground"
    >
      {entry.name.trim().charAt(0).toUpperCase()}
    </div>
  );
}

/** Origin market sources; official sources get preceding badges announced as Official. */
export function SourceChip({ name, kind }: { name: string; kind: MarketSourceKind | null }) {
  const { t } = useTranslation("dashboard");
  return (
    <span className="relative inline-flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
      {kind === "official" && (
        <BadgeCheck className="size-3.5 shrink-0 text-primary" role="img" aria-label={t("market_source_official")} />
      )}
      <TruncatedText text={name} />
    </span>
  );
}

/**
 * Market entry cards: names (open install dialogs, click areas cover cards), authors/versions, two-line descriptions, media/install states,
 * origins (plus official-service install counts/ratings), and primary buttons.
 * Primary actions: uninstalled Install/updateable Update open install dialogs; latest Open endpoint navigates directly to endpoints.
 * Unmet `min_app_version` disables primary buttons with version requirements, while details remain accessible.
 */
export function MarketEntryCard({
  entry,
  sourceName,
  sourceKind,
  aggregate = null,
  onOpen,
  onInstalledOpen,
}: {
  entry: MarketEntry;
  /** Current source display names, falling back to entry-supplied names if absent from source lists. */
  sourceName: string;
  sourceKind: MarketSourceKind | null;
  /** Present only with enabled official services and fetched aggregates; absent means no numbers displayed. */
  aggregate?: MarketEntryAggregate | null;
  onOpen: () => void;
  onInstalledOpen: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const unmet = !entry.min_app_version_satisfied && entry.min_app_version !== null;
  const { installation } = entry;
  const current = installation?.state === "current";
  const media = entry.media_type === "image" || entry.media_type === "video" ? entry.media_type : null;

  return (
    <article
      aria-labelledby={titleId}
      className="relative flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-input"
    >
      <div className="flex min-w-0 items-start gap-3">
        <EntryIcon entry={entry} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h3 id={titleId} className="text-sm font-medium">
            {/* Name-button pseudo-elements cover cards, opening details from blank areas; later positioned primary buttons overlay them. */}
            <button
              type="button"
              onClick={onOpen}
              className="line-clamp-2 text-left outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-3 focus-visible:after:ring-ring/50"
            >
              {entry.name}
            </button>
          </h3>
          <TruncatedText
            text={t("market_entry_byline", { author: entry.author, version: entry.version })}
            className="text-xs text-muted-foreground"
            focusable={false}
          />
        </div>
      </div>
      {entry.description && <p className="line-clamp-2 text-sm text-subtle-foreground">{entry.description}</p>}
      {(media || installation || unmet) && (
        <div className="flex flex-wrap items-center gap-1">
          {media && <Badge variant="secondary">{t(media === "image" ? "media_type_image" : "media_type_video")}</Badge>}
          {installation && <MarketInstallBadges state={installation.state} modified={installation.modified} />}
          {unmet && (
            <Badge variant="outline">
              <AlertTriangle data-icon="inline-start" className="text-warn" aria-hidden />
              {t("market_requires_app", { version: entry.min_app_version })}
            </Badge>
          )}
        </div>
      )}
      <div className="mt-auto flex min-w-0 items-center gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <SourceChip name={sourceName} kind={sourceKind} />
          {aggregate && <MarketEntryStats aggregate={aggregate} />}
        </div>
        <Button
          variant={current ? "ghost" : "outline"}
          size="sm"
          disabled={unmet && !current}
          onClick={current ? onInstalledOpen : onOpen}
          className="relative shrink-0"
        >
          {t(current ? "market_open_endpoint" : installation ? "market_update" : "market_install")}
        </Button>
      </div>
    </article>
  );
}
