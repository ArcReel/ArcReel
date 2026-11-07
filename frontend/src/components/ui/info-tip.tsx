import type * as React from "react"
import { Popover as PopoverPrimitive } from "@base-ui/react/popover"
import { cn } from "cn"
import { Popover, PopoverContent, PopoverTrigger } from "./popover"

// Infotips: triggers exist solely to open explanations; follow Base UI advice using hover-opening Popovers rather than Tooltips,
// making explanations available by touch, Enter/Space and screen readers. Use Tooltips when triggers have another purpose and explanations are supplementary.
function InfoTip({ ...props }: PopoverPrimitive.Root.Props) {
  return <Popover data-slot="info-tip" {...props} />
}

// Render native <button type="button"> by default. Callers supply dotted-underlined/badge appearances through
// render={<button type="button" className="..." />} (following ProjectStatusBar PopoverTrigger precedent),
// merging render className with focus-ring here.
function InfoTipTrigger({
  className,
  ...props
}: Omit<PopoverPrimitive.Trigger.Props, "openOnHover" | "delay" | "closeDelay">) {
  return (
    <PopoverTrigger
      data-slot="info-tip-trigger"
      openOnHover
      // Match TooltipProvider delay: open immediately on hover.
      delay={0}
      className={cn("focus-ring", className)}
      {...props}
    />
  )
}

function InfoTipContent({
  className,
  side = "top",
  title,
  children,
  ...props
}: Omit<React.ComponentProps<typeof PopoverContent>, "title"> & {
  /** Accessible popover names, usually trigger text; hidden visually, announced by screen readers only. */
  title: React.ReactNode
}) {
  return (
    <PopoverContent
      data-slot="info-tip-content"
      side={side}
      className={cn("w-fit max-w-xs px-3 py-1.5 text-xs", className)}
      {...props}
    >
      <PopoverPrimitive.Title className="sr-only">{title}</PopoverPrimitive.Title>
      {/* Attach explanations through popover aria-describedby so screen readers announce them when opening. */}
      <PopoverPrimitive.Description>{children}</PopoverPrimitive.Description>
    </PopoverContent>
  )
}

export { InfoTip, InfoTipContent, InfoTipTrigger }
