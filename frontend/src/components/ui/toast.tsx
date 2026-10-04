import * as React from "react"
import { Toast as ToastPrimitive } from "@base-ui/react/toast"
import { cn } from "cn"

import { Button } from "@/components/ui/button"
import { XIcon, CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"
import { useTranslation } from "react-i18next"

const toast = ToastPrimitive.createToastManager()

function ToastProvider({ ...props }: ToastPrimitive.Provider.Props) {
  return <ToastPrimitive.Provider {...props} />
}

function ToastPortal({ ...props }: ToastPrimitive.Portal.Props) {
  return <ToastPrimitive.Portal data-slot="toast-portal" {...props} />
}

function ToastViewport({ className, ...props }: ToastPrimitive.Viewport.Props) {
  const { t } = useTranslation("common")
  return (
    <ToastPrimitive.Viewport
      data-slot="toast-viewport"
      aria-label={t("toast_region")}
      // 顶部居中、紧贴顶栏下方纵向排列，不遮挡右侧 Agent 输入区；层级取 z-index token
      className={cn(
        "pointer-events-none fixed top-16 left-1/2 z-toast flex w-sm max-w-[calc(100%-2rem)] -translate-x-1/2 flex-col gap-2 outline-none",
        className
      )}
      {...props}
    />
  )
}

function Toast({ className, ...props }: ToastPrimitive.Root.Props) {
  return (
    <ToastPrimitive.Root
      data-slot="toast"
      // 不用上游的叠放卡组与 500ms 位移缩放：纵向列表，进出场 200ms；
      // 减少动态效果时 index.css 的全局规则不过渡位移，只剩淡入淡出。长文本在提示内部滚动。
      className={cn(
        "group/toast pointer-events-auto relative flex max-h-48 w-full rounded-lg border bg-popover text-popover-foreground shadow-overlay outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "transition-[opacity,translate] duration-base ease-emphasized data-starting-style:-translate-y-2 data-starting-style:opacity-0 data-ending-style:opacity-0 data-limited:hidden",
        className
      )}
      {...props}
    />
  )
}

function ToastContent({ className, ...props }: ToastPrimitive.Content.Props) {
  return (
    <ToastPrimitive.Content
      data-slot="toast-content"
      // 纵向列表里每条提示都完整显示，去掉卡组的 behind / expanded 透明度切换
      className={cn(
        "flex min-w-0 flex-1 items-start gap-3 overflow-y-auto p-3.5",
        className
      )}
      {...props}
    />
  )
}

function ToastTitle({ className, ...props }: ToastPrimitive.Title.Props) {
  return (
    <ToastPrimitive.Title
      data-slot="toast-title"
      className={cn("text-sm font-medium wrap-break-word", className)}
      {...props}
    />
  )
}

function ToastDescription({
  className,
  ...props
}: ToastPrimitive.Description.Props) {
  return (
    <ToastPrimitive.Description
      data-slot="toast-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

function ToastAction({
  className,
  render = <Button variant="outline" size="sm" />,
  ...props
}: ToastPrimitive.Action.Props) {
  return (
    <ToastPrimitive.Action
      data-slot="toast-action"
      render={render}
      className={cn("shrink-0", className)}
      {...props}
    />
  )
}

function ToastClose({
  className,
  children,
  render = <Button variant="ghost" size="icon-sm" />,
  ...props
}: ToastPrimitive.Close.Props) {
  const { t } = useTranslation("common")
  return (
    <ToastPrimitive.Close
      data-slot="toast-close"
      aria-label={t("dismiss_toast")}
      render={render}
      className={cn(
        "relative shrink-0 text-muted-foreground after:absolute after:-inset-2 after:content-[''] hover:text-foreground",
        className
      )}
      {...props}
    >
      {children ?? (
        <XIcon aria-hidden="true" />
      )}
    </ToastPrimitive.Close>
  )
}

function ToastIcon({ type }: { type: string | undefined }) {
  let icon: React.ReactNode = null

  // 图标按语义着色：成功 good、警告 warn、错误 destructive
  if (type === "success") {
    icon = (
      <CircleCheckIcon className="text-good" aria-hidden="true" />
    )
  }

  if (type === "info") {
    icon = (
      <InfoIcon className="text-muted-foreground" aria-hidden="true" />
    )
  }

  if (type === "warning") {
    icon = (
      <TriangleAlertIcon className="text-warn" aria-hidden="true" />
    )
  }

  if (type === "error") {
    icon = (
      <OctagonXIcon className="text-destructive" aria-hidden="true" />
    )
  }

  if (type === "loading") {
    icon = (
      <Loader2Icon className="animate-spin" aria-hidden="true" />
    )
  }

  if (!icon) {
    return null
  }

  return (
    <span
      data-slot="toast-icon"
      className="mt-0.5 shrink-0 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4"
    >
      {icon}
    </span>
  )
}

function ToastList() {
  const { toasts } = ToastPrimitive.useToastManager()

  // 只支持桌面端（鼠标、触控板），不启用滑动关闭
  return toasts.map((toastItem) => (
    <Toast key={toastItem.id} toast={toastItem} swipeDirection={[]}>
      <ToastContent>
        <ToastIcon type={toastItem.type} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <ToastTitle />
          <ToastDescription />
        </div>
        <ToastAction />
        <ToastClose />
      </ToastContent>
    </Toast>
  ))
}

function Toaster({
  children,
  toastManager = toast,
  ...props
}: ToastPrimitive.Provider.Props) {
  return (
    <ToastProvider toastManager={toastManager} {...props}>
      {children}
      <ToastPortal>
        <ToastViewport>
          <ToastList />
        </ToastViewport>
      </ToastPortal>
    </ToastProvider>
  )
}

const createToastManager = ToastPrimitive.createToastManager
const useToastManager = ToastPrimitive.useToastManager

export {
  Toaster,
  Toast,
  ToastAction,
  ToastClose,
  ToastContent,
  ToastDescription,
  ToastPortal,
  ToastProvider,
  ToastTitle,
  ToastViewport,
  createToastManager,
  toast,
  useToastManager,
}
