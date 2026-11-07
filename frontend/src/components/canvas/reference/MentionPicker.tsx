import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { assetColor } from "./asset-colors";
import { Popover, PopoverContent } from "@/components/ui/popover";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { formatReferenceName, normalizeAssetName, splitDerivativeReference } from "@/utils/reference-mentions";
import type { AssetKind } from "@/types/reference-video";

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
  candidates: Partial<Record<AssetKind, MentionCandidate[]>>;
  onSelect: (ref: { type: AssetKind; name: string }) => void;
  onClose: () => void;
  /** Project used to construct asset thumbnail URLs via API.getFileUrl. */
  projectName?: string;
  /** Optional extra className forwarded to the listbox root. */
  className?: string;
  /** Stable DOM id for the listbox; used by combobox aria-controls. Default: "reference-editor-picker". */
  listboxId?: string;
  /** Called whenever the keyboard-active option changes; receives the option's DOM id (null when empty). */
  onActiveChange?: (optionId: string | null) => void;
  /** Position anchor: editor cursor placeholder. Portal rendering avoids ancestor overflow/stacking-context clipping. */
  anchorElement?: HTMLElement | null;
}

function optionId(kind: AssetKind, name: string): string {
  // Sanitize CSS-unfriendly characters to prevent selector-query errors.
  // CJK range uses `一-鿿` Unicode escapes, literally matching utils/reference-mentions.ts
  // MENTION_RE for easy grep.
  const safe = name.replace(/[^A-Za-z0-9_一-鿿-]/g, "_");
  return `reference-option-${kind}-${safe}`;
}

interface FlatItem {
  type: AssetKind;
  name: string;
  imagePath: string | null;
  globalIndex: number;
}

