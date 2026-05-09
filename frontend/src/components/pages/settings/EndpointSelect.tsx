import { useEffect, useId, useMemo, useRef, useState, useCallback } from "react";
import { ChevronDown, Type, Image as ImageIcon, Film } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Popover } from "@/components/ui/Popover";
import type { EndpointKey, ImageCap, MediaType } from "@/types";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";

// ---------------------------------------------------------------------------
// EndpointSelect — 自定义供应商「调用端点」选择器
// ---------------------------------------------------------------------------
// 设计目标：
//  * trigger 紧凑（适配 model row），右侧用 mono 字体显示路径前缀作为提示。
//  * 弹层双行展示「显示名 + POST /path」让用户立刻识别具体接口。
//  * 选项 / 路径 / 媒体类型分组 全部从 endpoint-catalog-store 派生（后端单一真相源）。

interface EndpointOption {
  value: EndpointKey;
  labelKey: string;
  mediaType: MediaType;
  method: string;
  path: string;
  imageCaps: ImageCap[] | null;
}

const MEDIA_META: Record<MediaType, { Icon: typeof Type; labelKey: string }> = {
  text: { Icon: Type, labelKey: "endpoint_text_group" },
  image: { Icon: ImageIcon, labelKey: "endpoint_image_group" },
  video: { Icon: Film, labelKey: "endpoint_video_group" },
};

const MEDIA_ORDER: MediaType[] = ["text", "image", "video"];

interface EndpointSelectProps {
  value: EndpointKey;
  onChange: (next: EndpointKey) => void;
  /** Accessible label, e.g. t("endpoint_label") */
  ariaLabel?: string;
  /** Disable interaction */
  disabled?: boolean;
}

