import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { cn } from "cn";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TruncatedTextProps {
  text: string;
  className?: string;
  /**
   * 放在按钮等可聚焦元素里时传 false：截断时不再单独进入 Tab 顺序（嵌套的可聚焦元素违反 WCAG 4.1.2），
   * 悬停仍显示全文，读屏从外层元素的可访问名称读到全文。
   */
  focusable?: boolean;
  /**
   * 触屏（`hover: none`）上点按显示全文：没有悬停，提示气泡不会自己出来，改成点一下在下方浮出全文。
   * 桌面档仍是悬停提示，行为不变。
   */
  revealOnTap?: boolean;
}

// 没有悬停能力的设备（手机、平板）：悬停提示永远不会触发，需要点按才能看到全文。
const NO_HOVER_QUERY = "(hover: none)";

function subscribeNoHover(onChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(NO_HOVER_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isNoHover(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(NO_HOVER_QUERY).matches
  );
}

function useNoHover(): boolean {
  return useSyncExternalStore(subscribeNoHover, isNoHover, () => false);
}

/**
 * 单行截断的文字：被截断时可经键盘聚焦，悬停或聚焦都显示全文；没有截断时不进入 Tab 顺序、不弹提示。
 * 用于名称、路径、模型 ID 这类不能折行的短文本；放进 flex 或表格单元格时，父级需允许它收缩（min-w-0 / max-w-0）。
 */
export function TruncatedText({ text, className, focusable = true, revealOnTap = false }: TruncatedTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);
  const noHover = useNoHover();

  // 容器宽度变化（面板拖宽、窗口缩放）都会改变是否截断，只能在浏览器里比较内容宽度与可见宽度。
  // `truncated` 也在依赖里：截断与否会换掉渲染的元素（纯文字 ↔ 可点开的浮层触发元素），
  // 换元素后要重新观察新元素，否则测宽仍盯着已卸载的旧节点。
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setTruncated(el.scrollWidth - el.clientWidth > 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text, truncated]);

  const textClassName = cn("block min-w-0 truncate", className);

  // 触屏上点按显示全文：没有悬停，Tooltip 不会弹出，改用点按可开的浮层。
  // 没截断时只渲染纯文字，不给标题加按钮角色。
  if (revealOnTap && noHover) {
    if (!truncated) return <span ref={ref} className={textClassName}>{text}</span>;
    return (
      <Popover>
        <PopoverTrigger nativeButton={false} render={<span ref={ref} className={textClassName} />}>
          {text}
        </PopoverTrigger>
        <PopoverContent side="bottom" align="start" sideOffset={6} className="w-fit max-w-72">
          {text}
        </PopoverContent>
      </Popover>
    );
  }

  return (
    <Tooltip>
      <TooltipTrigger
        disabled={!truncated}
        render={
          <span
            ref={ref}
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 截断时须能用键盘聚焦以显示全文；它没有可执行的动作，不渲染为 button
            tabIndex={truncated && focusable ? 0 : undefined}
            className={textClassName}
          />
        }
      >
        {text}
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}
