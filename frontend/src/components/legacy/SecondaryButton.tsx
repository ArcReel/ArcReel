import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface SecondaryButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  size?: "sm" | "md";
  leadingIcon?: ReactNode;
  children?: ReactNode;
}

const SIZE = { sm: "sm", md: "default" } as const;

/** 旧次按钮：Button outline 变体的薄封装。新代码直接用 `components/ui/button`，调用处由各区域逐步替换。 */
export const SecondaryButton = forwardRef<HTMLButtonElement, SecondaryButtonProps>(
  function SecondaryButton({ size = "md", leadingIcon, children, type = "button", ...rest }, ref) {
    return (
      <Button ref={ref} type={type} variant="outline" size={SIZE[size]} {...rest}>
        {leadingIcon}
        {children}
      </Button>
    );
  },
);
