import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { assetColor } from "./asset-colors";
import { Popover, PopoverContent } from "@/components/ui/popover";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { formatReferenceName, normalizeAssetName, splitDerivativeReference } from "@/utils/reference-mentions";
import type { AssetKind } from "@/types/reference-video";

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
  projectName?: string;
  className?: string;
  listboxId?: string;
  onActiveChange?: (optionId: string | null) => void;
  anchorElement?: HTMLElement | null;
}

function optionId(kind: AssetKind, name: string): string {
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
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
  const [activeIndex, setActiveIndex] = useState(0);
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [syncedQuery, setSyncedQuery] = useState(query);
  const [syncedTab, setSyncedTab] = useState<TabKey>(activeTab);
  if (syncedQuery !== query || syncedTab !== activeTab) {
    setSyncedQuery(query);
    setSyncedTab(activeTab);
    setActiveIndex(0);
  }

  const filteredByQuery = useMemo(() => {
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

  const indexByKey = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of flat) {
      m.set(`${f.type}:${f.name}`, f.globalIndex);
    }
    return m;
  }, [flat]);

  const clampedActive = Math.min(activeIndex, Math.max(0, flat.length - 1));

  const lastPointerXY = useRef<{ x: number; y: number }>({ x: -1, y: -1 });

  const handleKey = useEffectEvent((e: KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex(Math.min(flat.length - 1, clampedActive + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex(Math.max(0, clampedActive - 1));
    } else if (e.key === "Enter" || (e.key === "Tab" && !e.shiftKey)) {
      e.preventDefault();
      const item = flat[clampedActive];
      if (item) onSelect({ type: item.type, name: item.name });
    }
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => handleKey(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (!onActiveChange) return;
    const current = flat[clampedActive];
    // react-doctor-disable-next-line react-doctor/no-pass-data-to-parent, react-doctor/no-pass-live-state-to-parent, react-doctor/no-prop-callback-in-effect -- 候选列表是自持高亮状态的复合控件，焦点留在编辑器里，只把高亮项 id 通知给编辑器作 aria-activedescendant；高亮随查询、标签与候选变化复位，没有单一事件可承载
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
