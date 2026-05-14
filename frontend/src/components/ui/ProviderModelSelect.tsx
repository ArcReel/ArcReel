import { useState, useRef, useEffect, useCallback, useLayoutEffect, useMemo, useId } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Check, Search } from "lucide-react";
import {
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  size,
  useFloating,
} from "@floating-ui/react";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import { DROPDOWN_PANEL_STYLE } from "@/components/ui/darkroom-tokens";
import { UI_LAYERS } from "@/utils/ui-layers";

interface ProviderModelSelectProps {
  value: string; // "gemini-aistudio/veo-3.1-generate-001"
  options: string[]; // ["gemini-aistudio/veo-3.1-generate-001", ...]
  providerNames: Record<string, string>; // {"gemini-aistudio": "Gemini AI Studio", ...}
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  /** If true, adds a default option that returns empty string */
  allowDefault?: boolean;
  /** Label for the default option */
  defaultLabel?: string;
  defaultHint?: string; // "当前: gemini-aistudio/veo-3.1-generate-001"
  /** When value is empty, show this "provider/model" as the effective fallback in the trigger */
  fallbackValue?: string;
  /** Accessible label for the trigger button */
  "aria-label"?: string;
  /** Enable in-dropdown search input. Defaults to true. */
  searchable?: boolean;
  /** Minimum option count to actually render the search input. Defaults to 6. */
  searchThreshold?: number;
}

interface FlatOption {
  type: "default" | "option";
  fullValue: string;
}

function groupByProvider(options: string[]): Record<string, string[]> {
  const groups: Record<string, string[]> = {};
  for (const opt of options) {
    const slashIdx = opt.indexOf("/");
    if (slashIdx === -1) continue;
    const provider = opt.slice(0, slashIdx);
    const model = opt.slice(slashIdx + 1);
    if (!groups[provider]) groups[provider] = [];
    groups[provider].push(model);
  }
  return groups;
}

