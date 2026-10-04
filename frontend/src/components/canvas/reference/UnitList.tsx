import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Search } from "lucide-react";
import { SortableHandle, SortableItem, SortableList, type SortableMove } from "@/components/shared/sortable/SortableList";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { StatusBadge, resolveUnitStatus } from "./unit-status";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";

export interface UnitListProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  onAdd: () => void;
  /** 画布推导的单元状态；未提供时按有无成片退化为 ready / pending。 */
  statusMap?: Record<string, UnitStatus>;
  /**
   * 把单元移到 `afterId` 之后，null 移到最前；提交完成后 resolve。缺省时列表不可排序。
   * 提交期间按新顺序显示，不接受新的移动：锚点仍按 store 里的旧顺序换算，叠加的移动会算错落点。
   */
  onMove?: (unitId: string, afterId: string | null) => Promise<void>;
  className?: string;
}

/**
 * 视频单元列表：搜索、新增与排序。拖动把手，或聚焦把手后用空格与方向键调整顺序；
 * 按搜索词筛选时不能排序。
 */
export function UnitList({ units, selectedId, onSelect, onAdd, statusMap, onMove, className = "" }: UnitListProps) {
  const { t } = useTranslation("dashboard");
  const [query, setQuery] = useState("");
  const [pendingOrder, setPendingOrder] = useState<string[] | null>(null);

  const byId = useMemo(() => new Map(units.map((u) => [u.unit_id, u])), [units]);
  const ids = pendingOrder ?? units.map((u) => u.unit_id);
  const filtering = query.trim().length > 0;
  const reorderable = Boolean(onMove) && !filtering && pendingOrder === null;

  const shown = useMemo(() => {
    const ordered = ids.flatMap((id) => byId.get(id) ?? []);
    const q = query.trim().toLowerCase();
    if (!q) return ordered;
    return ordered.filter((u) => u.unit_id.toLowerCase().includes(q) || u.text.toLowerCase().includes(q));
  }, [ids, byId, query]);

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
          getName={(id) => itemIdWithinEpisode(id)}
          onMove={move}
        >
          <ul aria-label={t("reference_unit_list_title")} className="relative min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {shown.map((u) => {
              const selected = u.unit_id === selectedId;
              const shortId = itemIdWithinEpisode(u.unit_id);
              return (
                <SortableItem
                  key={u.unit_id}
                  id={u.unit_id}
                  className="group/unit mb-1 flex items-start gap-0.5 rounded-lg bg-background data-dragging:shadow-overlay"
                >
                  {onMove && (
                    <span className="pt-1.5">
                      <SortableHandle label={t("reference_unit_reorder", { id: shortId })} />
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
                      <span
                        translate="no"
                        className={`rounded-sm px-1.5 py-0.5 font-mono text-xs font-semibold ${
                          selected ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
                        }`}
                      >
                        {shortId}
                      </span>
                      <StatusBadge status={resolveUnitStatus(u, statusMap)} />
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
