import { useTranslation } from "react-i18next";
import { LayoutGrid } from "lucide-react";
import { STATUS_CONF, resolveUnitStatus } from "./unit-status";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";

export interface UnitRailProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  onExpand: () => void;
  dirtyMap?: Record<string, boolean>;
  statusMap?: Record<string, UnitStatus>;
}

/**
 * Collapsed icon-only rail used when the canvas container width is below the
 * threshold for the full UnitList. Top button expands the list into a flyout
 * drawer (parent state), each row shows the unit short id + status + dirty dot.
 */
export function UnitRail({ units, selectedId, onSelect, onExpand, dirtyMap, statusMap }: UnitRailProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex h-full min-h-0 w-[56px] flex-col overflow-hidden border-r border-border bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
      <button
        type="button"
        onClick={onExpand}
        title={t("reference_unit_rail_expand")}
        aria-label={t("reference_unit_rail_expand")}
        className="focus-ring mx-auto mb-1.5 mt-2.5 grid h-[30px] w-[34px] place-items-center rounded-sm border border-border/50 bg-[oklch(0.22_0.011_265_/_0.5)] text-muted-foreground hover:text-foreground"
      >
        <LayoutGrid className="h-4 w-4" aria-hidden="true" />
      </button>
      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-1.5 pb-2.5">
        {units.map((u) => {
          const sel = u.unit_id === selectedId;
          const dirty = !!dirtyMap?.[u.unit_id];
          const status = resolveUnitStatus(u, statusMap);
          const conf = STATUS_CONF[status];
          const shortId = itemIdWithinEpisode(u.unit_id);
          return (
            <button
              key={u.unit_id}
              type="button"
              onClick={() => onSelect(u.unit_id)}
              title={`${shortId} · ${t(conf.i18nKey)}`}
              className={`focus-ring relative flex w-full flex-col items-center gap-1 rounded-md py-2 ${
                sel
                  ? "border border-primary/22 bg-[linear-gradient(180deg,oklch(0.26_0.018_290_/_0.5),oklch(0.22_0.015_280_/_0.35))]"
                  : "border border-transparent hover:bg-[oklch(0.22_0.011_265_/_0.4)]"
              }`}
            >
              {sel && (
                <span
                  aria-hidden="true"
                  className="absolute -left-px top-1.5 bottom-1.5 w-0.5 rounded-sm bg-primary shadow-[0_0_8px_color-mix(in_oklab,var(--primary)_35%,transparent)]"
                />
              )}
              <span
                translate="no"
                className={`rounded-sm px-1.5 py-0.5 font-mono text-[10.5px] font-bold ${
                  sel
                    ? "text-[oklch(0.14_0_0)] bg-primary"
                    : "bg-[oklch(0.22_0.011_265_/_0.6)] text-muted-foreground"
                }`}
              >
                {shortId}
              </span>
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 rounded-full ${conf.dotClass} ${conf.pulse ? "motion-safe:animate-pulse" : ""}`}
              />
              {dirty && (
                <span
                  aria-label={t("reference_unit_dirty_hint")}
                  className="h-[5px] w-[5px] rounded-full bg-amber-400 shadow-[0_0_6px_rgb(251_191_36)]"
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