export function ProviderModelSelect({
  value,
  options,
  providerNames,
  onChange,
  placeholder,
  className,
  allowDefault,
  defaultLabel,
  defaultHint,
  fallbackValue,
  "aria-label": ariaLabel,
  searchable = true,
  searchThreshold = 6,
}: ProviderModelSelectProps) {
  const { t } = useTranslation("dashboard");
  const resolvedPlaceholder = placeholder ?? t("select_model_placeholder");
  // Per-instance ARIA id prefix — without this, multiple ProviderModelSelect
  // instances on the same page (e.g. ImageModelDualSelect's T2I/I2I dual slots)
  // would all share the same listbox/option ids, breaking aria-controls and
  // aria-activedescendant relationships for screen readers.
  const reactId = useId();
  const listboxId = `provider-model-listbox-${reactId}`;
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<Map<number, HTMLButtonElement>>(new Map());

  // 用 FloatingPortal + useFloating 把面板渲染到 document.body，使其脱离任何
  // overflow: hidden / overflow: auto 祖先（如 ProjectSettingsPage 的 SectionCard
  // 或 SystemConfigPage 的 main 滚动容器）的视觉裁剪。fixed 策略 + autoUpdate
  // 保证窗口滚动/缩放时面板跟随触发按钮。
  const { refs, floatingStyles } = useFloating({
    open,
    onOpenChange: setOpen,
    strategy: "fixed",
    placement: "bottom-start",
    whileElementsMounted: autoUpdate,
    middleware: [
      offset(4),
      flip({ padding: 12 }),
      shift({ padding: 12 }),
      size({
        padding: 8,
        apply({ rects, elements }) {
          // 同步面板宽度到触发按钮，保持视觉等宽（替代原本依赖父级 `w-full`）。
          elements.floating.style.width = `${rects.reference.width}px`;
        },
      }),
    ],
  });

  // 在 layout 阶段绑定 reference，避免首帧把面板钉在视窗左上。open 进入依赖
  // 是为了 close→open 切换时让 floating-ui 重新计算位置（autoUpdate 仅在
  // 元素同时存在时才生效）。
  useLayoutEffect(() => {
    refs.setReference(triggerRef.current);
  }, [open, refs]);

  const showSearch = searchable && options.length >= searchThreshold;

  // Memoize grouped so flatOptions below has a stable reference when options
  // hasn't changed; otherwise every render creates a new `grouped` object,
  // invalidates flatOptions, and resets activeIndex on the effect below,
  // breaking keyboard ArrowUp/ArrowDown navigation.
  const grouped = useMemo(() => groupByProvider(options), [options]);

  // Apply search filter to grouped options. When the search input is hidden
  // (searchable=false or option count below threshold), any stale `query`
  // value must NOT continue filtering the list — otherwise users would see
  // an "invisibly filtered" list with no visible search box to clear.
  const filteredGrouped = useMemo(() => {
    if (!showSearch) return grouped;
    const q = query.trim().toLowerCase();
    if (!q) return grouped;
    const out: Record<string, string[]> = {};
    for (const [providerId, models] of Object.entries(grouped)) {
      const providerLabel = (providerNames[providerId] || providerId).toLowerCase();
      if (providerLabel.includes(q)) {
        out[providerId] = models;
        continue;
      }
      const matched = models.filter((m) => m.toLowerCase().includes(q));
      if (matched.length > 0) out[providerId] = matched;
    }
    return out;
  }, [grouped, query, providerNames, showSearch]);

  const hasQuery = showSearch && query.trim().length > 0;
  const showDefault = !!allowDefault && !hasQuery;

  // Build a flat list of selectable options for keyboard navigation
  const flatOptions = useMemo(() => {
    const list: FlatOption[] = [];
    if (showDefault) {
      list.push({ type: "default", fullValue: "" });
    }
    for (const [providerId, models] of Object.entries(filteredGrouped)) {
      for (const model of models) {
        list.push({
          type: "option",
          fullValue: `${providerId}/${model}`,
        });
      }
    }
    return list;
  }, [showDefault, filteredGrouped]);

  // Close on outside click. 面板 portal 到 body 后已不在 containerRef 子树内，
  // 必须同时检查 floating element，否则点击搜索框 / 选项会被判定为 outside 并立即关闭。
  // 仅在 open=true 时挂载全局监听，避免大量关闭态实例长期占用 mousedown listener。
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const insideTrigger = containerRef.current?.contains(target);
      const floatingEl = refs.floating.current;
      const insidePanel = floatingEl?.contains(target);
      if (!insideTrigger && !insidePanel) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, refs]);

  // Reset active index when opened — point to current value or 0
  useEffect(() => {
    if (open) {
      const idx = flatOptions.findIndex((o) => o.fullValue === value);
      // 动作驱动的重置（open 切换为 true 时），无法用 derived state 表达。
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveIndex(idx >= 0 ? idx : 0);
    }
  }, [open, flatOptions, value]);

  // Auto-focus search input when opening (if visible)
  useEffect(() => {
    if (open && showSearch) {
      const id = requestAnimationFrame(() => inputRef.current?.focus());
      return () => cancelAnimationFrame(id);
    }
  }, [open, showSearch]);

  // Clear stale query whenever the search input is hidden, so a later
  // showSearch flip back to true cannot resurface a forgotten query.
  useEffect(() => {
    // 动作驱动的重置，无法用 derived state 表达。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!showSearch) setQuery("");
  }, [showSearch]);

  // Scroll active item into view
  useEffect(() => {
    if (open) {
      itemRefs.current.get(activeIndex)?.scrollIntoView?.({ block: "nearest" });
    }
  }, [activeIndex, open]);

  const selectOption = useCallback(
    (optValue: string) => {
      onChange(optValue);
      setOpen(false);
      setQuery("");
      triggerRef.current?.focus();
    },
    [onChange],
  );

  const handleListKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          if (flatOptions.length > 0) {
            setActiveIndex((prev) => (prev + 1) % flatOptions.length);
          }
          break;
        case "ArrowUp":
          e.preventDefault();
          if (flatOptions.length > 0) {
            setActiveIndex((prev) => (prev - 1 + flatOptions.length) % flatOptions.length);
          }
          break;
        case "Home":
          e.preventDefault();
          setActiveIndex(0);
          break;
        case "End":
          e.preventDefault();
          setActiveIndex(Math.max(0, flatOptions.length - 1));
          break;
        case "Enter": {
          // Ignore Enter while an IME composition is in progress (e.g. selecting
          // a Chinese/Japanese candidate). Otherwise the candidate confirmation
          // would be hijacked into selecting a model.
          if (e.nativeEvent.isComposing) return;
          e.preventDefault();
          const opt = flatOptions[activeIndex];
          if (opt) selectOption(opt.fullValue);
          break;
        }
        case "Escape":
          e.preventDefault();
          setOpen(false);
          setQuery("");
          triggerRef.current?.focus();
          break;
      }
    },
    [flatOptions, activeIndex, selectOption],
  );

  const handleTriggerKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!open) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen(true);
          return;
        }
        return;
      }
      // When search is hidden, the trigger button retains focus and handles
      // navigation directly. With search visible, focus moves to the input.
      if (e.key === " ") {
        e.preventDefault();
        const opt = flatOptions[activeIndex];
        if (opt) selectOption(opt.fullValue);
        return;
      }
      handleListKeyDown(e);
    },
    [open, flatOptions, activeIndex, selectOption, handleListKeyDown],
  );

  const slashIdx = value ? value.indexOf("/") : -1;
  const currentProvider = slashIdx !== -1 ? value.slice(0, slashIdx) : "";
  const currentModel = slashIdx !== -1 ? value.slice(slashIdx + 1) : "";

  const fbSlashIdx = !value && fallbackValue ? fallbackValue.indexOf("/") : -1;
  const fbProvider = fbSlashIdx !== -1 ? fallbackValue!.slice(0, fbSlashIdx) : "";
  const fbModel = fbSlashIdx !== -1 ? fallbackValue!.slice(fbSlashIdx + 1) : "";
  const showFallback = !value && fbSlashIdx !== -1;

  const displayText = value
    ? `${providerNames[currentProvider] || currentProvider} · ${currentModel}`
    : showFallback
      ? `${t("follow_global_default")} · ${providerNames[fbProvider] || fbProvider} · ${fbModel}`
      : resolvedPlaceholder;

  const activeDescendantId =
    open && flatOptions.length > 0 ? `${listboxId}-option-${activeIndex}` : undefined;

  // Track flat index across grouped rendering
  let flatIdx = showDefault ? 1 : 0;

  return (
    <div ref={containerRef} className={`relative ${className || ""}`}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={listboxId}
        aria-activedescendant={activeDescendantId}
        aria-label={ariaLabel}
        onClick={() => {
          // Closing via the trigger should also clear any active query so the
          // next open starts fresh — matches Escape / outside-click / select.
          if (open) setQuery("");
          setOpen(!open);
        }}
        onKeyDown={handleTriggerKeyDown}
        className="flex w-full items-center justify-between gap-2 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[13px] text-text transition-colors hover:border-hairline-strong hover:bg-bg-grad-a/65 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span className={`truncate ${showFallback ? "text-text-3" : ""}`}>{displayText}</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-text-4 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {/* Dropdown panel — portal 到 body 后由 floating-ui 用 fixed 策略定位，
          脱离所有 overflow 祖先的视觉裁剪。z-layer 取 `modal` 与上层全屏容器同级，
          portal 默认 append 到 body 末尾，DOM order 保证下拉盖在 modal/page 之上。 */}
      {open && (
        <FloatingPortal>
          <div
            // floating-ui 的 setFloating 是 stable callback ref；hooks/refs
            // 规则误认为是读取 ref.current，这里安全。
            // eslint-disable-next-line react-hooks/refs
            ref={refs.setFloating}
            className={`isolate overflow-hidden rounded-[8px] border border-hairline shadow-xl ${UI_LAYERS.modal}`}
            style={{ ...floatingStyles, ...DROPDOWN_PANEL_STYLE }}
          >
            {showSearch && (
            <div className="relative border-b border-hairline-soft p-2">
              <Search className="pointer-events-none absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-4" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActiveIndex(0);
                }}
                onKeyDown={handleListKeyDown}
                placeholder={t("search_model_placeholder")}
                aria-label={t("search_model_aria")}
                aria-controls={listboxId}
                aria-activedescendant={activeDescendantId}
                autoComplete="off"
                spellCheck={false}
                className="w-full rounded-[6px] border border-hairline bg-bg-grad-a/65 py-1.5 pl-8 pr-2 text-[12.5px] text-text placeholder:text-text-4 focus:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              />
            </div>
          )}

          <div
            id={listboxId}
            role="listbox"
            aria-label={t("select_model_aria")}
            className="max-h-60 overflow-y-auto"
          >
            {showDefault && (
              <button
                ref={(el) => {
                  if (el) itemRefs.current.set(0, el);
                  else itemRefs.current.delete(0);
                }}
                id={`${listboxId}-option-0`}
                role="option"
                aria-selected={value === ""}
                type="button"
                onClick={() => selectOption("")}
                onMouseEnter={() => setActiveIndex(0)}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px] transition-colors ${
                  activeIndex === 0 ? "bg-accent-dim text-text" : "text-text-2 hover:bg-bg-grad-a/45"
                }`}
              >
                <span>{defaultLabel ?? t("follow_global_default")}</span>
                {defaultHint && (
                  <span className="ml-auto font-mono text-[10.5px] text-text-4">{defaultHint}</span>
                )}
              </button>
            )}

            {Object.entries(filteredGrouped).map(([providerId, models]) => (
              <div key={providerId} role="presentation">
                {/* Group header */}
                <div
                  role="presentation"
                  className="flex items-center gap-1.5 px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4 bg-bg-grad-a/35"
                >
                  <ProviderIcon providerId={providerId} className="h-3.5 w-3.5" />
                  {providerNames[providerId] || providerId}
                </div>
                {/* Model options */}
                {models.map((model) => {
                  const currentFlatIdx = flatIdx++;
                  const fullValue = `${providerId}/${model}`;
                  const isSelected = fullValue === value;
                  const isActive = currentFlatIdx === activeIndex;
                  return (
                    <button
                      key={fullValue}
                      ref={(el) => {
                        if (el) itemRefs.current.set(currentFlatIdx, el);
                        else itemRefs.current.delete(currentFlatIdx);
                      }}
                      id={`${listboxId}-option-${currentFlatIdx}`}
                      role="option"
                      aria-selected={isSelected}
                      type="button"
                      onClick={() => selectOption(fullValue)}
                      onMouseEnter={() => setActiveIndex(currentFlatIdx)}
                      className={`flex w-full items-center gap-1.5 px-3 py-2 pl-6 text-left text-[12.5px] transition-colors ${
                        isActive
                          ? "bg-accent-dim text-text"
                          : "text-text-2 hover:bg-bg-grad-a/45"
                      }`}
                    >
                      {isSelected ? (
                        <Check className="h-3.5 w-3.5 shrink-0 text-accent-2" />
                      ) : (
                        <span className="h-3.5 w-3.5 shrink-0" />
                      )}
                      <span className="truncate">{model}</span>
                    </button>
                  );
                })}
              </div>
            ))}

            {flatOptions.length === 0 && (
              <div role="status" className="px-3 py-3 text-center text-[12.5px] text-text-3">
                {t("no_models_match")}
              </div>
            )}
          </div>
          </div>
        </FloatingPortal>
      )}
    </div>
  );
}
