import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

export type PrimaryButtonTone = "accent" | "warm" | "danger";

interface PrimaryButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: PrimaryButtonTone;
  size?: "sm" | "md";
  leadingIcon?: ReactNode;
  children?: ReactNode;
}

const SIZE_CLS: Record<NonNullable<PrimaryButtonProps["size"]>, string> = {
  sm: "px-3 py-1.5 text-[12px]",
  md: "px-4 py-2 text-[13px]",
};

// 主 CTA — 紫色（accent，默认）/ 琥珀（warm，导出 / 覆盖类暖系动作）/ 红色（danger，删除 / 替换类有损动作）
export const PrimaryButton = forwardRef<HTMLButtonElement, PrimaryButtonProps>(
  function PrimaryButton(
    { tone = "accent", size = "md", leadingIcon, className = "", children, type = "button", ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        data-tone={tone}
        className={`arc-btn-primary focus-ring inline-flex items-center justify-center gap-1.5 rounded-md font-medium ${SIZE_CLS[size]} ${className}`.trim()}
        {...rest}
      >
        {leadingIcon}
        {children}
      </button>
    );
  },
);
