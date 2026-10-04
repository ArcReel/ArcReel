import { useEffect, useRef, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { cn } from "cn";
import { ArrowUpRight, Combine, FileText, History, ListX, MoreHorizontal, Plus, RefreshCw, Trash2 } from "lucide-react";

import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode } from "@/types";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

import { PlanGapButton } from "./PlanGapButton";
import { cutEpisodeActions } from "./manual-split-model";
import { episodeHue, formatVolume, otherEpisodes, outlineFileGroups } from "./episodes-view-model";

/** 集目录每行「⋯」里的单集操作。 */
export interface EpisodeMenuActions {
  /** 手工切分的请求在途：合并与清除暂不可用。 */
  splitBusy: boolean;
  /** 不能发起规划的原因：分集规划在进行或已有新的分集方案；可以时为 null。 */
  planBlocked: string | null;
  onCreateAfter: (episode: number) => void;
  onMergeWithNext: (episode: number) => void;
  onClearAfter: (episode: number) => void;
  onReplan: (episode: number) => void;
  onDelete: (episode: number) => void;
}

interface EpisodeOutlineProps {
  view: EpisodesView;
  episodes: EpisodeMeta[];
  /** 原文视口顶部所在的集。 */
  current: number | null;
  onLocate: (episode: number) => void;
  onLocateFile: (sourceFile: string) => void;
  actions: EpisodeMenuActions;
}

/**
 * 「分集」视图的集目录：按整本源文文件分组列出切出集，夹在其间的未切分原文是虚线行；
 * 原文不在整本源文里的集列在「其他集」。高亮原文视口顶部所在的集，点击一集让原文滚到这一集。
 */
