import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { MoreHorizontal, Trash2 } from "lucide-react";

import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InfoTip, InfoTipContent, InfoTipTrigger } from "@/components/ui/info-tip";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useCostStore } from "@/stores/cost-store";
import type { EpisodeCost, EpisodeMeta } from "@/types";
import { currentScriptBreakdown, formatCost, totalBreakdown } from "@/utils/cost-format";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";
import { itemCountKey, type GenerationRoute } from "@/utils/generation-mode";

import { episodesViewPath } from "../episodes/episodes-view-model";
import { useDeleteEpisode } from "../episodes/useDeleteEpisode";
import { EditableEpisodeTitle } from "./EditableEpisodeTitle";

function formatDuration(seconds: number): string {
  // Round total seconds before splitting, or 119.6 seconds becomes 1:60
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * Episode costs: spent includes historical expense from deleted/edited entries; remaining settles only current-script work under its rules.
 * Without amounts, all three display “—”, without distinguishing zero from unknown.
 */
function episodeCostSummary(cost: EpisodeCost) {
  const estimate = totalBreakdown(cost.totals.estimate);
  const currentActual = currentScriptBreakdown(cost.totals.actual);
  const remaining: Record<string, number> = {};
  for (const [currency, amount] of Object.entries(estimate)) {
    remaining[currency] = Math.max(0, amount - (currentActual[currency] ?? 0));
  }
  return {
    estimate: formatCost(estimate),
    spent: formatCost(totalBreakdown(cost.totals.actual)),
    remaining: formatCost(remaining),
  };
}

/**
 * Episode head in the first header row: badge, editable title, entry count · duration · video progress · estimated cost (spent/remaining in hints),
 * and the ⋯ menu. All data comes from project details/cost estimates, independently of the canvas.
 */
export function EpisodeHead({
  projectName,
  episode,
  meta,
  route,
  canEditTitle,
  onSaveTitle,
  canDelete,
}: {
  projectName: string;
  episode: number;
  meta: EpisodeMeta | undefined;
  route: GenerationRoute;
  canEditTitle: boolean;
  onSaveTitle: (title: string) => Promise<void>;
  /** Ad/short-film projects are single-episode and demos are read-only: no deletion. */
  canDelete: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const ledger = useEpisodeLedger();
  const position = episodePosition(ledger, episode);
  // Cost data is a singleton: after switching projects, it remains old until refreshed; match project names before use.
  const cost = useCostStore((s) =>
    s.costData?.project_name === projectName ? s._episodeIndex.get(episode) : undefined,
  );
  const confirmLeave = useConfirmLeave();
  // Guard leaving at deletion confirmation: discarding edits before confirming deletion would waste them if deletion is canceled.
  const deletion = useDeleteEpisode(projectName, () => setLocation(episodesViewPath()), confirmLeave);

  const facts: string[] = [];
  if (meta?.item_count != null) facts.push(t(itemCountKey(route), { count: meta.item_count }));
  if (meta?.duration_seconds) facts.push(t("episode_head_duration", { duration: formatDuration(meta.duration_seconds) }));
  if (meta?.videos && meta.videos.total > 0) {
    facts.push(t("episode_head_videos", { available: meta.videos.available, total: meta.videos.total }));
  }
  const costSummary = cost ? episodeCostSummary(cost) : null;
  const costDetail = costSummary
    ? t("episode_head_cost_detail", { spent: costSummary.spent, remaining: costSummary.remaining })
    : "";

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2.5">
      <Badge variant="secondary" translate="no">
        {t("episode_header_episode_chip", {
          number: position === null ? "—" : String(position).padStart(2, "0"),
        })}
      </Badge>
      <div className="min-w-0 max-w-[24em] shrink">
        <EditableEpisodeTitle
          title={meta?.title ?? ""}
          placeholder={episodeDisplayName(ledger, episode, t)}
          canEdit={canEditTitle}
          onSave={onSaveTitle}
        />
      </div>
      <p className="num flex min-w-0 shrink-[2] items-center gap-1.5 text-xs whitespace-nowrap text-muted-foreground">
        <span className="min-w-0 truncate">{facts.join(" · ")}</span>
        {costSummary ? (
          <>
            {facts.length > 0 ? <span aria-hidden>·</span> : null}
            <InfoTip>
              <InfoTipTrigger render={<button type="button" className="inline-flex min-h-6 shrink-0 items-center rounded-sm" />}>
                {t("episode_head_cost", { cost: costSummary.estimate })}
              </InfoTipTrigger>
              <InfoTipContent title={t("episode_head_cost", { cost: costSummary.estimate })}>{costDetail}</InfoTipContent>
            </InfoTip>
          </>
        ) : null}
      </p>
      {canDelete ? (
        <>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={<Button variant="ghost" size="icon-xs" aria-label={t("episode_head_more")} />}
            >
              <MoreHorizontal aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-auto">
              <DropdownMenuItem variant="destructive" onClick={() => void deletion.requestDelete(episode)}>
                <Trash2 aria-hidden />
                {t("episode_menu_delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {deletion.dialog}
        </>
      ) : null}
    </div>
  );
}
