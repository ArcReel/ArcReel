import { useEffect } from "react";
import { Toast as ToastPrimitive } from "@base-ui/react/toast";
import { Toaster } from "@/components/ui/toast";
import { useAppStore, type Toast } from "@/stores/app-store";

// Toast duration; pause while pointer hover or keyboard focus remains on the toast.
const AUTO_DISMISS_MS = 5000;

// The toast queue lives only here; other code emits through app-store pushToast
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

/**
 * Global notices: forward each newly emitted app-store toast to the display queue.
 * Subscribe directly instead of reading during render: a burst in one batch merges into its last item before rendering, while subscriptions receive each write.
 * Show at most three at once, dropping older notices; screen readers announce errors immediately.
 */
export function ToastOverlay() {
  useEffect(() => {
    // Also display the latest toast emitted before mount
    const current = useAppStore.getState().toast;
    if (current) enqueue(current);
    return useAppStore.subscribe((state, prev) => {
      if (state.toast && state.toast !== prev.toast) enqueue(state.toast);
    });
  }, []);

  return <Toaster toastManager={toastQueue} timeout={AUTO_DISMISS_MS} />;
}
