import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { assetColor } from "./asset-colors";
import type { AssetKind } from "@/types/reference-video";

export interface MentionCandidate {
  name: string;
  imagePath: string | null;
}

export interface MentionPickerProps {
  open: boolean;
  query: string;
  candidates: Record<AssetKind, MentionCandidate[]>;
  onSelect: (ref: { type: AssetKind; name: string }) => void;
  onClose: () => void;
  /** Optional inline anchor style; when absent, picker renders in-flow below its parent. */
  className?: string;
}

interface FlatItem {
  type: AssetKind;
  name: string;
  imagePath: string | null;
  globalIndex: number;
}

const GROUP_ORDER: AssetKind[] = ["character", "scene", "prop"];

export function MentionPicker({
  open,
  query,
  candidates,
  onSelect,
  onClose,
  className,
}: MentionPickerProps) {
  const { t } = useTranslation("dashboard");
  const [activeIndex, setActiveIndex] = useState(0);
  // Reset highlight to the first option whenever the filter query changes —
  // render-phase state sync (React-recommended alternative to the
  // `react-hooks/set-state-in-effect` pattern).
  const [syncedQuery, setSyncedQuery] = useState(query);
  if (syncedQuery !== query) {
    setSyncedQuery(query);
    setActiveIndex(0);
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const result: Record<AssetKind, MentionCandidate[]> = { character: [], scene: [], prop: [] };
    for (const kind of GROUP_ORDER) {
      const arr = candidates[kind] ?? [];
      result[kind] = q.length === 0 ? arr : arr.filter((c) => c.name.toLowerCase().includes(q));
    }
    return result;
  }, [candidates, query]);

  const flat: FlatItem[] = useMemo(() => {
    const out: FlatItem[] = [];
    let idx = 0;
    for (const kind of GROUP_ORDER) {
      for (const item of filtered[kind]) {
        out.push({ type: kind, name: item.name, imagePath: item.imagePath, globalIndex: idx });
        idx += 1;
      }
    }
    return out;
  }, [filtered]);

  // Map "<kind>:<name>" -> globalIndex for O(1) lookup during render.
  const indexByKey = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of flat) {
      m.set(`${f.type}:${f.name}`, f.globalIndex);
    }
    return m;
  }, [flat]);

  // Eagerly clamp active index so keystrokes during render shrinkage never land
  // on an undefined item (e.g. parent shortens the candidates list on the same
  // render that produced a new activeIndex).
  const clampedActive = Math.min(activeIndex, Math.max(0, flat.length - 1));

  const flatRef = useRef(flat);
  const clampedRef = useRef(clampedActive);

  useLayoutEffect(() => {
    flatRef.current = flat;
    clampedRef.current = clampedActive;
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const current = flatRef.current;
      const active = clampedRef.current;
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex(Math.min(current.length - 1, active + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex(Math.max(0, active - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = current[active];
        if (item) onSelect({ type: item.type, name: item.name });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onSelect, onClose]);

  if (!open) return null;

  const empty = flat.length === 0;

  return (
    <div
      role="listbox"
      aria-label={t("reference_picker_title")}
      className={`z-30 max-h-64 w-64 overflow-y-auto rounded-md border border-gray-800 bg-gray-950 shadow-xl ${className ?? ""}`}
    >
      <div aria-hidden="true" className="sticky top-0 bg-gray-950 px-2 py-1 text-[10px] uppercase tracking-wide text-gray-600">
        {t("reference_picker_title")}
      </div>
      {empty && (
        <div className="px-3 py-4 text-center text-xs text-gray-500">
          {t("reference_picker_empty")}
        </div>
      )}
      {!empty &&
        GROUP_ORDER.map((kind) => {
          const items = filtered[kind];
          if (items.length === 0) return null;
          const palette = assetColor(kind);
          return (
            <div key={kind}>
              <div
                className={`px-2 py-1 text-[10px] font-semibold uppercase ${palette.textClass}`}
              >
                {t(`reference_picker_group_${kind}`)}
              </div>
              {items.map((item) => {
                const globalIndex = indexByKey.get(`${kind}:${item.name}`) ?? -1;
                const active = globalIndex === clampedActive;
                return (
                  <button
                    key={`${kind}:${item.name}`}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onMouseEnter={() => setActiveIndex(globalIndex)}
                    onClick={() => onSelect({ type: kind, name: item.name })}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors ${
                      active ? "bg-indigo-500/15 text-indigo-200" : "text-gray-300 hover:bg-gray-900"
                    }`}
                  >
                    <span className={`h-2 w-2 shrink-0 rounded-full ${palette.bgClass} ${palette.borderClass} border`} />
                    <span className="truncate" title={item.name}>{item.name}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
    </div>
  );
}
