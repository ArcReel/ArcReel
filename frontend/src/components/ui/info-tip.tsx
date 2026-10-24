import type * as React from "react"
import { Popover as PopoverPrimitive } from "@base-ui/react/popover"
import { cn } from "cn"
import { Popover, PopoverContent, PopoverTrigger } from "./popover"

// 说明性提示（infotip）：触发元素只为打开说明而存在，按 Base UI 的建议用悬停可开的 Popover 而不是 Tooltip，
// 触屏点按、键盘 Enter/Space 与读屏都能取到说明。触发元素另有用途、说明只是附带的，仍用 Tooltip。
function InfoTip({ ...props }: PopoverPrimitive.Root.Props) {
  return <Popover data-slot="info-tip" {...props} />
}

// 默认渲染原生 <button type="button">。虚线下划线、徽标这类外观由调用处经
// render={<button type="button" className="..." />} 给出（先例同 ProjectStatusBar 的 PopoverTrigger），
// render 的 className 与这里的 focus-ring 拼接。
function InfoTipTrigger({
  className,
  ...props
}: Omit<PopoverPrimitive.Trigger.Props, "openOnHover" | "delay" | "closeDelay">) {
  return (
    <PopoverTrigger
      data-slot="info-tip-trigger"
      openOnHover
      // 与 TooltipProvider 的 delay 一致：悬停即开
      delay={0}
      className={cn("focus-ring", className)}
      {...props}
    />
  )
}

function InfoTipContent({
  className,
  side = "top",
  children,
  ...props
}: React.ComponentProps<typeof PopoverContent>) {
  return (
    <PopoverContent
      data-slot="info-tip-content"
      side={side}
      className={cn("w-fit max-w-xs px-3 py-1.5 text-xs", className)}
      {...props}
    >
      {/* 说明挂成弹层的 aria-describedby，读屏打开时连同弹层一起播报 */}
      <PopoverPrimitive.Description>{children}</PopoverPrimitive.Description>
    </PopoverContent>
  )
}

export { InfoTip, InfoTipContent, InfoTipTrigger }
