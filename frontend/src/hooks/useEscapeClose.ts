import { useEffect } from "react";
import { isOnboardingTourActive } from "@/onboarding/tour";

export function useEscapeClose(onClose: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      // 引导期间 Esc 交给 driver 自己的退出流程处理，底层弹窗让位，
      // 否则两边同时响应会把弹窗里尚未提交的内容一并关没。
      if (e.key === "Escape" && !isOnboardingTourActive()) onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, enabled]);
}
