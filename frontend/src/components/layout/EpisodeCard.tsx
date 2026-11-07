import { useTranslation } from "react-i18next";
import { Clapperboard } from "lucide-react";
import { cn } from "cn";
import type { EpisodeMeta } from "@/types";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Progress } from "@/components/ui/progress";
import { itemCountKey, type GenerationRoute } from "@/utils/generation-mode";
import { useCostStore } from "@/stores/cost-store";
import { totalBreakdown } from "@/utils/cost-format";

interface EpisodeCardProps {
  ep: EpisodeMeta;
  /** One-based broadcast positions from episodes[], displayed instead of IDs. */
  position: number;
  active: boolean;
  onClick: () => void;
  /** Ads hide episode semantics, using clapboard icons instead of positions. */
  showEpisodeBadge?: boolean;
  /** Missing ep.title fallback text, project titles for ads. */
  fallbackTitle?: string;
  /** Required generation modes determine storyboard/video-unit nouns; missing wiring type-errors rather than silently mislabels. */
  route: GenerationRoute;
}

const STATUS_DOT: Record<string, string> = {
  completed: "bg-good",
  in_production: "animate-breath bg-primary",
  scripted: "bg-subtle-foreground",
  draft: "bg-muted-foreground",
  missing: "bg-muted-foreground",
};

const STATUS_LABEL_KEY: Record<string, string> = {
  completed: "dashboard:episode_status_done",
  in_production: "dashboard:episode_status_active",
  scripted: "dashboard:episode_status_draft",
  draft: "dashboard:episode_status_draft",
  missing: "dashboard:episode_status_idea",
};

/**
 * Sidebar episode cards: broadcast positions left, names/status/progress middle, actual costs right; current episodes have backgrounds.
 */
export function EpisodeCard({
  ep,
  position,
  active,
  onClick,
  showEpisodeBadge = true,
  fallbackTitle,
  route,
}: EpisodeCardProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const status = ep.status ?? "draft";
  const statusLabel = t(STATUS_LABEL_KEY[status] ?? STATUS_LABEL_KEY.draft);

  // Video progress uses current ∪ stale usable artifacts, shared with workbenches.
  // Zero video totals before scripts fall back to entry counts, describing content volume only.
  const videoTotal = ep.videos?.total ?? 0;
  const itemCount = ep.item_count ?? 0;
  const totalShots = videoTotal || itemCount;
  const availableVideos = ep.videos?.available ?? 0;
  const progress = videoTotal > 0 ? Math.round((availableVideos / videoTotal) * 100) : 0;
  const showProgress = videoTotal > 0 && (active || progress > 0);

  // Stale is usable, excluded from gaps; report separately as possible regeneration candidates.
  // Aggregate all artifact kinds per episode, matching lobby cards.
  const staleCount = (ep.storyboards?.stale ?? 0) + (ep.videos?.stale ?? 0);

  const episodeCost = useCostStore((s) => s.getEpisodeCost(ep.episode));
  const spentBreakdown = episodeCost ? totalBreakdown(episodeCost.totals.actual) : null;
  const spentEntries = spentBreakdown ? Object.entries(spentBreakdown).filter(([, v]) => v > 0) : [];
  const primaryCost = spentEntries.find(([c]) => c === "USD") ?? spentEntries[0];
  const costText = primaryCost ? `${primaryCost[0] === "CNY" ? "¥" : "$"}${primaryCost[1].toFixed(2)}` : null;

  const dur = ep.duration_seconds ?? 0;
  const durLabel = dur > 0 ? `${Math.floor(dur / 60)}:${String(dur % 60).padStart(2, "0")}` : null;
  const title = ep.title || fallbackTitle || t("common:episode_position_name", { position });

  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 rounded-md p-2 text-left transition-colors focus-ring",
        active ? "bg-primary/10" : "hover:bg-muted",
      )}
    >
      <span
        className={cn(
          "num grid size-8 shrink-0 place-items-center rounded-md text-xs font-medium",
          active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
        )}
      >
        {showEpisodeBadge ? position : <Clapperboard aria-hidden className="size-4" />}
      </span>

      <span className="flex min-w-0 flex-col gap-0.5">
        <TruncatedText
          text={title}
          focusable={false}
          className={cn("text-sm", active ? "font-medium text-foreground" : "text-subtle-foreground")}
        />
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <span aria-hidden className={cn("size-1.5 rounded-full", STATUS_DOT[status] ?? STATUS_DOT.draft)} />
            {statusLabel}
          </span>
          {totalShots > 0 && (
            <span className="num">
              {videoTotal > 0 ? (
                <>
                  <span aria-hidden>{`${availableVideos}/${videoTotal}`}</span>
                  <span className="sr-only">
                    {t("episode_available_videos_hint", { count: availableVideos, total: videoTotal })}
                  </span>
                </>
              ) : (
                t(itemCountKey(route), { count: itemCount })
              )}
              {durLabel ? ` · ${durLabel}` : ""}
            </span>
          )}
          {staleCount > 0 && (
            <span className="num inline-flex items-center gap-1 text-warn">
              <span aria-hidden className="size-1.5 rounded-full bg-warn" />
              <span aria-hidden>{staleCount}</span>
              <span className="sr-only">{t("episode_stale_artifacts", { count: staleCount })}</span>
            </span>
          )}
        </span>
        {showProgress && <Progress value={progress} aria-hidden className="mt-1" />}
      </span>

      {costText && (
        <span className={cn("num self-start text-xs", active ? "text-primary" : "text-muted-foreground")}>
          {costText}
        </span>
      )}
    </button>
  );
}
