import { useEffect, useId, useMemo, useRef, useState, useCallback } from "react";
import { ChevronDown, Type, Image as ImageIcon, Film } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Popover } from "@/components/ui/Popover";
import type { EndpointKey, MediaType } from "@/types";
import { ENDPOINT_PATHS } from "./customProviderHelpers";

// ---------------------------------------------------------------------------
// EndpointSelect — 自定义供应商「调用端点」选择器
// ---------------------------------------------------------------------------
// 设计目标：
//  * trigger 紧凑（适配 model row），右侧用 mono 字体显示路径前缀作为提示。
//  * 弹层双行展示「显示名 + POST /path」让用户立刻识别具体接口。
//  * 类型分组用纯排版徽章，无 emoji；保持 dark theme 一致。

interface EndpointOption {
  value: EndpointKey;
  labelKey: string;
  mediaType: MediaType;
}

const OPTIONS: EndpointOption[] = [
  { value: "openai-chat", labelKey: "endpoint_openai_chat_display", mediaType: "text" },
  { value: "gemini-generate", labelKey: "endpoint_gemini_generate_display", mediaType: "text" },
  { value: "openai-images", labelKey: "endpoint_openai_images_display", mediaType: "image" },
  { value: "gemini-image", labelKey: "endpoint_gemini_image_display", mediaType: "image" },
  { value: "openai-video", labelKey: "endpoint_openai_video_display", mediaType: "video" },
  { value: "newapi-video", labelKey: "endpoint_newapi_video_display", mediaType: "video" },
];

const MEDIA_META: Record<MediaType, { Icon: typeof Type; labelKey: string }> = {
  text: { Icon: Type, labelKey: "endpoint_text_group" },
  image: { Icon: ImageIcon, labelKey: "endpoint_image_group" },
  video: { Icon: Film, labelKey: "endpoint_video_group" },
};

const MEDIA_ORDER: MediaType[] = ["text", "image", "video"];

/** 把 EndpointKey 解析为 OPTIONS 索引；若 value 不在已知 OPTIONS 内（数据
 *  漂移：后端推未知 endpoint 字符串），回退到 0，避免 `OPTIONS[-1]` 在键盘
 *  选中（Enter/空格）时抛 TypeError 中断弹层交互。 */
function indexOfValue(val: EndpointKey): number {
  const idx = OPTIONS.findIndex((o) => o.value === val);
  return idx >= 0 ? idx : 0;
}

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

  // 弹层打开时把焦点转到 listbox 接收键盘事件。用 useEffect 而非 inline
  // ref callback —— 后者会因每次 render 重新创建 callback 闭包而被 React
  // 反复调用 (null, el)，引发不必要的重复 focus 与 scroll-into-view 抖动。
  useEffect(() => {
    if (open) listboxRef.current?.focus();
  }, [open]);

  const grouped = useMemo(() => {
    return MEDIA_ORDER.map((m) => ({
      mediaType: m,
      options: OPTIONS.filter((o) => o.mediaType === m),
    }));
  }, []);

  const selected = OPTIONS.find((o) => o.value === value) ?? OPTIONS[0];
  const selectedPath = ENDPOINT_PATHS[selected.value];
  // trigger 中的简短路径提示：剥去前导 `/v1`、`/v1beta/models/`，更省宽。
  const triggerHint = selectedPath.path
    .replace(/^\/v1beta\/models\//, "/")
    .replace(/^\/v1/, "");

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
  const [activeIndex, setActiveIndex] = useState<number>(() => indexOfValue(value));

  const openMenu = () => {
    setActiveIndex(indexOfValue(value));
    setOpen(true);
  };

  const onTriggerClick = () => {
    if (open) setOpen(false);
    else openMenu();
  };

  const onTriggerKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openMenu();
    }
  };

  const onListKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % OPTIONS.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + OPTIONS.length) % OPTIONS.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(OPTIONS.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleSelect(OPTIONS[activeIndex].value);
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
        onClick={onTriggerClick}
        onKeyDown={onTriggerKeyDown}
        className={[
          "group inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left text-sm transition-colors",
          "border-gray-700 bg-gray-900 text-gray-100",
          "hover:border-gray-600",
          "focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500",
          "disabled:cursor-not-allowed disabled:opacity-50",
        ].join(" ")}
      >
        <span className="truncate">{t(selected.labelKey)}</span>
        <span
          aria-hidden="true"
          className="hidden font-mono text-[11px] tracking-tight text-emerald-400/70 sm:inline"
        >
          {triggerHint}
        </span>
        <ChevronDown
          aria-hidden="true"
          className={`h-3.5 w-3.5 shrink-0 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`}
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
        className="overflow-hidden rounded-xl border border-gray-800 shadow-2xl shadow-black/40"
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
            const meta = MEDIA_META[group.mediaType];
            const Icon = meta.Icon;
            return (
              <div key={group.mediaType}>
                {gIdx > 0 && <div className="mx-3 my-1 h-px bg-gray-800/80" />}
                <div className="flex items-center gap-1.5 px-3 pb-1 pt-2">
                  <Icon
                    aria-hidden="true"
                    className="h-3 w-3 text-gray-500"
                    strokeWidth={1.75}
                  />
                  <span className="text-[10px] font-medium uppercase tracking-[0.18em] text-gray-500">
                    {t(meta.labelKey)}
                  </span>
                </div>
                <ul className="px-1.5">
                  {group.options.map((opt) => {
                    const path = ENDPOINT_PATHS[opt.value];
                    const isSelected = opt.value === value;
                    const flatIdx = OPTIONS.findIndex((o) => o.value === opt.value);
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
                              ? "bg-indigo-500/[0.07] before:bg-indigo-400"
                              : "before:bg-transparent",
                            isActive && !isSelected ? "bg-gray-800/40" : "",
                          ].join(" ")}
                        >
                          <div
                            className={`truncate text-sm ${isSelected ? "text-gray-50" : "text-gray-200"}`}
                          >
                            {t(opt.labelKey)}
                          </div>
                          <div className="mt-0.5 flex items-baseline gap-1.5 font-mono text-[11px] leading-none">
                            <span className="text-gray-500">{path.method}</span>
                            <span className="truncate text-emerald-400/80">
                              {path.path}
                            </span>
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
