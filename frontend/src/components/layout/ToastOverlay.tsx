import { useCallback, useEffect, useRef } from "react";
import { CircleCheck, CircleX, Info, TriangleAlert, X } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { UI_LAYERS } from "@/utils/ui-layers";

const ICON_MAP = {
  info: Info,
  success: CircleCheck,
  error: CircleX,
  warning: TriangleAlert,
} as const;

const TONE_STYLES = {
  info: "bg-gray-800/90 border-gray-600/60 text-gray-100",
  success: "bg-emerald-950/90 border-emerald-500/50 text-emerald-100",
  error: "bg-red-950/90 border-red-500/50 text-red-100",
  warning: "bg-amber-950/90 border-amber-500/50 text-amber-100",
} as const;

const ICON_COLORS = {
  info: "text-gray-400",
  success: "text-emerald-400",
  error: "text-red-400",
  warning: "text-amber-400",
} as const;

const AUTO_DISMISS_MS = 4000;

export function ToastOverlay() {
  const toast = useAppStore((s) => s.toast);
  const clearToast = useAppStore((s) => s.clearToast);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const startTimer = useCallback(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(clearToast, AUTO_DISMISS_MS);
  }, [clearToast]);

  const pauseTimer = useCallback(() => {
    clearTimeout(timerRef.current);
  }, []);

  useEffect(() => {
    if (!toast) return;
    startTimer();
    return () => clearTimeout(timerRef.current);
  }, [toast, startTimer]);

  if (!toast) return null;

  const Icon = ICON_MAP[toast.tone];

  return (
    <div
      className={`fixed top-14 left-1/2 -translate-x-1/2 ${UI_LAYERS.toast} pointer-events-none`}
    >
      <div
        key={toast.id}
        onMouseEnter={pauseTimer}
        onMouseLeave={startTimer}
        className={`toast-enter pointer-events-auto flex items-center gap-2.5 rounded-lg border px-4 py-2.5 shadow-lg backdrop-blur-sm text-sm ${TONE_STYLES[toast.tone]}`}
      >
        <Icon className={`h-4 w-4 shrink-0 ${ICON_COLORS[toast.tone]}`} />
        <span className="max-w-sm">{toast.text}</span>
        <button
          type="button"
          onClick={clearToast}
          className="ml-1 shrink-0 rounded p-0.5 opacity-50 hover:opacity-100 transition-opacity cursor-pointer"
          aria-label="Dismiss notification"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
