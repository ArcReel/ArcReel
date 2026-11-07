import * as React from "react"
import { cn } from "cn"

// Auto-height: prefer CSS field-sizing: content; browsers relayout on programmatic writes and width changes.
// Unsupported browsers fall back to JS measurement: recalculate on value changes/input events; ResizeObserver tracks width changes.
function supportsFieldSizing() {
  return typeof CSS !== "undefined" && CSS.supports("field-sizing", "content")
}

function fitToContent(el: HTMLTextAreaElement) {
  const style = getComputedStyle(el)
  // scrollHeight excludes borders; add them for border-box or final lines get clipped.
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

// Change from generated code: default cap is 40% of containing size-container height (viewport when no size container exists),
// scrolling internally above it; height follows content, removing manual resizing; desktop only, fixed text-sm without viewport-dependent md:text-sm.
// Add variant="plain": directly edited canvas bodies (e.g. story settings), without borders/fills at rest, with looser line-height and borders on hover/focus.
// Callers pass max-h-none for long-page body fields, expanding with content and scrolling pages rather than nesting editor scrolling.
// Add mono: monospace fonts for JSON/code/request bodies; mono controls fonts and variant controls appearance, independently and combinably.
// Compared with upstream: reduce input fill opacity to retain text contrast; failures use a full red outline and stronger glow.
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
      // Add relative: positioned like other scroll containers (textarea has no children, so layout is unaffected).
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
