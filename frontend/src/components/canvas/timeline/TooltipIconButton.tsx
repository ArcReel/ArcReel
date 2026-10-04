import { useId, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TooltipIconButtonProps {
  label: string;
  /** 禁用原因：禁用时代替名称显示在提示里，并作为按钮的无障碍描述。 */
  hint?: string;
  disabled?: boolean;
  onClick: () => void;
  size?: "icon-xs" | "icon-sm";
  /** 对应的快捷键（如「J」）：写进 `aria-keyshortcuts`，并显示在提示里。 */
  shortcut?: string;
  children: ReactNode;
}

/**
 * 只有图标的工具按钮，悬停或聚焦显示名称。禁用的按钮不响应指针，提示改挂在外层，
 * 悬停时仍能看到禁用原因。
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
