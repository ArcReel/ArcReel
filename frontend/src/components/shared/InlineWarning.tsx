import { cn } from "cn";

import { Button } from "@/components/ui/button";

export interface InlineWarningAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}

export interface InlineWarningProps {
  message: string;
  action?: InlineWarningAction;
  className?: string;
}

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
