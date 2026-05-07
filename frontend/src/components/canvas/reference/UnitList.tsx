import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Scissors, Search } from "lucide-react";
import { assetColor } from "./asset-colors";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";

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
}

const STATUS_CONF: Record<
  UnitStatus,
  { i18nKey: string; color: string; bg: string; pulse: boolean }
> = {
  pending: {
    i18nKey: "reference_status_pending",
    color: "text-[var(--color-text-4)]",
    bg: "bg-[oklch(0.30_0.01_250_/_0.4)]",
    pulse: false,
  },
  running: {
    i18nKey: "reference_status_running",
    color: "text-amber-300",
    bg: "bg-amber-500/15",
    pulse: true,
  },
  ready: {
    i18nKey: "reference_status_ready",
    color: "text-emerald-300",
    bg: "bg-emerald-500/15",
    pulse: false,
  },
  failed: {
    i18nKey: "reference_status_failed",
    color: "text-red-300",
    bg: "bg-red-500/15",
    pulse: false,
  },
};

function StatusBadge({ status }: { status: UnitStatus }) {
  const { t } = useTranslation("dashboard");
  const conf = STATUS_CONF[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px] font-medium ${conf.color} ${conf.bg}`}
    >
      <span
        aria-hidden="true"
        className={`h-1 w-1 rounded-full ${conf.color.replace("text-", "bg-")} ${conf.pulse ? "motion-safe:animate-pulse" : ""}`}
      />
      {t(conf.i18nKey)}
    </span>
  );
}

function deriveStatus(
  unit: ReferenceVideoUnit,
  statusMap?: Record<string, UnitStatus>,
): UnitStatus {
  return statusMap?.[unit.unit_id] ?? (unit.generated_assets.video_clip ? "ready" : "pending");
}

function promptPreview(unit: ReferenceVideoUnit): string {
  return unit.shots.map((s) => s.text).join(" · ");
}

export function UnitList({ units, selectedId, onSelect, onAdd, dirtyMap, statusMap }: UnitListProps) {
  const { t } = useTranslation("dashboard");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return units;
    return units.filter(
      (u) =>
        u.unit_id.toLowerCase().includes(q) ||
        u.shots.some((s) => s.text.toLowerCase().includes(q)),
    );
  }, [units, query]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden border-r border-[var(--color-hairline)] bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-4)]">
          {t("reference_unit_list_title")}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-4)]">
          {units.length}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onAdd}
          className="focus-ring inline-flex items-center gap-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.24_0.012_265_/_0.5)] px-2 py-0.5 text-[11px] text-[var(--color-text-3)] hover:text-[var(--color-text)]"
        >
          <Plus className="h-3 w-3" aria-hidden="true" />
          {t("reference_unit_new")}
        </button>
      </div>

      <div className="px-3 pb-2">
        <label className="flex items-center gap-1.5 rounded-md border border-[var(--color-hairline-soft)] bg-[oklch(0.20_0.011_265_/_0.55)] px-2 py-1.5">
          <Search className="h-3 w-3 text-[var(--color-text-4)]" aria-hidden="true" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("reference_unit_search_placeholder")}
            aria-label={t("reference_unit_search_placeholder")}
            className="w-full bg-transparent text-[11.5px] text-[var(--color-text-2)] placeholder:text-[var(--color-text-4)] focus:outline-none"
          />
        </label>
      </div>

      {units.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-6 text-sm text-[var(--color-text-4)]">
          {t("reference_canvas_empty")}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-2 py-6 text-center text-[11.5px] text-[var(--color-text-4)]">
          {t("reference_unit_search_empty")}
        </div>
      ) : (
        <ul
          role="listbox"
          aria-label={t("reference_unit_list_title")}
          className="min-h-0 flex-1 overflow-y-auto px-2 pb-2"
        >
          {filtered.map((u) => {
            const status = deriveStatus(u, statusMap);
            const selected = u.unit_id === selectedId;
            const dirty = !!dirtyMap?.[u.unit_id];
            return (
              <li
                key={u.unit_id}
                data-testid={`unit-row-${u.unit_id}`}
                role="option"
                aria-selected={selected}
                tabIndex={0}
                onClick={() => onSelect(u.unit_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(u.unit_id);
                  }
                }}
                className={`focus-ring relative mb-1 cursor-pointer rounded-lg p-2.5 text-sm transition-colors ${
                  selected
                    ? "border border-[var(--color-accent-soft)] bg-[linear-gradient(180deg,oklch(0.26_0.018_290_/_0.5),oklch(0.22_0.015_280_/_0.35))]"
                    : "border border-transparent hover:bg-[oklch(0.22_0.011_265_/_0.4)]"
                }`}
              >
                {selected && (
                  <span
                    aria-hidden="true"
                    className="absolute -left-px top-2 bottom-2 w-0.5 rounded bg-[var(--color-accent)] shadow-[0_0_8px_var(--color-accent-glow)]"
                  />
                )}
                <div className="mb-1.5 flex items-center gap-1.5">
                  <span
                    className={`rounded px-1.5 py-0.5 font-mono text-[11px] font-bold tracking-wider ${
                      selected
                        ? "text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))]"
                        : "bg-[oklch(0.22_0.011_265_/_0.6)] text-[var(--color-text-3)]"
                    }`}
                    translate="no"
                  >
                    {u.unit_id}
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
                  <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-4)]">
                    {u.duration_seconds}s
                  </span>
                </div>
                <p
                  className={`m-0 line-clamp-2 text-[11.5px] leading-snug ${
                    selected ? "text-[var(--color-text-2)]" : "text-[var(--color-text-3)]"
                  }`}
                >
                  {promptPreview(u)}
                </p>
                {u.references.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-0.5">
                    {u.references.slice(0, 5).map((r, idx) => {
                      const palette = assetColor(r.type);
                      return (
                        <span
                          key={`${r.type}-${r.name}-${idx}`}
                          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${palette.textClass} ${palette.bgClass}`}
                          translate="no"
                        >
                          <span
                            aria-hidden="true"
                            className={`h-[3px] w-[3px] rounded-full ${palette.bgClass}`}
                          />
                          {r.name}
                        </span>
                      );
                    })}
                    {u.references.length > 5 && (
                      <span className="font-mono text-[10px] text-[var(--color-text-4)]">
                        +{u.references.length - 5}
                      </span>
                    )}
                  </div>
                )}
                {u.shots.length > 1 && (
                  <div className="mt-1.5 flex items-center gap-1 text-[10px] text-[var(--color-text-4)]">
                    <Scissors className="h-3 w-3" aria-hidden="true" />
                    <span>{t("reference_unit_shots_count", { count: u.shots.length })}</span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
