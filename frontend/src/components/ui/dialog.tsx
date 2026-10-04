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
  size?: "sm" | "default" | "lg" | "xl" | "wizard"
  showCloseButton?: boolean
}) {
  const { t } = useTranslation("common")
  return (
    <DialogPortal>
      <DialogOverlay />
      {/* 原语负责限高：Header / Body / Footer 纵向排布，只有 DialogBody 滚动，标题与底部操作始终可见。
          宽度按 size 分档，不让调用处用视口断点覆盖。多步向导（wizard）固定高度，切换步骤时外框与底部按钮不移动。 */}
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        data-size={size}
        className={cn(
          "fixed top-1/2 left-1/2 z-overlay flex max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl bg-popover text-sm text-popover-foreground shadow-overlay ring-1 ring-foreground/10 duration-100 outline-none data-[size=default]:max-w-lg data-[size=lg]:max-w-2xl data-[size=sm]:max-w-sm data-[size=xl]:max-w-4xl data-[size=wizard]:h-[min(760px,calc(100dvh-48px))] data-[size=wizard]:max-w-180 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
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
      className={cn("flex shrink-0 flex-col gap-1.5 border-b px-5 py-4 pr-12", className)}
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
        "relative min-h-0 flex-1 overflow-y-auto px-5 py-4 [scrollbar-gutter:stable]",
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
      // 固定在底部、不随 Body 滚动；只支持桌面端，按钮恒为一行靠右
      className={cn(
        "flex shrink-0 items-center justify-end gap-2 border-t bg-muted/50 px-5 py-3",
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
