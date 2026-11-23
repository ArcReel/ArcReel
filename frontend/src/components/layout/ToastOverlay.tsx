import { useEffect } from "react";
import { Toast as ToastPrimitive } from "@base-ui/react/toast";
import { Toaster } from "@/components/ui/toast";
import { useAppStore, type Toast } from "@/stores/app-store";

const AUTO_DISMISS_MS = 5000;

const toastQueue = ToastPrimitive.createToastManager();

function enqueue({ id, text, tone, action }: Toast) {
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
}

export function ToastOverlay() {
  useEffect(() => {
    const current = useAppStore.getState().toast;
    if (current) enqueue(current);
    return useAppStore.subscribe((state, prev) => {
      if (state.toast && state.toast !== prev.toast) enqueue(state.toast);
    });
  }, []);

  return <Toaster toastManager={toastQueue} timeout={AUTO_DISMISS_MS} />;
}
