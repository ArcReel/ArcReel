import { useLayoutEffect, useRef, useState } from "react";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TruncatedTextProps {
  text: string;
  className?: string;
  /**
   * 放在按钮等可聚焦元素里时传 false：截断时不再单独进入 Tab 顺序（嵌套的可聚焦元素违反 WCAG 4.1.2），
   * 悬停仍显示全文，读屏从外层元素的可访问名称读到全文。
   */
  focusable?: boolean;
}

/**
 * 单行截断的文字：被截断时可经键盘聚焦，悬停或聚焦都显示全文；没有截断时不进入 Tab 顺序、不弹提示。
 * 用于名称、路径、模型 ID 这类不能折行的短文本；放进 flex 或表格单元格时，父级需允许它收缩（min-w-0 / max-w-0）。
 */
export function TruncatedText({ text, className, focusable = true }: TruncatedTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);

  // 容器宽度变化（面板拖宽、窗口缩放）都会改变是否截断，只能在浏览器里比较内容宽度与可见宽度。
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setTruncated(el.scrollWidth - el.clientWidth > 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text]);

  return (
    <Tooltip>
      <TooltipTrigger
        disabled={!truncated}
        render={
          <span
            ref={ref}
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 截断时须能用键盘聚焦以显示全文；它没有可执行的动作，不渲染为 button
            tabIndex={truncated && focusable ? 0 : undefined}
            className={cn("block min-w-0 truncate", className)}
          />
        }
      >
        {text}
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}