export function EpisodeOutline({ view, episodes, current, onLocate, onLocateFile, actions }: EpisodeOutlineProps) {
  const { t } = useTranslation("dashboard");
  const groups = outlineFileGroups(view, episodes);
  const others = otherEpisodes(view, episodes);
  const listRef = useRef<HTMLElement>(null);

  // 高亮随原文滚动变化时，把这一行带进集目录的可见范围
  useEffect(() => {
    if (current === null) return;
    listRef.current?.querySelector(`[data-outline-episode="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const row = (episode: EpisodeMeta, info: EpisodesViewEpisode | null) => (
    <OutlineRow
      key={episode.episode}
      view={view}
      episodes={episodes}
      episode={episode}
      info={info}
      active={current === episode.episode}
      onLocate={onLocate}
      actions={actions}
    />
  );

  return (
    <nav
      ref={listRef}
      aria-label={t("episodes_outline_label")}
      className="relative flex w-[clamp(240px,26cqw,300px)] shrink-0 flex-col gap-4 overflow-y-auto border-r px-2 py-3"
    >
      {groups.map((group) => (
        <section key={group.file.source_file} aria-label={group.file.name} className="flex flex-col gap-0.5">
          <button
            type="button"
            onClick={() => onLocateFile(group.file.source_file)}
            className="focus-ring flex w-full min-w-0 items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs text-muted-foreground hover:text-foreground"
          >
            <FileText className="size-3.5 shrink-0" aria-hidden />
            <TruncatedText text={group.file.name} focusable={false} />
          </button>
          <ul className="flex flex-col gap-px">
            {group.rows.map((item) =>
              item.kind === "episode" ? (
                row(item.episode, item.info)
              ) : (
                <li
                  key={item.key}
                  className="flex flex-col items-start gap-1.5 rounded-md border border-dashed border-primary/30 px-2.5 py-1.5 text-xs text-muted-foreground"
                >
                  {t("episodes_view_gap_row", { volume: formatVolume(t, item.units, view.unit) })}
                  <PlanGapButton sourceFile={item.sourceFile} end={item.end} blocked={actions.planBlocked} />
                </li>
              ),
            )}
          </ul>
          {group.tailUnits > 0 ? (
            <p className="px-2.5 pt-1 text-xs text-muted-foreground">
              {t("episodes_view_tail_after", { volume: formatVolume(t, group.tailUnits, view.unit) })}
            </p>
          ) : null}
        </section>
      ))}
      {others.length > 0 ? (
        <section aria-labelledby="episodes-outline-others" className="flex flex-col gap-0.5">
          <h3 id="episodes-outline-others" className="px-2 py-1 text-xs text-muted-foreground">
            {t("episodes_view_other_section")}
          </h3>
          <ul className="flex flex-col gap-px">{others.map(({ episode, info }) => row(episode, info))}</ul>
        </section>
      ) : null}
    </nav>
  );
}

function OutlineRow({
  view,
  episodes,
  episode,
  info,
  active,
  onLocate,
  actions,
}: {
  view: EpisodesView;
  episodes: EpisodeMeta[];
  episode: EpisodeMeta;
  info: EpisodesViewEpisode | null;
  active: boolean;
  onLocate: (episode: number) => void;
  actions: EpisodeMenuActions;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const id = episode.episode;
  const position = episodePosition(episodes, id);
  const title = episode.title?.trim() || t("dashboard:episodes_view_untitled");
  const volume =
    info?.units != null ? formatVolume(t, info.units, view.unit) : t(`dashboard:episodes_view_origin_${info?.origin ?? "none"}`);
  const rowClass = cn(
    "focus-ring flex w-full items-center gap-2 rounded-md py-1.5 pr-9 pl-2 text-left text-sm transition-colors duration-fast",
    active ? "bg-primary/15 text-foreground" : "text-subtle-foreground hover:bg-muted/60 hover:text-foreground",
  );
  const content = (
    <>
      <span aria-hidden className="h-4 w-0.75 shrink-0 rounded-full bg-episode" />
      <span
        aria-hidden
        className={cn("num w-6 shrink-0 text-right", active ? "text-episode" : "text-muted-foreground")}
      >
        {position ?? "—"}
      </span>
      {position !== null ? (
        <span className="sr-only">{t("common:episode_position_name", { position })}</span>
      ) : null}
      <TruncatedText text={title} focusable={false} className="flex-1" />
      {episode.ledger_status === "stale" ? (
        <>
          <History aria-hidden className="size-3.5 shrink-0 text-primary" />
          <span className="sr-only">{t("dashboard:episodes_view_replanned")}</span>
        </>
      ) : null}
      <span className="num shrink-0 text-xs text-muted-foreground">{volume}</span>
    </>
  );
  return (
    <li data-outline-episode={id} className="relative" style={{ "--episode-hue": episodeHue(id) } as CSSProperties}>
      {/* 原文在整本源文里的集点击后滚到原文中这一集；其他集不在原文里，点击打开这一集 */}
      {info?.placed ? (
        <button
          type="button"
          onClick={() => onLocate(id)}
          aria-current={active ? "true" : undefined}
          className={rowClass}
        >
          {content}
        </button>
      ) : (
        <Link href={`/${WORKSPACE_ROUTE_EPISODES}/${id}`} className={rowClass}>
          {content}
        </Link>
      )}
      <span className="absolute top-1/2 right-1 -translate-y-1/2">
        <EpisodeActionsMenu view={view} episodes={episodes} episode={id} actions={actions} />
      </span>
    </li>
  );
}

/**
 * 一集的「⋯」菜单：打开这一集、在后面新建一集、与下一集合并、清除之后的分集、从这一集开始重新规划、删除这一集。
 * 合并、清除与重新规划只对切出集出现，不可用时置灰。
 */
function EpisodeActionsMenu({
  view,
  episodes,
  episode,
  actions,
}: {
  view: EpisodesView;
  episodes: EpisodeMeta[];
  episode: number;
  actions: EpisodeMenuActions;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const available = cutEpisodeActions(view, episode);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={t("dashboard:episode_menu_label", { name: episodeDisplayName(episodes, episode, t) })}
          />
        }
      >
        <MoreHorizontal aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-52">
        <DropdownMenuItem render={<Link href={`/${WORKSPACE_ROUTE_EPISODES}/${episode}`} />}>
          <ArrowUpRight aria-hidden />
          {t("dashboard:episodes_view_open_episode")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => actions.onCreateAfter(episode)}>
          <Plus aria-hidden />
          {t("dashboard:episodes_outline_create_after")}
        </DropdownMenuItem>
        {available.placed ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              disabled={actions.splitBusy || available.merge !== "ok"}
              onClick={() => actions.onMergeWithNext(episode)}
            >
              <Combine aria-hidden />
              {t("dashboard:manual_split_merge")}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={actions.splitBusy || !available.clearAfter}
              onClick={() => actions.onClearAfter(episode)}
            >
              <ListX aria-hidden />
              {t("dashboard:manual_split_clear_after")}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={actions.planBlocked !== null} onClick={() => actions.onReplan(episode)}>
              <RefreshCw aria-hidden />
              {t("dashboard:replan_start_action")}
            </DropdownMenuItem>
          </>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={() => actions.onDelete(episode)}>
          <Trash2 aria-hidden />
          {t("dashboard:episode_menu_delete")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
