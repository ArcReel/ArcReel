import { useTranslation } from "react-i18next";
import { LayoutGrid } from "lucide-react";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";

export interface UnitRailProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  onExpand: () => void;
  dirtyMap?: Record<string, boolean>;
  statusMap?: Record<string, UnitStatus>;
}

const STATUS_DOT: Record<UnitStatus, string> = {
  pending: "bg-[var(--color-text-4)]",
  running: "bg-amber-400 motion-safe:animate-pulse",
  ready: "bg-emerald-400",
  failed: "bg-red-400",
};

const STATUS_LABEL_KEY: Record<UnitStatus, string> = {
  pending: "reference_status_pending",
  running: "reference_status_running",
  ready: "reference_status_ready",
  failed: "reference_status_failed",
};

function deriveStatus(
  unit: ReferenceVideoUnit,
  statusMap?: Record<string, UnitStatus>,
): UnitStatus {
  return statusMap?.[unit.unit_id] ?? (unit.generated_assets.video_clip ? "ready" : "pending");
}

/**
 * Collapsed icon-only rail used when the canvas container width is below the
 * threshold for the full UnitList. Top button expands the list into a flyout
 * drawer (parent state), each row shows the unit short id + status + dirty dot.
 */
export function UnitRail({ units, selectedId, onSelect, onExpand, dirtyMap, statusMap }: UnitRailProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex h-full min-h-0 w-[56px] flex-col overflow-hidden border-r border-[var(--color-hairline)] bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
      <button
        type="button"
        onClick={onExpand}
        title={t("reference_unit_rail_expand")}
        aria-label={t("reference_unit_rail_expand")}
        className="focus-ring mx-auto mb-1.5 mt-2.5 grid h-[30px] w-[34px] place-items-center rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-3)] hover:text-[var(--color-text)]"
      >
        <LayoutGrid className="h-4 w-4" aria-hidden="true" />
      </button>
      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-1.5 pb-2.5">
        {units.map((u) => {
          const sel = u.unit_id === selectedId;
          const dirty = !!dirtyMap?.[u.unit_id];
          const status = deriveStatus(u, statusMap);
          // Strip the leading E{episode} from the unit id so the rail shows just `U{n}`.
          const shortId = u.unit_id.replace(/^E\d+/, "");
          return (
            <button
              key={u.unit_id}
              type="button"
              onClick={() => onSelect(u.unit_id)}
              title={`${u.unit_id} · ${t(STATUS_LABEL_KEY[status])}`}
              className={`focus-ring relative flex w-full flex-col items-center gap-1 rounded-md py-2 ${
                sel
                  ? "border border-[var(--color-accent-soft)] bg-[linear-gradient(180deg,oklch(0.26_0.018_290_/_0.5),oklch(0.22_0.015_280_/_0.35))]"
                  : "border border-transparent hover:bg-[oklch(0.22_0.011_265_/_0.4)]"
              }`}
            >
              {sel && (
                <span
                  aria-hidden="true"
                  className="absolute -left-px top-1.5 bottom-1.5 w-0.5 rounded bg-[var(--color-accent)] shadow-[0_0_8px_var(--color-accent-glow)]"
                />
              )}
              <span
                translate="no"
                className={`rounded px-1.5 py-0.5 font-mono text-[10.5px] font-bold ${
                  sel
                    ? "text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))]"
                    : "bg-[oklch(0.22_0.011_265_/_0.6)] text-[var(--color-text-3)]"
                }`}
              >
                {shortId}
              </span>
              <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[status]}`} />
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
