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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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
  // 先对总秒数取整再拆分，否则 119.6 秒会写成 1:60
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * 一集的费用：「已花」含历史支出（已删改条目留下的），「剩余」只对当前剧本结算，按当前剧本口径扣减。
 * 没有金额时三项都写「—」，不区分 0 与未知。
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
 * 集页页头第一行的集头：集徽标、可编辑的集标题、条目数 · 时长 · 视频进度 · 预估费用（已花与剩余在提示里），
 * 以及「⋯」菜单。数据都来自项目详情与费用估算，画布不参与。
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
  /** 广告/短片恒单集、演示项目只读：不给删除。 */
  canDelete: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const ledger = useEpisodeLedger();
  const position = episodePosition(ledger, episode);
  // 费用表是单例：切项目后到新数据到达前仍是上一个项目的，按项目名核对后才用。
  const cost = useCostStore((s) =>
    s.costData?.project_name === projectName ? s._episodeIndex.get(episode) : undefined,
  );
  const confirmLeave = useConfirmLeave();
  // 离开拦截放在确认删除这一步：先问未保存修改再确认删除，放弃后取消删除就白丢了修改
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
            <Tooltip>
              <TooltipTrigger
                render={
                  // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 已花与剩余只在提示里，须能用键盘聚焦打开；它没有可执行的动作，不渲染为 button
                  <span tabIndex={0} className="focus-ring shrink-0 rounded-sm" />
                }
              >
                {t("episode_head_cost", { cost: costSummary.estimate })}
                <span className="sr-only">{costDetail}</span>
              </TooltipTrigger>
              <TooltipContent>{costDetail}</TooltipContent>
            </Tooltip>
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
