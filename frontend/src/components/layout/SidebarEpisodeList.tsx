import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, MoreHorizontal, Plus, Trash2 } from "lucide-react";

import { ActionMenu } from "@/components/legacy/ActionMenu";
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
  /** 搜索过滤时只显示部分集，不能拖拽调序。 */
  reorderable: boolean;
  onOpen: (episode: number) => void;
  onCreateAfter: (episode: number) => void;
  onMove: (episode: number, after: number | null) => void;
  onDelete: (episode: number) => void;
}

/**
 * 侧栏的集列表：拖拽或菜单里的前移、后移调整播出顺序，菜单里还有在这一集之后新建和删除这一集。
 * 切出集之间按源文位置排列，违背这一顺序的落点不接受拖放。
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
  const [dragId, setDragId] = useState<number | null>(null);
  const [drop, setDrop] = useState<{ episode: number; edge: "top" | "bottom" } | null>(null);
  const ids = episodes.map((ep) => ep.episode);

  /** 拖到某一集的上半或下半：落在它之前（即前一集之后）或之后。 */
  const dropAfter = (target: number, edge: "top" | "bottom"): number | null => {
    if (edge === "bottom") return target;
    const index = ids.indexOf(target);
    const before = ids.slice(0, index).filter((id) => id !== dragId);
    return before.length > 0 ? before[before.length - 1] : null;
  };
  const check = (episode: number, after: number | null) =>
    episodeMoveCheck(episodes, wholeSourceFiles, episode, after);

  const endDrag = () => {
    setDragId(null);
    setDrop(null);
  };

  return (
    <>
      {shown.map(({ ep, position }) => {
        const index = ids.indexOf(ep.episode);
        const earlier = stepAnchor(ids, index, "earlier");
        const later = stepAnchor(ids, index, "later");
        const name = ep.title?.trim() || t("common:episode_position_name", { position });
        const edge = drop?.episode === ep.episode && dragId !== ep.episode ? drop.edge : null;
        return (
          <div
            key={ep.episode}
            className={`group relative ${dragId === ep.episode ? "opacity-40" : ""}`}
            draggable={reorderable}
            title={reorderable ? t("dashboard:episode_drag_hint") : undefined}
            onDragStart={(event) => {
              setDragId(ep.episode);
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", String(ep.episode));
            }}
            onDragOver={(event) => {
              if (dragId === null || dragId === ep.episode) return;
              const rect = event.currentTarget.getBoundingClientRect();
              const nextEdge = event.clientY < rect.top + rect.height / 2 ? "top" : "bottom";
              // 违背源文顺序的落点不接受拖放，光标显示为不可放置
              if (check(dragId, dropAfter(ep.episode, nextEdge)) !== "ok") {
                setDrop(null);
                return;
              }
              event.preventDefault();
              setDrop({ episode: ep.episode, edge: nextEdge });
            }}
            onDrop={(event) => {
              event.preventDefault();
              const source = dragId;
              const target = drop;
              endDrag();
              if (source !== null && target !== null) onMove(source, dropAfter(target.episode, target.edge));
            }}
            onDragEnd={endDrag}
          >
            {edge !== null ? (
              <span
                aria-hidden
                className={`pointer-events-none absolute inset-x-1 h-0.5 rounded-sm bg-primary ${edge === "top" ? "-top-px" : "bottom-0.5"}`}
              />
            ) : null}
            <EpisodeCard
              ep={ep}
              position={position}
              active={ep.episode === activeEp}
              onClick={() => onOpen(ep.episode)}
              route={route}
            />
            <div className="absolute right-1.5 top-1.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
              <ActionMenu
                label={t("dashboard:episode_menu_label", { name })}
                triggerClassName="focus-ring grid h-6 w-6 place-items-center rounded-md text-muted-foreground hover:text-foreground"
                triggerStyle={{ background: "oklch(0.22 0.012 265 / 0.9)" }}
                items={[
                  {
                    key: "create-after",
                    label: t("dashboard:episode_menu_create_after"),
                    icon: Plus,
                    onSelect: () => onCreateAfter(ep.episode),
                  },
                  {
                    key: "earlier",
                    label: t("dashboard:episode_menu_move_earlier"),
                    icon: ArrowUp,
                    disabled: earlier === undefined || check(ep.episode, earlier) !== "ok",
                    title: earlier !== undefined && check(ep.episode, earlier) === "locked"
                      ? t("dashboard:episode_move_cut_locked")
                      : undefined,
                    onSelect: () => earlier !== undefined && onMove(ep.episode, earlier),
                  },
                  {
                    key: "later",
                    label: t("dashboard:episode_menu_move_later"),
                    icon: ArrowDown,
                    disabled: later === undefined || check(ep.episode, later) !== "ok",
                    title: later !== undefined && check(ep.episode, later) === "locked"
                      ? t("dashboard:episode_move_cut_locked")
                      : undefined,
                    onSelect: () => later !== undefined && onMove(ep.episode, later),
                  },
                  {
                    key: "delete",
                    label: t("dashboard:episode_menu_delete"),
                    icon: Trash2,
                    danger: true,
                    onSelect: () => onDelete(ep.episode),
                  },
                ]}
              >
                <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
              </ActionMenu>
            </div>
          </div>
        );
      })}
    </>
  );
}
