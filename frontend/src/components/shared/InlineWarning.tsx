import { cn } from "cn";

import { Button } from "@/components/ui/button";

export interface InlineWarningAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** Hover reason when disabled. */
  title?: string;
}

export interface InlineWarningProps {
  /** Warning body. */
  message: string;
  /** One-click correction; omit when actions are unavailable, e.g. read-only contexts. */
  action?: InlineWarningAction;
  /** Outer spacing determined by caller layouts; component owns typography/colors. */
  className?: string;
}

/**
 * Inline warnings: amber text plus optional one-click correction buttons.
 *
 * Explain settings that cause later runtime failures without blocking now: warnings never gate controls,
 * only explain consequences/provide correction paths. `role="alert"` announces immediately when conditions hold, so attach it to
 * conditional outer elements: entire blocks appear conditionally rather than rendering empty shells then filling contents.
 *
 * This component owns correction buttons instead of slots: both callers require identical appearance/disable behavior, which slots could let drift.
 */
export function InlineWarning({ message, action, className }: InlineWarningProps) {
  return (
    <div role="alert" className={cn("flex flex-wrap items-center gap-2 text-xs text-warn", className)}>
      <span>{message}</span>
      {action && (
        <Button variant="outline" size="xs" onClick={action.onClick} disabled={action.disabled} title={action.title}>
          {action.label}
        </Button>
      )}
    </div>
  );
}
