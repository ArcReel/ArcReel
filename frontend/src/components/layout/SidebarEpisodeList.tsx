import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, MoreHorizontal, Plus, Trash2 } from "lucide-react";

import { SortableHandle, SortableItem, SortableList, type SortableMove } from "@/components/shared/sortable/SortableList";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { EpisodeMeta } from "@/types";
import type { GenerationRoute } from "@/utils/generation-mode";
import { episodeMoveCheck } from "@/utils/episode-order";
import { stepAnchor } from "@/utils/move-anchor";

import { EpisodeCard } from "./EpisodeCard";

interface SidebarEpisodeListProps {
  /** 完整账本（播出顺序）。 */
  episodes: EpisodeMeta[];
  /** 搜索过滤后要显示的集与它们的播出位置。 */
  shown: { ep: EpisodeMeta; position: number }[];
  wholeSourceFiles: readonly { source_file: string }[];
  activeEp: number | null;
  route: GenerationRoute;
  /** 搜索过滤时只显示部分集，不能调整顺序。 */
  reorderable: boolean;
  onOpen: (episode: number) => void;
  onCreateAfter: (episode: number) => void;
  /** 把一集移到 `after` 之后（null 为最前）；提交与刷新完成后 resolve，失败时由调用方提示。 */
  onMove: (episode: number, after: number | null) => Promise<void>;
  onDelete: (episode: number) => void;
}

/**
 * 侧栏的集列表：拖动把手，或聚焦把手后用空格与方向键调整播出顺序；菜单里有前移、后移、
 * 在这一集之后新建和删除这一集。切出集之间按源文位置排列，违背这一顺序的落点不接受拖放。
 */
export function SidebarEpisodeList({
  episodes,
  shown,
  wholeSourceFiles,
  activeEp,
  route,
  reorderable,
  onOpen,
  onCreateAfter,
  onMove,
  onDelete,
}: SidebarEpisodeListProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  // 移动提交到刷新完成之间先按新顺序显示，松手后条目不会弹回原处再跳过去。这段时间里不接受新的
  // 移动：校验与提交仍按 store 里的旧顺序，叠加的移动会算错落点。把手不禁用，键盘放下后焦点留在原处。
  const [pendingOrder, setPendingOrder] = useState<number[] | null>(null);
  const moving = pendingOrder !== null;
  const byId = new Map(episodes.map((ep) => [ep.episode, ep]));
  const ids = pendingOrder ?? episodes.map((ep) => ep.episode);
  const rows = reorderable
    ? ids.flatMap((id, index) => {
        const ep = byId.get(id);
        return ep ? [{ ep, position: index + 1 }] : [];
      })
    : shown;
  const nameOf = (ep: EpisodeMeta, position: number) =>
    ep.title?.trim() || t("common:episode_position_name", { position });

  const check = (episode: number, after: number | null) =>
    episodeMoveCheck(episodes, wholeSourceFiles, episode, after);

  const move = (episode: number, after: number | null) => {
    if (moving) return;
    if (check(episode, after) === "ok") {
      const rest = ids.filter((id) => id !== episode);
      const at = after === null ? 0 : rest.indexOf(after) + 1;
      setPendingOrder([...rest.slice(0, at), episode, ...rest.slice(at)]);
    }
    // 不可行的移动同样交给调用方：它会提示切出集为什么不能移到这里
    void onMove(episode, after).finally(() => setPendingOrder(null));
  };

  const afterOf = ({ ids: next, to }: SortableMove<number>) => (to > 0 ? next[to - 1] : null);

  return (
    <SortableList
      ids={ids}
      disabled={!reorderable}
      getName={(id) => {
        const index = ids.indexOf(id);
        const ep = byId.get(id);
        return ep ? nameOf(ep, index + 1) : String(id);
      }}
      canMove={(sortableMove) => !moving && check(sortableMove.id, afterOf(sortableMove)) === "ok"}
      onMove={(sortableMove) => move(sortableMove.id, afterOf(sortableMove))}
    >
      <ul className="flex flex-col gap-0.5">
        {rows.map(({ ep, position }) => {
          const index = ids.indexOf(ep.episode);
          const earlier = stepAnchor(ids, index, "earlier");
          const later = stepAnchor(ids, index, "later");
          const name = nameOf(ep, position);
          return (
            <SortableItem
              key={ep.episode}
              id={ep.episode}
              className="group/episode relative flex items-center rounded-md bg-background data-dragging:shadow-overlay"
            >
              {reorderable && (
                // 把手盖在集序号上，悬停或聚焦时出现
                <span className="absolute top-1/2 left-2.5 -translate-y-1/2 rounded-md bg-muted opacity-0 transition-opacity group-hover/episode:opacity-100 focus-within:opacity-100 group-data-dragging/episode:opacity-100 pointer-coarse:opacity-100">
                  <SortableHandle label={t("dashboard:episode_reorder", { name })} />
                </span>
              )}
              <EpisodeCard
                ep={ep}
                position={position}
                active={ep.episode === activeEp}
                onClick={() => onOpen(ep.episode)}
                route={route}
              />
              <span className="absolute top-1.5 right-1.5 opacity-0 transition-opacity group-hover/episode:opacity-100 focus-within:opacity-100 has-aria-expanded:opacity-100 pointer-coarse:opacity-100">
                <DropdownMenu>
                  <DropdownMenuTrigger
                    render={
                      <Button
                        variant="secondary"
                        size="icon-xs"
                        aria-label={t("dashboard:episode_menu_label", { name })}
                      />
                    }
                  >
                    <MoreHorizontal aria-hidden />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuGroup>
                      <DropdownMenuItem onClick={() => onCreateAfter(ep.episode)}>
                        <Plus aria-hidden />
                        {t("dashboard:episode_menu_create_after")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={!reorderable || moving || earlier === undefined}
                        onClick={() => earlier !== undefined && move(ep.episode, earlier)}
                      >
                        <ArrowUp aria-hidden />
                        {t("dashboard:episode_menu_move_earlier")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={!reorderable || moving || later === undefined}
                        onClick={() => later !== undefined && move(ep.episode, later)}
                      >
                        <ArrowDown aria-hidden />
                        {t("dashboard:episode_menu_move_later")}
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" onClick={() => onDelete(ep.episode)}>
                      <Trash2 aria-hidden />
                      {t("dashboard:episode_menu_delete")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </span>
            </SortableItem>
          );
        })}
      </ul>
    </SortableList>
  );
}
