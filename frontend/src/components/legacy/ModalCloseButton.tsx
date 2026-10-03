import { forwardRef, type ButtonHTMLAttributes } from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ModalCloseButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 覆盖默认 aria-label（默认从 common.close 取） */
  ariaLabel?: string;
}

// 玻璃 X 关闭按钮 — hover 走 CSS :hover（消除 6 处 inline onMouseEnter/Leave 重复）
export const ModalCloseButton = forwardRef<HTMLButtonElement, ModalCloseButtonProps>(
  function ModalCloseButton({ ariaLabel, className = "", type = "button", ...rest }, ref) {
    const { t } = useTranslation("common");
    return (
      <button
        ref={ref}
        type={type}
        aria-label={ariaLabel ?? t("close")}
        className={`arc-close-btn focus-ring grid h-7 w-7 place-items-center rounded-md ${className}`.trim()}
        {...rest}
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>
    );
  },
);
