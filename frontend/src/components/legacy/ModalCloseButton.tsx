import { forwardRef, type ButtonHTMLAttributes } from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

interface ModalCloseButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 覆盖默认 aria-label（默认从 common.close 取） */
  ariaLabel?: string;
}

/** 旧弹窗关闭按钮：Button ghost 图标按钮的薄封装。新弹层用 Dialog 自带的关闭按钮。 */
export const ModalCloseButton = forwardRef<HTMLButtonElement, ModalCloseButtonProps>(
  function ModalCloseButton({ ariaLabel, type = "button", ...rest }, ref) {
    const { t } = useTranslation("common");
    return (
      <Button ref={ref} type={type} variant="ghost" size="icon-sm" aria-label={ariaLabel ?? t("close")} {...rest}>
        <X aria-hidden="true" />
      </Button>
    );
  },
);
