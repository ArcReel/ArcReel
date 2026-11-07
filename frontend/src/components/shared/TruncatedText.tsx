import { useLayoutEffect, useRef, useState } from "react";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TruncatedTextProps {
  text: string;
  className?: string;
  /**
   * Pass false inside focusable elements such as buttons: truncated text no longer enters Tab order independently (nested focusables violate WCAG 4.1.2);
   * hover still reveals full text, and screen readers read it from outer accessible names.
   */
  focusable?: boolean;
}

/**
 * Single-line truncated text: truncated items accept keyboard focus and show full text on hover/focus; untruncated items skip Tab order and hints.
 * For unwrappable short names/paths/model IDs; flex/table-cell parents must permit shrinking (min-w-0/max-w-0).
 */
export function TruncatedText({ text, className, focusable = true }: TruncatedTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);

  // Container-width changes (panel resizing/window resizing) alter clipping; only browsers can compare content/visible widths.
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
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Clipped text needs keyboard focus to reveal full contents; it has no executable actions and is not a button
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
