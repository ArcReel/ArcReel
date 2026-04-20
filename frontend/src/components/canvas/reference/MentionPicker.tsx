import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { assetColor } from "./asset-colors";
import type { AssetKind } from "@/types/reference-video";

/** Default DOM id for the listbox; paired with combobox aria-controls in ReferenceVideoCard. */
export const MENTION_PICKER_DEFAULT_ID = "reference-editor-picker";

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
  /** Stable DOM id for the listbox; used by combobox aria-controls. Default: "reference-editor-picker". */
  listboxId?: string;
  /** Called whenever the keyboard-active option changes; receives the option's DOM id (null when empty). */
  onActiveChange?: (optionId: string | null) => void;
  /** Optional trigger element excluded from outside-pointerdown close so the caller's
   * toggle button (open ↔ close) round-trips cleanly without a capture-phase race. */
  anchorRef?: RefObject<HTMLElement | null>;
}

function optionId(kind: AssetKind, name: string): string {
  // 安全化：把 CSS 不友好字符替换，避免选择器查询出错
  const safe = name.replace(/[^A-Za-z0-9_\u4e00-\u9fff-]/g, "_");
  return `reference-option-${kind}-${safe}`;
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
  listboxId,
  onActiveChange,
  anchorRef,
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
  const listboxRef = useRef<HTMLDivElement>(null);
  // 真实鼠标坐标。浏览器可能在列表滚动（键盘方向键选中触发）导致元素移到静止光标下时
  // 补发 mousemove/mouseenter；仅当 (x, y) 相对上一次记录变化才视作用户主动移动。
  const lastPointerXY = useRef<{ x: number; y: number }>({ x: -1, y: -1 });

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

  // Report the keyboard-active option id up to the parent (used by combobox's
  // aria-activedescendant). Re-runs on flat/clampedActive change; when flat is
  // empty (e.g. after close or no matches), flat[0] is undefined → null.
  useEffect(() => {
    if (!onActiveChange) return;
    const current = flat[clampedActive];
    onActiveChange(current ? optionId(current.type, current.name) : null);
  }, [flat, clampedActive, onActiveChange]);

  // Close on outside pointerdown. Capture phase so we run before the option's
  // own `onMouseDown preventDefault` — the listbox root still gets the event
  // through `contains()`, and any event landing outside the listbox tree
  // (including the anchoring textarea) closes the picker.
  //
  // 例外：调用方传入的 anchorRef（如 toggle 按钮）需排除，否则同一按钮点一下会先在
  // capture 阶段 onClose()，再在 click 的 toggle 里读取 queued false 并翻回 true，
  // 结果用户无法用鼠标再按一次按钮关闭 picker。
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const el = listboxRef.current;
      if (!el) return;
      if (!(e.target instanceof Node)) return;
      if (el.contains(e.target)) return;
      if (anchorRef?.current?.contains(e.target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [open, onClose, anchorRef]);

  if (!open) return null;

  const empty = flat.length === 0;

  return (
    <div
      ref={listboxRef}
      id={listboxId ?? MENTION_PICKER_DEFAULT_ID}
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
                    id={optionId(kind, item.name)}
                    type="button"
                    role="option"
                    aria-selected={active}
                    // 仅在鼠标真实移动（坐标相对上次记录变化）时才覆盖 activeIndex，
                    // 避免键盘选中导致列表滚动把元素移到静止光标下方时被浏览器补发的
                    // mouseenter/mousemove 抢走高亮。mousemove 记坐标；mouseenter 对比。
                    onMouseMove={(e) => {
                      lastPointerXY.current = { x: e.clientX, y: e.clientY };
                      if (clampedActive !== globalIndex) setActiveIndex(globalIndex);
                    }}
                    onMouseEnter={(e) => {
                      const last = lastPointerXY.current;
                      if (last.x === e.clientX && last.y === e.clientY) return;
                      lastPointerXY.current = { x: e.clientX, y: e.clientY };
                      if (clampedActive !== globalIndex) setActiveIndex(globalIndex);
                    }}
                    // Suppress focus transfer on mousedown so the textarea keeps
                    // focus long enough for the click to fire on this option —
                    // avoids the "blur closes picker before click" race without
                    // relying on a setTimeout hack in the parent.
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => onSelect({ type: kind, name: item.name })}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors focus-visible:ring-1 focus-visible:ring-indigo-400 focus-visible:outline-none ${
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
