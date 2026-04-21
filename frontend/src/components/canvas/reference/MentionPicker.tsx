import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  size,
  useFloating,
} from "@floating-ui/react";
import { assetColor } from "./asset-colors";
import { API } from "@/api";
import type { AssetKind } from "@/types/reference-video";

// 为何不复用 `components/ui/Popover` + `hooks/useAnchoredPopover`（仓库约定
// "所有弹出面板必须使用此组件"）：
// 1) ReferenceVideoCard 的 caret-tracking 场景需要 element-as-state 锚点——
//    useAnchoredPopover 基于 ref，`ref.current` 变更不会重新触发定位 effect。
// 2) 本 picker 需要 floating-ui 的 `size` middleware 做 max-height 夹紧，
//    useAnchoredPopover 未暴露该扩展点。
// 将 Popover/useAnchoredPopover 迁移为 floating-ui 壳层属于独立 follow-up PR。

/** Default DOM id for the listbox; paired with combobox aria-controls in ReferenceVideoCard. */
export const MENTION_PICKER_DEFAULT_ID = "reference-editor-picker";

export interface MentionCandidate {
  name: string;
  imagePath: string | null;
}

type TabKey = "all" | AssetKind;

export interface MentionPickerProps {
  open: boolean;
  query: string;
  candidates: Record<AssetKind, MentionCandidate[]>;
  onSelect: (ref: { type: AssetKind; name: string }) => void;
  onClose: () => void;
  /** Project used to construct asset thumbnail URLs via API.getFileUrl. */
  projectName?: string;
  /** Optional inline anchor style; when absent, picker renders in-flow below its parent. */
  className?: string;
  /** Stable DOM id for the listbox; used by combobox aria-controls. Default: "reference-editor-picker". */
  listboxId?: string;
  /** Called whenever the keyboard-active option changes; receives the option's DOM id (null when empty). */
  onActiveChange?: (optionId: string | null) => void;
  /** Element the picker should anchor to. The picker is rendered via FloatingPortal
   * to document.body and uses floating-ui to flip/shift against this element — so
   * ancestor overflow-hidden / stacking contexts cannot clip the popup.
   * Also doubles as the outside-pointerdown exclusion target so a toggle button
   * (open ↔ close) round-trips cleanly without a capture-phase race. */
  anchorElement?: HTMLElement | null;
}

