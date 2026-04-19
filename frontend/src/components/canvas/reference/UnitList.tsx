import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import type { ReferenceVideoUnit, UnitPersistedStatus } from "@/types";

export interface UnitListProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  onAdd: () => void;
}

/**
 * Map persisted status to dot color. PR4 only distinguishes pending (gray)
 * vs completed/storyboard_ready (green). PR5 will layer running/failed states
 * driven by the task queue.
 */
const STATUS_DOT: Record<UnitPersistedStatus, string> = {
  pending: "bg-gray-500",
  storyboard_ready: "bg-emerald-500",
  completed: "bg-emerald-500",
};

function promptPreview(unit: ReferenceVideoUnit): string {
  const text = unit.shots.map((s) => s.text).join("\n");
  const lines = text.split("\n").slice(0, 2);
  const joined = lines.join(" · ");
  return joined.length > 120 ? `${joined.slice(0, 117)}…` : joined;
}

export function UnitList({ units, selectedId, onSelect, onAdd }: UnitListProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex h-full flex-col border-r border-gray-800 bg-gray-950/50">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <span className="text-sm font-medium text-gray-200">{t("reference_unit_list_title")}</span>
        <button
          type="button"
          onClick={onAdd}
          className="inline-flex items-center gap-1 rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300 hover:border-indigo-500 hover:text-indigo-300"
        >
          <Plus className="h-3 w-3" />
          {t("reference_unit_new")}
        </button>
      </div>
      {units.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-6 text-sm text-gray-500">
          {t("reference_canvas_empty")}
        </div>
      ) : (
        <ul className="flex-1 overflow-y-auto">
          {units.map((u) => {
            const status = u.generated_assets.status;
            const selected = u.unit_id === selectedId;
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
                className={`cursor-pointer border-b border-gray-900 px-3 py-2 text-sm transition-colors ${
                  selected ? "bg-indigo-500/15 text-indigo-200" : "text-gray-300 hover:bg-gray-900"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    aria-label={status}
                    className={`h-2 w-2 rounded-full ${STATUS_DOT[status]}`}
                  />
                  <span className="font-mono text-xs text-gray-400">{u.unit_id}</span>
                  <span className="ml-auto text-xs text-gray-500 tabular-nums">{u.duration_seconds}s</span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-gray-500">{promptPreview(u)}</p>
                {u.references.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {u.references.map((r, idx) => (
                      <span
                        key={`${r.type}-${r.name}-${idx}`}
                        className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-400"
                      >
                        @{r.name}
                      </span>
                    ))}
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
