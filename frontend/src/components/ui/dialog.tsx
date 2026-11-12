import * as React from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"
import { cn } from "cn"

import { Button } from "@/components/ui/button"
import { XIcon } from "lucide-react"
import { useTranslation } from "react-i18next"

function Dialog({ ...props }: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogTrigger({ ...props }: DialogPrimitive.Trigger.Props) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogPortal({ ...props }: DialogPrimitive.Portal.Props) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />
}

function DialogClose({ ...props }: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({
  className,
  ...props
}: DialogPrimitive.Backdrop.Props) {
  return (
    <DialogPrimitive.Backdrop
      data-slot="dialog-overlay"
      // 遮罩不透明、不模糊，与新手引导遮罩同一规范；层级取 z-index token
      className={cn(
        "fixed inset-0 isolate z-overlay bg-scrim duration-100 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
        className
      )}
      {...props}
    />
  )
}

function DialogContent({
  className,
  children,
  size = "default",
  showCloseButton = true,
  ...props
}: DialogPrimitive.Popup.Props & {
  size?: "sm" | "default" | "lg" | "xl" | "wizard" | "viewer"
  showCloseButton?: boolean
}) {
  const { t } = useTranslation("common")
  return (
    <DialogPortal>
      <DialogOverlay />
      {/* 原语负责限高：Header / Body / Footer 纵向排布，只有 DialogBody 滚动，标题与底部操作始终可见。
          宽度按 size 分档，不让调用处用视口断点覆盖。多步向导（wizard）固定高度，切换步骤时外框与底部按钮不移动。
          图片查看器（viewer）占满视口四周各留 1rem 的区域，大图按可用空间缩放。 */}
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        data-size={size}
        className={cn(
          // 移动档贴底弹出：宽度占满、上圆角、最大高度留出刘海，操作按钮在拇指可及范围内。
          "fixed z-overlay flex flex-col overflow-hidden bg-popover text-sm text-popover-foreground shadow-overlay ring-1 ring-foreground/10 duration-100 outline-none",
          "max-md:inset-x-0 max-md:bottom-0 max-md:top-auto max-md:max-h-[calc(100dvh-2rem)] max-md:w-full max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-t-xl max-md:rounded-b-none",
          "md:top-1/2 md:left-1/2 md:max-h-[calc(100dvh-2rem)] md:w-[calc(100%-2rem)] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-xl",
          "data-[size=default]:md:max-w-lg data-[size=lg]:md:max-w-2xl data-[size=sm]:md:max-w-sm data-[size=xl]:md:max-w-4xl data-[size=wizard]:md:h-[min(760px,calc(100dvh-48px))] data-[size=wizard]:md:max-w-180 data-[size=viewer]:md:h-[calc(100dvh-2rem)] data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close
            data-slot="dialog-close"
            render={
              <Button
                variant="ghost"
                className="absolute top-3 right-3"
                size="icon-sm"
              />
            }
          >
            <XIcon
            />
            <span className="sr-only">{t("close")}</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Popup>
    </DialogPortal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      // 不随 Body 滚动；右侧给关闭按钮留位
      className={cn("flex shrink-0 flex-col gap-1.5 border-b px-4 py-3 pr-12 md:px-5 md:py-4", className)}
      {...props}
    />
  )
}

// 本地新增：弹层里唯一的滚动区，内容超高时只有它滚动
function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-body"
      className={cn(
        "relative min-h-0 flex-1 overflow-y-auto px-4 py-3 [scrollbar-gutter:stable] md:px-5 md:py-4",
        className
      )}
      {...props}
    />
  )
}

function DialogFooter({
  className,
  showCloseButton = false,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  showCloseButton?: boolean
}) {
  const { t } = useTranslation("common")
  return (
    <div
      data-slot="dialog-footer"
      // 固定在底部、不随 Body 滚动；桌面端按钮一行靠右，移动端铺满整行便于点按。
      className={cn(
        "flex shrink-0 flex-wrap items-center justify-end gap-2 border-t bg-muted/50 px-4 py-3 max-md:[&>[data-slot=button]]:flex-1 max-md:pb-[max(0.75rem,env(safe-area-inset-bottom))] md:px-5",
        className
      )}
      {...props}
    >
      {children}
      {showCloseButton && (
        <DialogPrimitive.Close render={<Button variant="outline" />}>
          {t("close")}
        </DialogPrimitive.Close>
      )}
    </div>
  )
}

function DialogTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn(
        // 中文标题可能折行，行高不能为 1
        "text-base leading-snug font-medium",
        className
      )}
      {...props}
    />
  )
}

function DialogDescription({
  className,
  ...props
}: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        "text-sm text-muted-foreground *:[a]:underline *:[a]:underline-offset-3 *:[a]:hover:text-foreground",
        className
      )}
      {...props}
    />
  )
}

export {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
}