function optionId(kind: AssetKind, name: string): string {
  // 安全化：把 CSS 不友好字符替换，避免选择器查询出错。
  // CJK 范围用 `一-鿿` unicode escape，与 utils/reference-mentions.ts
  // 的 MENTION_RE 保持字面一致，便于 grep。
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
const TAB_ORDER: TabKey[] = ["all", "character", "scene", "prop"];

export function MentionPicker({
  open,
  query,
  candidates,
  onSelect,
  onClose,
  projectName,
  className,
  listboxId,
  onActiveChange,
  anchorElement,
}: MentionPickerProps) {
  const { t } = useTranslation("dashboard");
  const [activeIndex, setActiveIndex] = useState(0);
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  // Reset highlight to the first option whenever the filter query or tab
  // changes — render-phase state sync (React-recommended alternative to the
  // `react-hooks/set-state-in-effect` pattern).
  const [syncedQuery, setSyncedQuery] = useState(query);
  const [syncedTab, setSyncedTab] = useState<TabKey>(activeTab);
  if (syncedQuery !== query || syncedTab !== activeTab) {
    setSyncedQuery(query);
    setSyncedTab(activeTab);
    setActiveIndex(0);
  }

  // 按 query 过滤所有 kind 一遍；filtered/totalsByKind 都派生自此，避免每次 keystroke 双倍 filter。
  const filteredByQuery = useMemo(() => {
    const q = query.trim().toLowerCase();
    const result: Record<AssetKind, MentionCandidate[]> = { character: [], scene: [], prop: [] };
    for (const kind of GROUP_ORDER) {
      const arr = candidates[kind] ?? [];
      result[kind] = q.length === 0 ? arr : arr.filter((c) => c.name.toLowerCase().includes(q));
    }
    return result;
  }, [candidates, query]);

  const filtered = useMemo(() => {
    if (activeTab === "all") return filteredByQuery;
    // 单 tab：保留选中 kind，其余置空数组（下游 filtered[kind] 读取契约不变）。
    return {
      character: activeTab === "character" ? filteredByQuery.character : [],
      scene: activeTab === "scene" ? filteredByQuery.scene : [],
      prop: activeTab === "prop" ? filteredByQuery.prop : [],
    } satisfies Record<AssetKind, MentionCandidate[]>;
  }, [filteredByQuery, activeTab]);

  const totalsByKind: Record<AssetKind, number> = useMemo(
    () => ({
      character: filteredByQuery.character.length,
      scene: filteredByQuery.scene.length,
      prop: filteredByQuery.prop.length,
    }),
    [filteredByQuery],
  );

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
  const listboxRef = useRef<HTMLDivElement | null>(null);
  // 真实鼠标坐标。浏览器可能在列表滚动（键盘方向键选中触发）导致元素移到静止光标下时
  // 补发 mousemove/mouseenter；仅当 (x, y) 相对上一次记录变化才视作用户主动移动。
  const lastPointerXY = useRef<{ x: number; y: number }>({ x: -1, y: -1 });

  // Floating-ui placement: bottom-start below the anchor, offset 4px;
  // `flip` pushes up when bottom overflows the viewport, `shift` keeps the
  // popup fully visible horizontally with 8px viewport padding, `size` caps
  // the floating panel so it never exceeds available vertical space (useful on
  // the small-card layout where the editor occupies most of the viewport).
  const { refs, floatingStyles } = useFloating<HTMLElement>({
    open,
    placement: "bottom-start",
    whileElementsMounted: autoUpdate,
    middleware: [
      offset(4),
      flip({ padding: 8 }),
      shift({ padding: 8 }),
      size({
        padding: 8,
        apply({ availableHeight, elements }) {
          elements.floating.style.maxHeight = `${Math.min(288, Math.max(120, availableHeight))}px`;
        },
      }),
    ],
  });

  useEffect(() => {
    refs.setReference(anchorElement ?? null);
  }, [refs, anchorElement]);

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
      } else if (e.key === "Enter" || (e.key === "Tab" && !e.shiftKey)) {
        // Tab 补全（仅正向）：与 Enter 同义，阻止默认 tab-out。Shift+Tab 保留原生反向
        // 焦点切换行为，避免 a11y 回退（picker 打开时仍能按 Shift+Tab 离开输入框）。
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
  // 例外：anchorElement（如 toggle 按钮）需排除，否则同一按钮点一下会先在
  // capture 阶段 onClose()，再在 click 的 toggle 里读取 queued false 并翻回 true，
  // 结果用户无法用鼠标再按一次按钮关闭 picker。
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const el = listboxRef.current;
      if (!el) return;
      if (!(e.target instanceof Node)) return;
      if (el.contains(e.target)) return;
      if (anchorElement?.contains(e.target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [open, onClose, anchorElement]);

  // 合并 ref：既要维护本地 listboxRef（outside-pointerdown 用 contains 判定），
  // 又要把浮层 DOM 给 floating-ui。每渲染一次 new function 会导致 React 先 detach
  // 再 reattach → floating-ui 重新计算一遍，useCallback 稳定下来就只挂一次。
  const setListboxRef = useCallback(
    (el: HTMLDivElement | null) => {
      listboxRef.current = el;
      refs.setFloating(el);
    },
    [refs],
  );

  if (!open) return null;

  const empty = flat.length === 0;

  return (
    <FloatingPortal>
      <div
        ref={setListboxRef}
        id={listboxId ?? MENTION_PICKER_DEFAULT_ID}
        role="listbox"
        aria-label={t("reference_picker_title")}
        style={floatingStyles}
        className={`z-30 w-64 overflow-hidden rounded-md border border-gray-800 bg-gray-950 shadow-xl ${className ?? ""}`}
      >
        <div
          role="tablist"
          aria-label={t("reference_picker_title")}
          className="sticky top-0 z-10 flex gap-0 border-b border-gray-800 bg-gray-950 px-1"
        >
          {TAB_ORDER.map((tab) => {
            const count =
              tab === "all"
                ? totalsByKind.character + totalsByKind.scene + totalsByKind.prop
                : totalsByKind[tab];
            const isActive = tab === activeTab;
            return (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={isActive}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => setActiveTab(tab)}
                className={`flex items-center gap-1 border-b-2 px-2 py-1.5 text-[11px] transition-colors focus-ring ${
                  isActive
                    ? "border-indigo-500 font-medium text-indigo-300"
                    : "border-transparent text-gray-500 hover:text-gray-300"
                }`}
              >
                <span>{t(`reference_picker_tab_${tab}`)}</span>
                <span className="tabular-nums text-[10px] text-gray-600">{count}</span>
              </button>
            );
          })}
        </div>
        <div className="max-h-60 overflow-y-auto">
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
              // activeTab==="all" 时保留分组小标题；选中单 tab 时把小标题去掉避免视觉重复。
              const showGroupHeader = activeTab === "all";
              return (
                <div key={kind}>
                  {showGroupHeader && (
                    <div
                      data-testid={`picker-group-${kind}`}
                      className={`px-2 py-1 text-[10px] font-semibold uppercase ${palette.textClass}`}
                    >
                      {t(`reference_picker_group_${kind}`)}
                    </div>
                  )}
                  {items.map((item) => {
                    const globalIndex = indexByKey.get(`${kind}:${item.name}`) ?? -1;
                    const active = globalIndex === clampedActive;
                    // imagePath 是 project-relative 文件路径（如 "characters/foo.png"），用 API.getFileUrl
                    // 转为可 fetch 的 URL；无 projectName 时回退圆点（测试环境常见）。
                    const thumbUrl =
                      item.imagePath && projectName
                        ? API.getFileUrl(projectName, item.imagePath)
                        : null;
                    return (
                      <button
                        key={`${kind}:${item.name}`}
                        id={optionId(kind, item.name)}
                        type="button"
                        role="option"
                        aria-selected={active}
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
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => onSelect({ type: kind, name: item.name })}
                        className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors focus-visible:ring-1 focus-visible:ring-indigo-400 focus-visible:outline-none ${
                          active ? "bg-indigo-500/15 text-indigo-200" : "text-gray-300 hover:bg-gray-900"
                        }`}
                      >
                        {thumbUrl ? (
                          <img
                            src={thumbUrl}
                            alt=""
                            aria-hidden="true"
                            loading="lazy"
                            className={`h-7 w-7 shrink-0 rounded object-cover ${palette.borderClass} border`}
                          />
                        ) : (
                          <span
                            aria-hidden="true"
                            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded ${palette.bgClass} ${palette.borderClass} border`}
                          >
                            <span className={`h-2 w-2 rounded-full ${palette.bgClass} ${palette.borderClass} border`} />
                          </span>
                        )}
                        <span className="truncate" title={item.name}>{item.name}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
        </div>
      </div>
    </FloatingPortal>
  );
}