const GROUP_ORDER: AssetKind[] = ["product", "character", "scene", "prop"];
const TAB_ORDER: TabKey[] = ["all", "product", "character", "scene", "prop"];

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
  // Thumbnail URLs carry asset fingerprints: regenerated contents change URLs, unchanged contents hit long browser caches.
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
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

  // Filter every kind once per query; derive filtered/totalsByKind together, avoiding double filtering per keystroke.
  const filteredByQuery = useMemo(() => {
    // Compare in NFC: candidates retain original names, possibly persisted NFD, while queries come from input methods;
    // encoding may differ, so normalize comparison copies only, preserving original names for display/selection.
    const q = normalizeAssetName(query.trim()).toLowerCase();
    const result: Record<AssetKind, MentionCandidate[]> = { product: [], character: [], scene: [], prop: [] };
    for (const kind of GROUP_ORDER) {
      const arr = candidates[kind] ?? [];
      result[kind] = q.length === 0 ? arr : arr.filter((c) => normalizeAssetName(c.name).toLowerCase().includes(q));
    }
    return result;
  }, [candidates, query]);

  const filtered = useMemo(() => {
    if (activeTab === "all") return filteredByQuery;
    // Single tab retains selected kind and empties others, preserving downstream filtered[kind] contracts.
    return {
      product: activeTab === "product" ? filteredByQuery.product : [],
      character: activeTab === "character" ? filteredByQuery.character : [],
      scene: activeTab === "scene" ? filteredByQuery.scene : [],
      prop: activeTab === "prop" ? filteredByQuery.prop : [],
    } satisfies Record<AssetKind, MentionCandidate[]>;
  }, [filteredByQuery, activeTab]);

  const totalsByKind: Record<AssetKind, number> = useMemo(
    () => ({
      product: filteredByQuery.product.length,
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

  // Actual mouse coordinates: browser list scrolling from keyboard selection may move elements under stationary cursors
  // and emit mousemove/mouseenter. Recognize intentional motion only when recorded (x, y) changes.
  const lastPointerXY = useRef<{ x: number; y: number }>({ x: -1, y: -1 });

  // Keyboard subscription follows open only; read latest list/highlight/onSelect at key time without resubscribing.
  const handleKey = useEffectEvent((e: KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex(Math.min(flat.length - 1, clampedActive + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex(Math.max(0, clampedActive - 1));
    } else if (e.key === "Enter" || (e.key === "Tab" && !e.shiftKey)) {
      // Forward Tab completes like Enter, preventing tab-out. Shift+Tab preserves native reverse
      // focus navigation so accessibility still permits leaving inputs while picker is open.
      e.preventDefault();
      const item = flat[clampedActive];
      if (item) onSelect({ type: item.type, name: item.name });
    }
  });

  // Handle only navigation/completion keys; Popover onOpenChange owns Esc/outside clicks.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => handleKey(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Report the keyboard-active option id up to the parent (used by combobox's
  // aria-activedescendant). Re-runs on flat/clampedActive change; when flat is
  // empty (e.g. after close or no matches), flat[0] is undefined → null.
  useEffect(() => {
    if (!onActiveChange) return;
    const current = flat[clampedActive];
    // react-doctor-disable-next-line react-doctor/no-pass-data-to-parent, react-doctor/no-pass-live-state-to-parent, react-doctor/no-prop-callback-in-effect -- Candidate list owns highlight state while focus stays in editors; report highlight IDs only for aria-activedescendant. Query/tab/candidate changes reset highlights without one shared event
    onActiveChange(current ? optionId(current.type, current.name) : null);
  }, [flat, clampedActive, onActiveChange]);

  const empty = flat.length === 0;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {/* Focus remains in editors; keyboard listeners handle arrows/Enter, aria-activedescendant points to options. */}
      <PopoverContent
        anchor={anchorElement ?? undefined}
        align="start"
        sideOffset={4}
        initialFocus={false}
        finalFocus={false}
        className="w-64"
      >
        <div role="tablist" aria-label={t("reference_picker_title")} className="flex border-b border-border">
          {TAB_ORDER.map((tab) => {
            const count =
              tab === "all"
                ? totalsByKind.product + totalsByKind.character + totalsByKind.scene + totalsByKind.prop
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
                className={`focus-ring flex items-center gap-1 border-b-2 px-1.5 py-1.5 text-xs transition-colors duration-fast ${
                  isActive
                    ? "border-primary font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                <span>{t(`reference_picker_tab_${tab}`)}</span>
                <span className="tabular-nums">{count}</span>
              </button>
            );
          })}
        </div>
        <div
          id={listboxId ?? MENTION_PICKER_DEFAULT_ID}
          role="listbox"
          aria-label={t("reference_picker_title")}
          className={`relative max-h-60 overflow-y-auto ${className ?? ""}`}
        >
          {empty && (
            <p className="px-3 py-4 text-center text-xs text-muted-foreground">{t("reference_picker_empty")}</p>
          )}
          {!empty &&
            GROUP_ORDER.map((kind) => {
              const items = filtered[kind];
              if (items.length === 0) return null;
              const palette = assetColor(kind);
              // activeTab==="all" retains group headings; single-kind tabs omit them to avoid repetition.
              const showGroupHeader = activeTab === "all";
              const groupLabel = t(`reference_picker_group_${kind}`);
              return (
                <div key={kind} role="group" aria-label={groupLabel}>
                  {showGroupHeader && (
                    <div
                      aria-hidden="true"
                      data-testid={`picker-group-${kind}`}
                      className={`px-2 py-1 text-xs font-medium ${palette.textClass}`}
                    >
                      {groupLabel}
                    </div>
                  )}
                  {items.map((item) => {
                    const globalIndex = indexByKey.get(`${kind}:${item.name}`) ?? -1;
                    const active = globalIndex === clampedActive;
                    // imagePath is project-relative, e.g. "characters/foo.png"; API.getFileUrl
                    // converts to fetchable URLs. Missing projectName uses dots, common in tests.
                    const thumbUrl =
                      item.imagePath && projectName
                        ? API.getFileUrl(projectName, item.imagePath, fingerprints[item.imagePath] ?? null, {
                            width: 160,
                          })
                        : null;
                    return (
                      <button
                        key={`${kind}:${item.name}`}
                        id={optionId(kind, item.name)}
                        type="button"
                        role="option"
                        aria-selected={active}
                        tabIndex={-1}
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
                        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors duration-fast ${
                          active ? "bg-primary/15 text-foreground" : "text-subtle-foreground hover:bg-muted"
                        }`}
                      >
                        {thumbUrl ? (
                          <img
                            src={thumbUrl}
                            alt=""
                            aria-hidden="true"
                            loading="lazy"
                            decoding="async"
                            className={`size-7 shrink-0 rounded-sm border object-cover ${palette.borderClass}`}
                          />
                        ) : (
                          <span
                            aria-hidden="true"
                            className={`flex size-7 shrink-0 items-center justify-center rounded-sm border ${palette.bgClass} ${palette.borderClass}`}
                          >
                            <span className={`size-2 rounded-full ${palette.dotClass}`} />
                          </span>
                        )}
                        <span className="truncate">{formatReferenceName(item.name)}</span>
                        {splitDerivativeReference(item.name)[1] && (
                          <span className="shrink-0 rounded-sm bg-primary/15 px-1 py-0.5 text-xs font-medium text-primary">
                            {t("reference_picker_derivative_tag")}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              );
            })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
