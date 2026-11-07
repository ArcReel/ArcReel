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
      // Remove upstream scrollbar-thin and hiding thumbs during autoscroll: once scrollbar-width / scrollbar-color are set,
      // Chromium stops using shared index.css scrollbar styles. Add relative: scroll containers must be positioned elements.
      // Primitives handle scroll following; disable browser anchoring so upward compensation after async body shortening is not mistaken for user scrolling.
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
      // Primitives own message spacing/padding (callers do not change them): only Agent panels use this, each message with a persistent-height action row.
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
        // On-demand Markdown rendering changes offscreen item heights; keep actual layout so bottom following uses full content heights.
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
        // Use repository duration/easing tokens for entry/exit: upstream 400ms exit exceeds the UI-animation 300ms limit;
        // Jump to latest is a round icon button, distinct from square input-area buttons; hiding translates within margins only,
        // since upstream full-button-height movement extends beyond root overflow-hidden and overflow probes flag clipping.
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
