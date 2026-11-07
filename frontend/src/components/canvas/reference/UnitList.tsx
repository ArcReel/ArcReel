import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Search } from "lucide-react";
import { SortableHandle, SortableItem, SortableList, type SortableMove } from "@/components/shared/sortable/SortableList";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { resolveUnitStatus } from "./unit-status";
import { UnitStatusBadge } from "./UnitStatusBadge";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";
import { ItemPositionBadge } from "@/components/shared/episode-item/ItemPositionBadge";

export interface UnitListProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  onAdd: () => void;
  /** Canvas-derived unit states; without them, fall back to ready / pending by final-video presence. */
  statusMap?: Record<string, UnitStatus>;
  /**
   * Move after `afterId`, or first with null; resolve after submission completes. Without this callback, the list is not sortable.
   * Show optimistic order during submission and reject further moves: anchors still use old store order, so overlapping moves miscalculate destinations.
   */
  onMove?: (unitId: string, afterId: string | null) => Promise<void>;
  className?: string;
}

/**
 * Video-unit list with search, addition and ordering. Drag handles, or focus them and use Space/arrow keys;
 * disable sorting while filtering by search.
 */
export function UnitList({ units, selectedId, onSelect, onAdd, statusMap, onMove, className = "" }: UnitListProps) {
  const { t } = useTranslation("dashboard");
  const [query, setQuery] = useState("");
  const [pendingOrder, setPendingOrder] = useState<string[] | null>(null);

  const byId = useMemo(() => new Map(units.map((u) => [u.unit_id, u])), [units]);
  const ids = pendingOrder ?? units.map((u) => u.unit_id);
  const filtering = query.trim().length > 0;
  const reorderable = Boolean(onMove) && !filtering && pendingOrder === null;

  // Display positions follow display order: number optimistic moves in their new order, matching the script after settlement.
  const positionById = useMemo(() => new Map(ids.map((id, i) => [id, i + 1])), [ids]);
  const positionLabel = (id: string) => t("common:item_position_unit", { position: positionById.get(id) });

  const shown = useMemo(() => {
    const ordered = ids.flatMap((id) => byId.get(id) ?? []);
    const q = query.trim().toLowerCase();
    if (!q) return ordered;
    return ordered.filter((u) => String(positionById.get(u.unit_id)) === q || u.text.toLowerCase().includes(q));
  }, [ids, byId, positionById, query]);

  const move = ({ id, to, ids: next }: SortableMove<string>) => {
    if (!onMove) return;
    setPendingOrder(next);
    void onMove(id, to > 0 ? next[to - 1] : null).finally(() => setPendingOrder(null));
  };

  return (
    <div className={`flex min-h-0 flex-col border-r border-border ${className}`}>
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
        <h2 className="text-xs font-medium text-muted-foreground">{t("reference_unit_list_title")}</h2>
        <span className="font-mono text-xs tabular-nums text-muted-foreground">{units.length}</span>
        <span className="flex-1" />
        <Button variant="ghost" size="xs" onClick={onAdd}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("reference_unit_new")}
        </Button>
      </div>

      <div className="px-3 pb-2">
        <InputGroup>
          <InputGroupAddon>
            <Search aria-hidden />
          </InputGroupAddon>
          <InputGroupInput
            type="search"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("reference_unit_search_placeholder")}
            aria-label={t("reference_unit_search_placeholder")}
          />
        </InputGroup>
      </div>

      {units.length === 0 ? (
        <p className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
          {t("reference_canvas_empty")}
        </p>
      ) : shown.length === 0 ? (
        <p className="flex flex-1 items-center justify-center px-2 py-6 text-center text-xs text-muted-foreground">
          {t("reference_unit_search_empty")}
        </p>
      ) : (
        <SortableList
          ids={ids}
          disabled={!reorderable}
          getName={positionLabel}
          onMove={move}
        >
          <ul aria-label={t("reference_unit_list_title")} className="relative min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {shown.map((u) => {
              const selected = u.unit_id === selectedId;
              return (
                <SortableItem
                  key={u.unit_id}
                  id={u.unit_id}
                  className="group/unit mb-1 flex items-start gap-0.5 rounded-lg bg-background data-dragging:shadow-overlay"
                >
                  {onMove && (
                    <span className="pt-1.5">
                      <SortableHandle label={t("reference_unit_reorder", { id: positionLabel(u.unit_id) })} />
                    </span>
                  )}
                  <button
                    type="button"
                    data-testid={`unit-row-${u.unit_id}`}
                    aria-current={selected || undefined}
                    onClick={() => onSelect(u.unit_id)}
                    className={`focus-ring flex min-w-0 flex-1 flex-col gap-1.5 rounded-lg border p-2.5 text-left transition-colors duration-fast ${
                      selected ? "border-primary/30 bg-primary/10" : "border-transparent hover:bg-muted/50"
                    }`}
                  >
                    <span className="flex items-center gap-1.5">
                      <ItemPositionBadge position={positionById.get(u.unit_id) ?? null} kind="unit" active={selected} />
                      <UnitStatusBadge status={resolveUnitStatus(u, statusMap)} />
                      <span className="flex-1" />
                      <span className="font-mono text-xs tabular-nums text-muted-foreground">
                        {t("reference_editor_unit_meta", { duration: u.duration_seconds })}
                      </span>
                    </span>
                    <span
                      className={`line-clamp-2 text-xs leading-snug ${
                        selected ? "text-subtle-foreground" : "text-muted-foreground"
                      }`}
                    >
                      {u.text}
                    </span>
                  </button>
                </SortableItem>
              );
            })}
          </ul>
        </SortableList>
      )}
    </div>
  );
}
