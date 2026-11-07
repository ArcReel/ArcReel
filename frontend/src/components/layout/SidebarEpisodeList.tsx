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
  /** Complete broadcast-order ledgers. */
  episodes: EpisodeMeta[];
  /** Search-filtered episodes/positions. */
  shown: { ep: EpisodeMeta; position: number }[];
  wholeSourceFiles: readonly { source_file: string }[];
  activeEp: number | null;
  route: GenerationRoute;
  /** Partial search results cannot reorder episodes. */
  reorderable: boolean;
  onOpen: (episode: number) => void;
  onCreateAfter: (episode: number) => void;
  /** Move episodes after `after`, null first; resolve after commit/refresh, callers notify failures. */
  onMove: (episode: number, after: number | null) => Promise<void>;
  onDelete: (episode: number) => void;
}

/**
 * Episode rails: drag handles, or focus then Space/arrows to reorder; menus offer forward/back,
 * create-after/delete. Cut episodes retain source order, rejecting incompatible drops.
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
  // Display new order between submission/refetch to avoid snap-back then jumps. Reject further moves
  // while stores still hold old order. Keep handles enabled so keyboard-drop focus stays put.
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
    // Invalid moves still reach callers, explaining cut-episode placement refusals.
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
                // Handles overlay episode positions, appearing on hover/focus.
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
