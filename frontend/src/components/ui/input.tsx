import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"
import { cn } from "cn"

// 相对生成代码的改动：variant="plain" 是正文样式的字段（如故事设定），静止时没有边框与底色，悬停或聚焦时显出边框。
// mono 换成等宽字体，用于代码、模型 ID、令牌与请求路径；它管字体、variant 管外观，两者独立，可同时使用。
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
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        variant === "plain" && "border-transparent bg-transparent hover:border-input dark:bg-transparent",
        mono && "font-mono",
        className
      )}
      {...props}
    />
  )
}

export { Input }
