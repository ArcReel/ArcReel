import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

export type PrimaryButtonTone = "accent" | "warm" | "danger";

interface PrimaryButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: PrimaryButtonTone;
  size?: "sm" | "md";
  leadingIcon?: ReactNode;
  children?: ReactNode;
}

// 琥珀色只表示警告与过期，warm 与 accent 一样渲染为主按钮；删除、替换类有损动作用 destructive。
const VARIANT = { accent: "default", warm: "default", danger: "destructive" } as const;
const SIZE = { sm: "sm", md: "default" } as const;

/** 旧主按钮：Button 的薄封装。新代码直接用 `components/ui/button`，调用处由各区域逐步替换。 */
export const PrimaryButton = forwardRef<HTMLButtonElement, PrimaryButtonProps>(
  function PrimaryButton(
    { tone = "accent", size = "md", leadingIcon, children, type = "button", ...rest },
    ref,
  ) {
    return (
      <Button ref={ref} type={type} variant={VARIANT[tone]} size={SIZE[size]} {...rest}>
        {leadingIcon}
        {children}
      </Button>
    );
  },
);
