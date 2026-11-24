import { useId } from "react";
import { Bot } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import type { StepAct } from "./step-list";

interface Props {
  act: StepAct;
  onRun: (act: StepAct) => void;
  asLink?: boolean;
  busy?: boolean;
}

export function StepActButton({ act, onRun, asLink = false, busy = false }: Props) {
  const reasonId = useId();
  const disabled = Boolean(act.disabledReason) || busy;
  const variant =
    act.kind === "danger"
      ? "destructive"
      : asLink
        ? "link"
        : act.kind === "agent"
          ? "default"
          : act.kind === "ai"
            ? "outline"
            : "ghost";
  return (
    <span className={cn("inline-flex", disabled && "cursor-not-allowed opacity-50")}>
      <Button
        variant={variant}
        size={asLink ? "xs" : "sm"}
        aria-disabled={disabled || undefined}
        aria-describedby={act.disabledReason ? reasonId : undefined}
        title={act.disabledReason ?? undefined}
        onClick={() => {
          if (!disabled) onRun(act);
        }}
      >
        {act.kind === "agent" && !asLink && <Bot aria-hidden data-icon="inline-start" />}
        {act.label}
      </Button>
      {act.disabledReason && (
        <span id={reasonId} className="sr-only">
          {act.disabledReason}
        </span>
      )}
    </span>
  );
}
