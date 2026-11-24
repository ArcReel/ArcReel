import type * as React from "react"
import { useRef } from "react"
import { Popover as PopoverPrimitive } from "@base-ui/react/popover"
import { cn } from "cn"
import { Popover, PopoverContent, PopoverTrigger } from "./popover"

function InfoTip({ onOpenChange, ...props }: PopoverPrimitive.Root.Props) {
  const awaitingFirstPress = useRef(false)

  function handleOpenChange(open: boolean, details: PopoverPrimitive.Root.ChangeEventDetails) {
    if (!open && details.reason === "trigger-press" && awaitingFirstPress.current) {
      awaitingFirstPress.current = false
      details.cancel()
      return
    }

    onOpenChange?.(open, details)
    if (!details.isCanceled) {
      awaitingFirstPress.current = open && details.reason === "trigger-hover"
    }
  }

  return <Popover data-slot="info-tip" onOpenChange={handleOpenChange} {...props} />
}

function InfoTipTrigger({
  className,
  ...props
}: Omit<PopoverPrimitive.Trigger.Props, "openOnHover" | "delay" | "closeDelay">) {
  return (
    <PopoverTrigger
      data-slot="info-tip-trigger"
      openOnHover
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
      <PopoverPrimitive.Description>{children}</PopoverPrimitive.Description>
    </PopoverContent>
  )
}

export { InfoTip, InfoTipContent, InfoTipTrigger }
