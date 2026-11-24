import type * as React from "react"
import { useEffect, useRef } from "react"
import { Popover as PopoverPrimitive } from "@base-ui/react/popover"
import { cn } from "cn"
import { Popover, PopoverContent, PopoverTrigger } from "./popover"

type HoverOpenMode = "idle" | "hover" | "pinned"

function InfoTip({ open, onOpenChange, ...props }: PopoverPrimitive.Root.Props) {
  const hoverModeRef = useRef<HoverOpenMode>("idle")

  useEffect(() => {
    if (open === false) {
      hoverModeRef.current = "idle"
    }
  }, [open])

  function handleOpenChange(nextOpen: boolean, details: PopoverPrimitive.Root.ChangeEventDetails) {
    if (!nextOpen && details.reason === "trigger-press" && hoverModeRef.current === "hover") {
      hoverModeRef.current = "pinned"
      details.cancel()
      return
    }

    if (!nextOpen && details.reason === "trigger-hover" && hoverModeRef.current === "pinned") {
      details.cancel()
      return
    }

    onOpenChange?.(nextOpen, details)
    if (!details.isCanceled) {
      hoverModeRef.current = nextOpen && details.reason === "trigger-hover" ? "hover" : "idle"
    }
  }

  return <Popover data-slot="info-tip" open={open} onOpenChange={handleOpenChange} {...props} />
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
