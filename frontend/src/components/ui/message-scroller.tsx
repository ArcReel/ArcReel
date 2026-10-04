import * as React from "react"
import {
  MessageScroller as MessageScrollerPrimitive,
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
} from "@shadcn/react/message-scroller"
import { cn } from "cn"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { ArrowDownIcon } from "lucide-react"

function MessageScrollerProvider(
  props: React.ComponentProps<typeof MessageScrollerPrimitive.Provider>
) {
  return <MessageScrollerPrimitive.Provider {...props} />
}

function MessageScroller({
  className,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Root>) {
  return (
    <MessageScrollerPrimitive.Root
      data-slot="message-scroller"
      className={cn(
        "group/message-scroller relative flex size-full min-h-0 flex-col overflow-hidden",
        className
      )}
      {...props}
    />
  )
}

function MessageScrollerViewport({
  className,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Viewport>) {
  return (
    <MessageScrollerPrimitive.Viewport
      data-slot="message-scroller-viewport"
      // 去掉上游的 scrollbar-thin 与自动滚动时隐藏滑块：元素一旦设置 scrollbar-width / scrollbar-color，
      // Chromium 就不再使用 index.css 里统一的滚动条样式。补 relative：滚动容器须是定位元素。
      // 滚动跟随由原语负责；禁用浏览器锚定，避免异步正文缩短时向上补偿被原语误判为用户上翻。
      className={cn(
        "relative size-full min-h-0 min-w-0 scroll-fade-b scrollbar-gutter-stable overflow-y-auto overscroll-contain [overflow-anchor:none] contain-content data-pending-scroll:invisible",
        className
      )}
      {...props}
    />
  )
}

function MessageScrollerContent({
  className,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Content>) {
  return (
    <MessageScrollerPrimitive.Content
      data-slot="message-scroller-content"
      // 消息间距与内边距收在原语里（调用处不改间距）：唯一的使用处是 Agent 面板，每条消息自带一行常驻行高的操作行
      className={cn("flex h-max min-h-full flex-col gap-3 px-3 py-4", className)}
      {...props}
    />
  )
}

function MessageScrollerItem({
  className,
  scrollAnchor = false,
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Item>) {
  return (
    <MessageScrollerPrimitive.Item
      data-slot="message-scroller-item"
      scrollAnchor={scrollAnchor}
      className={cn(
        // Markdown 按需渲染会改变离屏项高度；保持真实排版，使贴底跟随使用完整内容高度。
        "min-w-0 shrink-0",
        className
      )}
      {...props}
    />
  )
}

function MessageScrollerButton({
  direction = "end",
  className,
  children,
  render,
  variant = "secondary",
  size = "icon-sm",
  ...props
}: React.ComponentProps<typeof MessageScrollerPrimitive.Button> &
  Pick<React.ComponentProps<typeof Button>, "variant" | "size">) {
  const { t } = useTranslation("common")
  return (
    <MessageScrollerPrimitive.Button
      data-slot="message-scroller-button"
      data-direction={direction}
      data-variant={variant}
      data-size={size}
      direction={direction}
      className={cn(
        // 进出场改用本仓库的时长与缓动 token：上游退场 400ms 超过界面动效 300ms 的上限；
        // 「跳到最新」是圆形图标按钮，与输入区的方形按钮区分开；隐藏时只在边距内位移，
        // 上游位移整个按钮高度会越出根节点的 overflow-hidden，溢出探针判为内容被裁切
        "absolute inset-s-1/2 rounded-full -translate-x-1/2 border-border bg-background text-foreground transition-[translate,scale,opacity] duration-base ease-emphasized hover:bg-muted hover:text-foreground data-[active=false]:pointer-events-none data-[active=false]:scale-95 data-[active=false]:opacity-0 data-[active=true]:translate-y-0 data-[active=true]:scale-100 data-[active=true]:opacity-100 data-[direction=end]:bottom-4 data-[direction=end]:data-[active=false]:translate-y-2 data-[direction=start]:top-4 data-[direction=start]:data-[active=false]:-translate-y-2 rtl:translate-x-1/2 data-[direction=start]:[&_svg]:rotate-180",
        className
      )}
      render={render ?? <Button variant={variant} size={size} />}
      {...props}
    >
      {children ?? (
        <>
          <ArrowDownIcon
          />
          <span className="sr-only">
            {direction === "end" ? t("jump_to_latest") : t("jump_to_start")}
          </span>
        </>
      )}
    </MessageScrollerPrimitive.Button>
  )
}

export {
  MessageScrollerProvider,
  MessageScroller,
  MessageScrollerViewport,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerButton,
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
}
