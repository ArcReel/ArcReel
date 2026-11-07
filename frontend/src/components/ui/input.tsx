import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"
import { cn } from "cn"

// Changes from generated code: variant="plain" is for body-style fields such as story settings, without border/background at rest and with a border on hover/focus.
// mono selects a monospace font for code, model IDs, tokens and request paths; font and appearance variant are independent and may be combined.
// Compared with upstream: reduce input fill opacity to retain text contrast; failures use a full red outline and stronger glow.
function Input({
  className,
  type,
  variant = "default",
  mono = false,
  ...props
}: React.ComponentProps<"input"> & { variant?: "default" | "plain"; mono?: boolean }) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/20 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/40 md:text-sm dark:bg-input/12 dark:disabled:bg-input/32",
        variant === "plain" && "border-transparent bg-transparent hover:border-input dark:bg-transparent",
        mono && "font-mono",
        className
      )}
      {...props}
    />
  )
}

export { Input }
