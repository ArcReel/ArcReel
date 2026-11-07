import { useId } from "react";
import { Bot } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import type { StepAct } from "./step-list";

interface Props {
  act: StepAct;
  onRun: (act: StepAct) => void;
  /** Or alternatives, persistent inline entries and reminders use text links; only primary next-step entries remain buttons. */
  asLink?: boolean;
  busy?: boolean;
}

/**
 * Shared next-step entries for episode panels/header popovers: Hand to Agent solid, direct AI outlined,
 * navigation/manual ghost buttons; `asLink` always uses link styling, destructive actions always danger colors.
 * Inadmissible entries stay visible but unclickable, explaining reasons on hover/to screen readers.
 */
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
    // Disabled entries remain focusable without native disabled; built-in button fading does not apply, so outer wrappers dim them.
    <span className={cn("inline-flex", disabled && "cursor-not-allowed opacity-50")}>
      <Button
        variant={variant}
        size={asLink ? "xs" : "sm"}
        // aria-disabled rather than disabled preserves focus so screen readers can announce disabled reasons.
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
