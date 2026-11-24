import { useId, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TooltipIconButtonProps {
  label: string;
  hint?: string;
  disabled?: boolean;
  onClick: () => void;
  size?: "icon-xs" | "icon-sm";
  shortcut?: string;
  children: ReactNode;
}

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