export function EndpointSelect({ value, onChange, ariaLabel, disabled }: EndpointSelectProps) {
  const { t } = useTranslation("dashboard");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listboxRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();
  const [open, setOpen] = useState(false);

  const endpoints = useEndpointCatalogStore((s) => s.endpoints);
  const initialized = useEndpointCatalogStore((s) => s.initialized);
  const loading = useEndpointCatalogStore((s) => s.loading);
  const fetchCatalog = useEndpointCatalogStore((s) => s.fetch);
  const refreshCatalog = useEndpointCatalogStore((s) => s.refresh);

  // catalog 未就绪时 trigger 一次 fetch；store 自身有 short-circuit，重复挂载安全。
  useEffect(() => {
    if (!initialized) void fetchCatalog();
  }, [initialized, fetchCatalog]);

  // 把 catalog 数据投影成 EndpointOption[]，按 MEDIA_ORDER 顺序排列以获得稳定的键盘导航。
  const options = useMemo<EndpointOption[]>(() => {
    const ordered: EndpointOption[] = [];
    for (const media of MEDIA_ORDER) {
      for (const e of endpoints) {
        if (e.media_type === media) {
          ordered.push({
            value: e.key,
            labelKey: e.display_name_key,
            mediaType: e.media_type,
            method: e.request_method,
            path: e.request_path_template,
            imageCaps: e.image_capabilities,
          });
        }
      }
    }
    return ordered;
  }, [endpoints]);

  const grouped = useMemo(() => {
    return MEDIA_ORDER.map((m) => ({
      mediaType: m,
      options: options.filter((o) => o.mediaType === m),
    }));
  }, [options]);

  // catalog 未就绪：trigger 显示 placeholder，点击不展开（避免空 listbox 让键盘焦点卡死）。
  const catalogReady = options.length > 0;

  // 弹层打开时把焦点转到 listbox。
  useEffect(() => {
    if (open) listboxRef.current?.focus();
  }, [open]);

  /** 把 EndpointKey 解析为 options 索引；value 不在 catalog 内时回退到 0，避免
   *  键盘选中（Enter/空格）时取 options[-1] 抛 TypeError 中断弹层交互。 */
  const indexOfValue = useCallback(
    (val: EndpointKey): number => {
      const idx = options.findIndex((o) => o.value === val);
      return idx >= 0 ? idx : 0;
    },
    [options],
  );

  // 不要 fallback 到 options[0]：value 不在 catalog 时（漂移 / 后端临时移除 / catalog 未加载）
  // 必须显式 undefined，下方 triggerLabel 才能走原始 key 分支，否则用户会看到与已存值无关的
  // 第一项 label，UI 严重误导。
  const selected = options.find((o) => o.value === value);
  // trigger 中的简短路径提示：剥去前导 `/v1`、`/v1beta/models/`，更省宽。
  const triggerHint = selected
    ? selected.path.replace(/^\/v1beta\/models\//, "/").replace(/^\/v1/, "")
    : "";
  // 已选 endpoint 不在当前 catalog（数据漂移或后端临时移除）：用原始 key 兜底显示。
  const triggerLabel = selected ? t(selected.labelKey) : value || t("endpoint_catalog_loading");

  const handleSelect = useCallback(
    (next: EndpointKey) => {
      onChange(next);
      setOpen(false);
      // 关闭后把焦点还给 trigger，键盘可访问。
      requestAnimationFrame(() => triggerRef.current?.focus());
    },
    [onChange],
  );

  // 键盘：弹层中支持上下键切换、Enter 选中、Escape 关闭。
  // 初始值是 0，正确值在 openMenu() 中按当前 value 设置；catalog 未就绪时
  // catalogReady 为 false，trigger 是 disabled，listbox 永远打不开，0 不会被读到。
  const [activeIndex, setActiveIndex] = useState<number>(0);

  const openMenu = () => {
    if (!catalogReady) return;
    setActiveIndex(indexOfValue(value));
    setOpen(true);
  };

  const onTriggerClick = () => {
    // catalog 未就绪：作为「重试加载」按钮使用，避免 fetch 失败后下拉永久禁用。
    // store.refresh 内部对 loading 短路，连点不会重复发请求。
    if (!catalogReady) {
      void refreshCatalog();
      return;
    }
    if (open) setOpen(false);
    else openMenu();
  };

  const onTriggerKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!catalogReady) {
        void refreshCatalog();
        return;
      }
      openMenu();
    }
  };

  const onListKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + options.length) % options.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(options.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleSelect(options[activeIndex].value);
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        disabled={disabled}
        aria-busy={loading || undefined}
        onClick={onTriggerClick}
        onKeyDown={onTriggerKeyDown}
        className={[
          "group inline-flex items-center gap-2 rounded-[8px] border px-2.5 py-1.5 text-left text-sm transition-colors",
          "border-hairline bg-bg-grad-a/55 text-text",
          "hover:border-hairline-strong",
          "focus-visible:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          "disabled:cursor-not-allowed disabled:opacity-50",
        ].join(" ")}
      >
        <span className="truncate">{triggerLabel}</span>
        {triggerHint && (
          <span
            aria-hidden="true"
            className="hidden font-mono text-[11px] tracking-tight text-good/80 sm:inline"
          >
            {triggerHint}
          </span>
        )}
        <ChevronDown
          aria-hidden="true"
          className={`h-3.5 w-3.5 shrink-0 text-text-4 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={triggerRef}
        align="start"
        sideOffset={6}
        width="w-[22rem]"
        maxHeight={420}
        className="overflow-hidden rounded-xl border border-hairline shadow-2xl shadow-black/40"
      >
        <div
          ref={listboxRef}
          id={listboxId}
          role="listbox"
          aria-label={ariaLabel}
          tabIndex={-1}
          onKeyDown={onListKeyDown}
          className="max-h-[420px] overflow-y-auto py-1.5 outline-none"
        >
          {grouped.map((group, gIdx) => {
            if (group.options.length === 0) return null;
            const meta = MEDIA_META[group.mediaType];
            const Icon = meta.Icon;
            return (
              <div key={group.mediaType}>
                {gIdx > 0 && <div className="mx-3 my-1 h-px bg-hairline-soft" />}
                <div className="flex items-center gap-1.5 px-3 pb-1 pt-2">
                  <Icon
                    aria-hidden="true"
                    className="h-3 w-3 text-text-4"
                    strokeWidth={1.75}
                  />
                  <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
                    {t(meta.labelKey)}
                  </span>
                </div>
                <ul className="px-1.5">
                  {group.options.map((opt) => {
                    const isSelected = opt.value === value;
                    const flatIdx = options.findIndex((o) => o.value === opt.value);
                    const isActive = flatIdx === activeIndex;
                    return (
                      <li key={opt.value}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={isSelected}
                          onMouseEnter={() => setActiveIndex(flatIdx)}
                          onClick={() => handleSelect(opt.value)}
                          className={[
                            "relative w-full rounded-lg py-2 pl-3.5 pr-3 text-left transition-colors",
                            "before:absolute before:left-0 before:top-2.5 before:bottom-2.5 before:w-[2px] before:rounded-full before:transition-colors",
                            isSelected
                              ? "bg-accent-dim before:bg-accent"
                              : "before:bg-transparent",
                            isActive && !isSelected ? "bg-bg-grad-a/50" : "",
                          ].join(" ")}
                        >
                          <div
                            className={`truncate text-sm ${isSelected ? "text-text" : "text-text-2"}`}
                          >
                            {t(opt.labelKey)}
                          </div>
                          <div className="mt-0.5 flex items-baseline gap-1.5 font-mono text-[11px] leading-none">
                            <span className="text-text-4">{opt.method}</span>
                            <span className="truncate text-good/80">{opt.path}</span>
                            {opt.imageCaps && (
                              <span className="ml-auto shrink-0 font-sans text-[10px] tracking-wide text-warm-bright/80">
                                {opt.imageCaps.length === 2
                                  ? t("image_capability_both")
                                  : opt.imageCaps[0] === "text_to_image"
                                    ? t("image_capability_t2i")
                                    : t("image_capability_i2i")}
                              </span>
                            )}
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      </Popover>
    </>
  );
}
