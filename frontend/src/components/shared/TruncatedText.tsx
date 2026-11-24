import { useLayoutEffect, useRef, useState } from "react";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TruncatedTextProps {
  text: string;
  className?: string;
  focusable?: boolean;
}

export function TruncatedText({ text, className, focusable = true }: TruncatedTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);

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
