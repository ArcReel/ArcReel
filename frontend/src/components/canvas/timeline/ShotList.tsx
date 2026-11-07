import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, SearchIcon } from "lucide-react";
import { cn } from "cn";
import type { NarrationSegment, AdShot } from "@/types";
import { API } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { SortableHandle, SortableItem, SortableList, type SortableMove } from "@/components/shared/sortable/SortableList";
import { useProjectsStore } from "@/stores/projects-store";
import { statusFromAssets } from "@/components/canvas/timeline/shot-status";
import { StatusBadge } from "@/components/canvas/timeline/StatusBadge";
import {
  getScriptItemId,
  type EditorContentMode,
  type ScriptItem,
} from "@/utils/script-shape";
import { ItemPositionBadge } from "@/components/shared/episode-item/ItemPositionBadge";
import { InsertShotButton, type InsertShotHandler } from "./ShotStructureActions";

type Segment = ScriptItem;
type ListContentMode = EditorContentMode;

interface ShotListProps {
  segments: Segment[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  contentMode: ListContentMode;
  projectName: string;
  /** Portrait storyboards use vertical thumbnails, landscape horizontal. */
  aspectRatio?: "9:16" | "16:9";
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** Append a shot; omitted hides the list-header add button. */
  onAppend?: InsertShotHandler;
  /** Add/remove/save requests disable addition. */
  appendDisabled?: boolean;
  /** Reorder after afterId, null to front; omitted makes lists unsortable. */
  onMove?: (itemId: string, afterId: string | null) => void | Promise<void>;
  /** Reorder/add/remove requests disable sorting. */
  moveDisabled?: boolean;
  /** Expanded-list bottom content (shortcut hints), absent when collapsed. */
  footer?: ReactNode;
}

function getImagePromptScene(seg: Segment): string {
  // Validate scene is a string, not just present: types permit ImagePrompt | string, while actual data
  // may be null/undefined from hand-edited JSON/partial generation; returning null would break downstream toLowerCase().
  const ip = seg.image_prompt;
  if (typeof ip === "string") return ip;
  if (ip && typeof ip === "object") {
    const scene = (ip as { scene?: unknown }).scene;
    if (typeof scene === "string") return scene;
  }
  return "";
}

function getSegmentText(seg: Segment, mode: ListContentMode): string {
  if (mode === "narration") return (seg as NarrationSegment).novel_text || "";
  if (mode === "ad") {
    // Ad/short-film spoken copy is primary, preferred in list previews; silent visual shots fall back to descriptions.
    const voiceover = (seg as AdShot).voiceover_text;
    if (typeof voiceover === "string" && voiceover.trim()) return voiceover;
    return getImagePromptScene(seg);
  }
  // Drama uses image_prompt.scene as visual previews, symmetric with narration novel_text.
  return getImagePromptScene(seg);
}

/**
 * Shot list: 220px expanded, 44px position strip collapsed. Expanded search/handle-drag/keyboard sorting;
 * sorting has no clear meaning under search filtering, so disable handles.
 */
export function ShotList({
  segments,
  selectedIndex,
  onSelect,
  contentMode,
  projectName,
  aspectRatio = "9:16",
  collapsed,
  onToggleCollapse,
  onAppend,
  appendDisabled = false,
  onMove,
  moveDisabled = false,
  footer,
}: ShotListProps) {
  const { t } = useTranslation("dashboard");
  const [search, setSearch] = useState("");
  // Show new order optimistically during reorder requests, then return to segments (new on success, original on failure).
  const [pendingOrder, setPendingOrder] = useState<string[] | null>(null);
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
  const scrollRef = useRef<HTMLDivElement>(null);

  const indexById = useMemo(
    () => new Map(segments.map((seg, index) => [getScriptItemId(seg, contentMode), index])),
    [segments, contentMode],
  );
  const orderedIds = useMemo(() => {
    const ids = [...indexById.keys()];
    if (!pendingOrder || pendingOrder.length !== ids.length || !pendingOrder.every((id) => indexById.has(id))) {
      return ids;
    }
    return pendingOrder;
  }, [indexById, pendingOrder]);

  // Display positions follow displayed order: optimistic new numbering, script numbering after settlement.
  const positionById = useMemo(() => new Map(orderedIds.map((id, i) => [id, i + 1])), [orderedIds]);
  const positionLabel = (id: string) => t("common:item_position_shot", { position: positionById.get(id) });

  const visibleIds = useMemo(() => {
    if (!search) return orderedIds;
    const query = search.toLowerCase();
    return orderedIds.filter((id) => {
      const seg = segments[indexById.get(id)!];
      return (
        String(positionById.get(id)) === query.trim() || getSegmentText(seg, contentMode).toLowerCase().includes(query)
      );
    });
  }, [orderedIds, search, segments, indexById, positionById, contentMode]);

  const selectedId = segments[selectedIndex] ? getScriptItemId(segments[selectedIndex], contentMode) : null;

  // Keyboard/detail navigation scrolls selection into view unless already visible.
  useEffect(() => {
    if (!selectedId) return;
    const node = scrollRef.current?.querySelector<HTMLElement>(`[data-shot-id="${CSS.escape(selectedId)}"]`);
    node?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId, collapsed]);

  const sortable = Boolean(onMove);
  const handleSortMove = ({ id, to, ids }: SortableMove<string>) => {
    if (!onMove) return;
    setPendingOrder(ids);
    void Promise.resolve(onMove(id, to === 0 ? null : ids[to - 1])).finally(() => setPendingOrder(null));
  };

  if (collapsed) {
    return (
      <nav
        aria-label={t("shot_list_label")}
        className="flex min-h-0 flex-col items-center gap-2 border-r border-border/50 bg-sidebar/40 py-2"
      >
        <Tooltip>
          <TooltipTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={t("shot_list_expand")} onClick={onToggleCollapse} />}
          >
            <ChevronRight aria-hidden />
          </TooltipTrigger>
          <TooltipContent side="right">{t("shot_list_expand")}</TooltipContent>
        </Tooltip>
        <div ref={scrollRef} className="relative min-h-0 w-full flex-1 overflow-y-auto">
          <ul className="flex flex-col items-center gap-1 pb-1">
            {orderedIds.map((id) => {
              const index = indexById.get(id)!;
              const active = index === selectedIndex;
              return (
                <li key={id} data-shot-id={id}>
                  <button
                    type="button"
                    onClick={() => onSelect(index)}
                    aria-current={active || undefined}
                    aria-label={positionLabel(id)}
                    className={cn(
                      "num focus-ring grid size-7 place-items-center rounded-md text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                      active && "bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground",
                    )}
                  >
                    {positionById.get(id)}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </nav>
    );
  }

  return (
    <nav aria-label={t("shot_list_label")} className="flex min-h-0 min-w-0 flex-col border-r border-border/50 bg-sidebar/40">
      <div className="flex shrink-0 items-center gap-1 px-3 pt-3 pb-2">
        <h2 className="text-xs font-medium text-muted-foreground">{t("shots_section_title")}</h2>
        <span className="num text-xs text-muted-foreground">{visibleIds.length}</span>
        <span className="flex-1" />
        {onAppend && (
          <InsertShotButton
            afterId={null}
            contentMode={contentMode}
            onInsert={onAppend}
            label={t("shot_append")}
            disabled={appendDisabled}
            variant="compact"
          />
        )}
        <Tooltip>
          <TooltipTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={t("shot_list_collapse")} onClick={onToggleCollapse} />}
          >
            <ChevronLeft aria-hidden />
          </TooltipTrigger>
          <TooltipContent>{t("shot_list_collapse")}</TooltipContent>
        </Tooltip>
      </div>

      <div className="shrink-0 px-3 pb-2">
        <InputGroup>
          <InputGroupInput
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("shot_search_placeholder")}
            aria-label={t("shot_search_placeholder")}
          />
          <InputGroupAddon>
            <SearchIcon aria-hidden />
          </InputGroupAddon>
        </InputGroup>
      </div>

      <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <SortableList
          ids={visibleIds}
          onMove={handleSortMove}
          getName={positionLabel}
          disabled={!sortable || moveDisabled || search !== ""}
        >
          <ul className="flex flex-col gap-0.5">
            {visibleIds.map((id) => {
              const index = indexById.get(id)!;
              const seg = segments[index];
              const text = getSegmentText(seg, contentMode);
              const active = index === selectedIndex;
              const sbPath = seg.generated_assets?.storyboard_image;
              const sbUrl = sbPath
                ? API.getFileUrl(projectName, sbPath, fingerprints[sbPath] ?? null, { width: 160 })
                : null;
              const section = contentMode === "ad" ? (seg as AdShot).section : undefined;
              return (
                <SortableItem
                  key={id}
                  id={id}
                  data-shot-id={id}
                  className={cn(
                    "group/shot flex items-center gap-0.5 rounded-lg pr-0.5 transition-colors hover:bg-muted/60 data-dragging:bg-popover data-dragging:shadow-overlay",
                    active && "bg-primary/12 hover:bg-primary/12",
                  )}
                >
                  <button
                    id={`segment-${id}`}
                    type="button"
                    onClick={() => onSelect(index)}
                    aria-current={active || undefined}
                    className="focus-ring flex min-w-0 flex-1 items-center gap-2.5 rounded-lg p-1.5 text-left"
                  >
                    <span
                      className={cn(
                        "relative shrink-0 overflow-hidden rounded-sm bg-muted",
                        aspectRatio === "9:16" ? "h-14 w-10" : "h-9 w-16",
                      )}
                    >
                      {sbUrl ? (
                        <img src={sbUrl} alt="" className="size-full object-cover" loading="lazy" decoding="async" />
                      ) : null}
                      <ItemPositionBadge position={positionById.get(id) ?? null} kind="shot" overlay />
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col gap-1">
                      <span className="flex items-center gap-1.5">
                        <StatusBadge status={statusFromAssets(seg.generated_assets?.status)} />
                      </span>
                      {text ? (
                        <span
                          className={cn(
                            "line-clamp-2 text-xs leading-snug",
                            active ? "text-foreground" : "text-subtle-foreground",
                          )}
                        >
                          {text}
                        </span>
                      ) : null}
                      <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        <span className="num">{t("duration_seconds_value_text", { value: seg.duration_seconds ?? 0 })}</span>
                        {section ? <Badge variant="outline">{section}</Badge> : null}
                        {seg.pending_authoring === true ? (
                          <span className="text-warn">{t("shot_pending_authoring")}</span>
                        ) : null}
                      </span>
                    </span>
                  </button>
                  {sortable ? <SortableHandle label={t("shot_reorder", { id: positionLabel(id) })} /> : null}
                </SortableItem>
              );
            })}
          </ul>
        </SortableList>
      </div>
      {footer}
    </nav>
  );
}
