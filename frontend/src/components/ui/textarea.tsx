import * as React from "react"
import { cn } from "cn"

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
      data-slot="textarea"
      className={cn(
        "relative flex field-sizing-content min-h-16 max-h-[40cqh] w-full resize-none overflow-y-auto rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/20 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/40 dark:bg-input/12 dark:disabled:bg-input/32",
        variant === "plain" && "border-transparent bg-transparent leading-relaxed hover:border-input dark:bg-transparent",
        mono && "font-mono",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
