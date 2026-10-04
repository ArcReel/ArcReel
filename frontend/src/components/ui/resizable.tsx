import { cn } from "cn"
import * as ResizablePrimitive from "react-resizable-panels"

function ResizablePanelGroup({
  className,
  ...props
}: ResizablePrimitive.GroupProps) {
  return (
    <ResizablePrimitive.Group
      data-slot="resizable-panel-group"
      className={cn(
        "flex h-full w-full aria-[orientation=vertical]:flex-col",
        className
      )}
      {...props}
    />
  )
}

function ResizablePanel({ ...props }: ResizablePrimitive.PanelProps) {
  return <ResizablePrimitive.Panel data-slot="resizable-panel" {...props} />
}

// 相对生成代码的改动：手柄的分隔线在悬停、拖动与键盘聚焦时用品牌色标出（库在 data-separator 上写当前状态），
// 键盘聚焦时另把两侧的命中区染色：1px 的线上画不出可见的聚焦环。
function ResizableHandle({
  withHandle,
  className,
  ...props
}: ResizablePrimitive.SeparatorProps & {
  withHandle?: boolean
}) {
  return (
    <ResizablePrimitive.Separator
      data-slot="resizable-handle"
      className={cn(
        "relative flex w-px items-center justify-center bg-border ring-offset-background transition-colors duration-fast after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:outline-hidden focus-visible:after:bg-primary/40 data-[separator=active]:bg-primary data-[separator=focus]:bg-primary data-[separator=hover]:bg-primary/60 aria-[orientation=horizontal]:h-px aria-[orientation=horizontal]:w-full aria-[orientation=horizontal]:after:left-0 aria-[orientation=horizontal]:after:h-1 aria-[orientation=horizontal]:after:w-full aria-[orientation=horizontal]:after:translate-x-0 aria-[orientation=horizontal]:after:-translate-y-1/2 [&[aria-orientation=horizontal]>div]:rotate-90",
        className
      )}
      {...props}
    >
      {withHandle && (
        <div className="z-10 flex h-6 w-1 shrink-0 rounded-lg bg-border" />
      )}
    </ResizablePrimitive.Separator>
  )
}

export { ResizableHandle, ResizablePanel, ResizablePanelGroup }
