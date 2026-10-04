import { useTranslation } from "react-i18next";
import { PanelLeftOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { STATUS_CONF, resolveUnitStatus } from "./unit-status";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";

export interface UnitRailProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  /** 展开完整列表（搜索、新增与排序都在完整列表里）。 */
  onExpand: () => void;
  statusMap?: Record<string, UnitStatus>;
  className?: string;
}

/** 工作台较窄时的单元图标栏：每项只有集内编号与状态点，顶部按钮展开完整列表。 */
export function UnitRail({ units, selectedId, onSelect, onExpand, statusMap, className = "" }: UnitRailProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className={`flex min-h-0 flex-col items-center border-r border-border ${className}`}>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={onExpand}
        aria-label={t("reference_unit_rail_expand")}
        className="my-2 shrink-0"
      >
        <PanelLeftOpen aria-hidden />
      </Button>
      <ul
        aria-label={t("reference_unit_list_title")}
        className="relative flex min-h-0 w-full flex-1 flex-col gap-1 overflow-y-auto px-1.5 pb-2.5"
      >
        {units.map((u) => {
          const selected = u.unit_id === selectedId;
          const conf = STATUS_CONF[resolveUnitStatus(u, statusMap)];
          const shortId = itemIdWithinEpisode(u.unit_id);
          return (
            <li key={u.unit_id}>
              <button
                type="button"
                onClick={() => onSelect(u.unit_id)}
                aria-current={selected || undefined}
                aria-label={`${shortId} · ${t(conf.i18nKey)}`}
                className={`focus-ring flex w-full flex-col items-center gap-1 rounded-md border py-2 transition-colors duration-fast ${
                  selected ? "border-primary/30 bg-primary/10" : "border-transparent hover:bg-muted/50"
                }`}
              >
                <span
                  translate="no"
                  className={`rounded-sm px-1 py-0.5 font-mono text-xs font-semibold ${
                    selected ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
                  }`}
                >
                  {shortId}
                </span>
                <span aria-hidden="true" className={`size-1.5 rounded-full ${conf.dotClass}`} />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
