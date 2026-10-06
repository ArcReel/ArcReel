import * as React from "react"
import { cn } from "cn"

// 自动撑高：优先 CSS field-sizing: content，程序写入与宽度变化都由浏览器重排。
// 不支持的浏览器回退到 JS 测量：值变化与 input 事件时重算，ResizeObserver 监听宽度变化。
function supportsFieldSizing() {
  return typeof CSS !== "undefined" && CSS.supports("field-sizing", "content")
}

function fitToContent(el: HTMLTextAreaElement) {
  const style = getComputedStyle(el)
  // scrollHeight 不含边框，border-box 下要补上，否则最后一行被裁掉。
  const border =
    style.boxSizing === "border-box"
      ? (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0)
      : 0
  el.style.height = "auto"
  el.style.height = `${el.scrollHeight + border}px`
}

function useFieldSizingFallback(
  ref: React.RefObject<HTMLTextAreaElement | null>,
  value: React.ComponentProps<"textarea">["value"]
) {
  const [fallback] = React.useState(() => !supportsFieldSizing())

  React.useLayoutEffect(() => {
    const el = ref.current
    if (!fallback || !el) return
    let width: number | undefined
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width === width) return
      width = entry.contentRect.width
      fitToContent(el)
    })
    const onInput = () => fitToContent(el)
    observer.observe(el)
    el.addEventListener("input", onInput)
    return () => {
      observer.disconnect()
      el.removeEventListener("input", onInput)
    }
  }, [fallback, ref])

  React.useLayoutEffect(() => {
    if (fallback && ref.current) fitToContent(ref.current)
  }, [fallback, ref, value])
}

// 相对生成代码的改动：默认上限为所在尺寸容器高度的 40%（没有尺寸容器时按视口），
// 超出后在内部滚动；高度随内容变化，去掉手动拖拽；只支持桌面端，字号固定为 text-sm，去掉按视口切换的 md:text-sm。
// 另加 variant="plain"：正文样式的字段（如故事设定），静止时没有边框与底色、行高放宽，悬停或聚焦时显出边框。
// 另加 mono：换成等宽字体，用于 JSON、代码与请求体；它管字体、variant 管外观，两者独立，可同时使用。
function Textarea({
  className,
  ref,
  variant = "default",
  mono = false,
  ...props
}: React.ComponentProps<"textarea"> & { variant?: "default" | "plain"; mono?: boolean }) {
  const innerRef = React.useRef<HTMLTextAreaElement | null>(null)
  useFieldSizingFallback(innerRef, props.value)

  const setRef = React.useCallback(
    (el: HTMLTextAreaElement | null) => {
      innerRef.current = el
      if (typeof ref === "function") ref(el)
      else if (ref) ref.current = el
    },
    [ref]
  )

  return (
    <textarea
      ref={setRef}
      // 补 relative：与其他滚动容器一致写成定位元素（textarea 没有子元素，不影响布局）
      data-slot="textarea"
      className={cn(
        "relative flex field-sizing-content min-h-16 max-h-[40cqh] w-full resize-none overflow-y-auto rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        variant === "plain" && "border-transparent bg-transparent leading-relaxed hover:border-input dark:bg-transparent",
        mono && "font-mono",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
