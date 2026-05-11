import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

interface SecondaryButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  size?: "sm" | "md";
  leadingIcon?: ReactNode;
  children?: ReactNode;
}

const SIZE_CLS: Record<NonNullable<SecondaryButtonProps["size"]>, string> = {
  sm: "px-3 py-1.5 text-[12px]",
  md: "px-4 py-2 text-[13px]",
};

// 玻璃次按钮 — Cancel / 普通操作。背景 oklch(0.22) + hairline，hover 用 CSS :hover（不用 inline JS）
export const SecondaryButton = forwardRef<HTMLButtonElement, SecondaryButtonProps>(
  function SecondaryButton(
    { size = "md", leadingIcon, className = "", children, type = "button", ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        className={`arc-btn-secondary focus-ring inline-flex items-center justify-center gap-1.5 rounded-md ${SIZE_CLS[size]} ${className}`.trim()}
        {...rest}
      >
        {leadingIcon}
        {children}
      </button>
    );
  },
);
