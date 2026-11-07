import { useId, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TooltipIconButtonProps {
  label: string;
  /** Disabled reason: replace the name with it in the tooltip when disabled, and use it as the button's accessible description. */
  hint?: string;
  disabled?: boolean;
  onClick: () => void;
  size?: "icon-xs" | "icon-sm";
  /** Associated shortcut, such as J: set `aria-keyshortcuts` and show it in the tooltip. */
  shortcut?: string;
  children: ReactNode;
}

/**
 * Icon-only tool button, showing its name on hover/focus. Disabled buttons do not respond to pointers, so attach the tooltip to the wrapper
 * to keep the disabled reason available on hover.
 */
export function TooltipIconButton({
  label,
  hint,
  disabled = false,
  onClick,
  size = "icon-sm",
  shortcut,
  children,
}: TooltipIconButtonProps) {
  const hintId = useId();
  const describe = disabled && hint !== undefined;
  const buttonProps = {
    variant: "ghost",
    size,
    "aria-label": label,
    "aria-describedby": describe ? hintId : undefined,
    "aria-keyshortcuts": shortcut,
    disabled,
  } as const;
  return (
    <>
      <Tooltip>
        {disabled ? (
          <TooltipTrigger render={<span className="inline-flex" />}>
            <Button {...buttonProps}>{children}</Button>
          </TooltipTrigger>
        ) : (
          <TooltipTrigger render={<Button {...buttonProps} onClick={onClick} />}>{children}</TooltipTrigger>
        )}
        <TooltipContent>
          {describe ? hint : label}
          {shortcut && !describe ? <Kbd>{shortcut}</Kbd> : null}
        </TooltipContent>
      </Tooltip>
      {describe ? (
        <span id={hintId} hidden>
          {hint}
        </span>
      ) : null}
    </>
  );
}
