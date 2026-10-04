import { useEffect } from "react";
import { Toaster, toast as toastQueue } from "@/components/ui/toast";
import { useAppStore } from "@/stores/app-store";

// 提示的停留时长；指针悬停或键盘聚焦在提示上时暂停计时。
const AUTO_DISMISS_MS = 5000;

/**
 * 全局提示：把 app-store 里每条新发出的 toast 转交提示队列显示。
 * 队列同时最多显示 3 条，更早的提示被挤出；错误提示由读屏立即播报。
 */
export function ToastOverlay() {
  const latest = useAppStore((s) => s.toast);

  useEffect(() => {
    if (!latest) return;
    const { id, text, tone, action } = latest;
    toastQueue.add({
      id,
      title: text,
      type: tone,
      priority: tone === "error" ? "high" : "low",
      actionProps: action
        ? {
            children: action.label,
            onClick: () => {
              action.onClick();
              toastQueue.close(id);
            },
          }
        : undefined,
    });
  }, [latest]);

  return <Toaster timeout={AUTO_DISMISS_MS} />;
}
