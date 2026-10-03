import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Search } from "lucide-react";
import { StatusBadge, resolveUnitStatus } from "./unit-status";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { dropAnchor } from "@/utils/move-anchor";

export interface UnitListProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  onAdd: () => void;
  /** Per-unit dirty flag. Renders an amber dot in the row header. */
  dirtyMap?: Record<string, boolean>;
  /** Optional per-unit derived status for color/label. Falls back to
   *  `video_clip ? 'ready' : 'pending'` based on persisted assets. */
  statusMap?: Record<string, UnitStatus>;
  /** 拖拽改序：把单元移到 afterId 之后，null 移到最前；缺省时列表不可拖拽。 */
  onMove?: (unitId: string, afterId: string | null) => void;
}

export function UnitList({ units, selectedId, onSelect, onAdd, dirtyMap, statusMap, onMove }: UnitListProps) {
  const { t } = useTranslation("dashboard");
  const [query, setQuery] = useState("");
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);

  const endDrag = () => {
    setDragId(null);
    setDropId(null);
  };
  const commitDrop = (targetId: string) => {
    const sourceId = dragId;
    endDrag();
    if (!onMove || sourceId === null) return;
    const ids = units.map((u) => u.unit_id);
    const afterId = dropAnchor(ids, ids.indexOf(sourceId), ids.indexOf(targetId));
    if (afterId !== undefined) onMove(sourceId, afterId);
  };
  const unitIndex = (unitId: string | null) => (unitId === null ? -1 : units.findIndex((u) => u.unit_id === unitId));

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return units;
    return units.filter(
      (u) => u.unit_id.toLowerCase().includes(q) || u.text.toLowerCase().includes(q),
    );
  }, [units, query]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden border-r border-border bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
          {t("reference_unit_list_title")}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {units.length}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onAdd}
          className="focus-ring inline-flex items-center gap-1 rounded-sm border border-border/50 bg-[oklch(0.24_0.012_265_/_0.5)] px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <Plus className="h-3 w-3" aria-hidden="true" />
          {t("reference_unit_new")}
        </button>
      </div>

      <div className="px-3 pb-2">
        <label className="flex items-center gap-1.5 rounded-md border border-border/50 bg-[oklch(0.20_0.011_265_/_0.55)] px-2 py-1.5">
          <Search className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
          <input
            type="search"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("reference_unit_search_placeholder")}
            aria-label={t("reference_unit_search_placeholder")}
            className="w-full bg-transparent text-[11.5px] text-subtle-foreground placeholder:text-muted-foreground focus:outline-none"
          />
        </label>
      </div>

      {units.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
          {t("reference_canvas_empty")}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-2 py-6 text-center text-[11.5px] text-muted-foreground">
          {t("reference_unit_search_empty")}
        </div>
      ) : (
        <ul
          role="listbox"
          aria-label={t("reference_unit_list_title")}
          className="min-h-0 flex-1 overflow-y-auto px-2 pb-2"
        >
          {filtered.map((u) => {
            const status = resolveUnitStatus(u, statusMap);
            const selected = u.unit_id === selectedId;
            const dirty = !!dirtyMap?.[u.unit_id];
            const dropEdge =
              dropId === u.unit_id && dragId !== null && dragId !== u.unit_id
                ? unitIndex(dragId) < unitIndex(u.unit_id)
                  ? "bottom"
                  : "top"
                : null;
            return (
              <li
                key={u.unit_id}
                data-testid={`unit-row-${u.unit_id}`}
                role="option"
                aria-selected={selected}
                tabIndex={0}
                draggable={Boolean(onMove)}
                title={onMove ? t("shot_drag_hint") : undefined}
                onDragStart={(event) => {
                  event.dataTransfer.effectAllowed = "move";
                  setDragId(u.unit_id);
                }}
                onDragOver={(event) => {
                  if (dragId === null) return;
                  event.preventDefault();
                  setDropId(u.unit_id);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  commitDrop(u.unit_id);
                }}
                onDragEnd={endDrag}
                onClick={() => onSelect(u.unit_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(u.unit_id);
                  }
                }}
                className={`focus-ring relative mb-1 cursor-pointer rounded-lg p-2.5 text-sm transition-colors ${
                  dragId === u.unit_id ? "opacity-40" : ""
                } ${
                  selected
                    ? "border border-primary/22 bg-[linear-gradient(180deg,oklch(0.26_0.018_290_/_0.5),oklch(0.22_0.015_280_/_0.35))]"
                    : "border border-transparent hover:bg-[oklch(0.22_0.011_265_/_0.4)]"
                }`}
              >
                {dropEdge && (
                  <span
                    aria-hidden="true"
                    className={`pointer-events-none absolute left-1 right-1 h-0.5 rounded-sm bg-primary ${
                      dropEdge === "top" ? "-top-px" : "-bottom-px"
                    }`}
                  />
                )}
                {selected && (
                  <span
                    aria-hidden="true"
                    className="absolute -left-px top-2 bottom-2 w-0.5 rounded-sm bg-primary shadow-[0_0_8px_color-mix(in_oklab,var(--primary)_35%,transparent)]"
                  />
                )}
                <div className="mb-1.5 flex items-center gap-1.5">
                  <span
                    className={`rounded-sm px-1.5 py-0.5 font-mono text-[11px] font-bold tracking-wider ${
                      selected
                        ? "text-[oklch(0.14_0_0)] bg-primary"
                        : "bg-[oklch(0.22_0.011_265_/_0.6)] text-muted-foreground"
                    }`}
                    translate="no"
                  >
                    {itemIdWithinEpisode(u.unit_id)}
                  </span>
                  <StatusBadge status={status} />
                  <span className="flex-1" />
                  {dirty && (
                    <span
                      title={t("reference_unit_dirty_hint")}
                      aria-label={t("reference_unit_dirty_hint")}
                      className="h-1.5 w-1.5 rounded-full bg-amber-400 shadow-[0_0_6px_rgb(251_191_36)]"
                    />
                  )}
                  <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                    {u.duration_seconds}s
                  </span>
                </div>
                <p
                  className={`m-0 line-clamp-2 text-[11.5px] leading-snug ${
                    selected ? "text-subtle-foreground" : "text-muted-foreground"
                  }`}
                >
                  {u.text}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
