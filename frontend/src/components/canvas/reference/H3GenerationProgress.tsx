import { Check, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { H3ExecutionProgress } from "@/types";

interface H3GenerationProgressProps {
  progress: H3ExecutionProgress;
  onCancel?: () => void;
}

const PHASE_INDEX: Record<H3ExecutionProgress["phase"], number> = {
  style_analyzing: 0,
  prompt_optimizing: 1,
  submitted: 2,
  queued: 3,
  preparing: 4,
  running: 4,
  cancelling: 4,
  completed: 5,
  failed: 5,
  cancelled: 5,
};

export function H3GenerationProgress({ progress, onCancel }: H3GenerationProgressProps) {
  const { t } = useTranslation("dashboard");
  const activeIndex = PHASE_INDEX[progress.phase];
  const labels = [
    t("h3_progress_style"),
    t("h3_progress_optimize"),
    t("h3_progress_submitted"),
    t("h3_progress_queued"),
    t("h3_progress_generating"),
  ];
  const percent = Math.max(0, Math.min(100, progress.progress ?? 0));

  let detail = progress.phase === "style_analyzing"
    ? t("h3_progress_style_detail")
    : t("h3_progress_optimize_detail");
  if (progress.phase === "submitted") detail = t("h3_progress_submitted_detail");
  if (progress.phase === "queued") {
    detail =
      progress.queue_ahead == null
        ? t("h3_progress_queued_waiting")
        : t("h3_progress_queued_detail", {
            ahead: progress.queue_ahead,
            total: progress.queue_length ?? progress.queue_position ?? "—",
          });
  }
  if (progress.phase === "preparing") detail = t("h3_progress_preparing_detail");
  if (progress.phase === "running") detail = t("h3_progress_running_detail", { progress: percent });
  if (progress.phase === "cancelling") detail = t("h3_progress_cancelling_detail");

  return (
    <div className="w-[min(92%,420px)] rounded-xl border border-white/10 bg-black/70 p-4 text-left shadow-xl backdrop-blur">
      <div className="mb-3 flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent)]" aria-hidden="true" />
        <span className="text-xs font-semibold text-white">{detail}</span>
      </div>

      <ol className="grid grid-cols-5 gap-1" aria-label={t("h3_progress_aria")}>
        {labels.map((label, index) => {
          const completed = index < activeIndex;
          const active = index === activeIndex || (activeIndex === 5 && index === 4);
          return (
            <li key={label} className="min-w-0 text-center">
              <div className="mb-1 flex items-center">
                <span
                  className={`grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[9px] ${
                    completed || activeIndex === 5
                      ? "border-emerald-300/70 bg-emerald-400/20 text-emerald-200"
                      : active
                        ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-white"
                        : "border-white/15 text-white/35"
                  }`}
                >
                  {completed || activeIndex === 5 ? <Check className="h-3 w-3" /> : index + 1}
                </span>
                {index < labels.length - 1 && (
                  <span
                    className={`h-px flex-1 ${index < activeIndex ? "bg-emerald-300/50" : "bg-white/10"}`}
                  />
                )}
              </div>
              <span className={`block truncate text-[9px] ${active ? "text-white/85" : "text-white/45"}`}>
                {label}
              </span>
            </li>
          );
        })}
      </ol>

      {(progress.phase === "running" || progress.phase === "preparing") && (
        <div className="mt-3">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-white/10"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
          >
            <div
              className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>
          <div className="mt-1 text-right font-mono text-[9px] tabular-nums text-white/50">
            {percent}%
          </div>
        </div>
      )}

      {progress.can_cancel && onCancel && progress.phase !== "cancelling" && (
        <button
          type="button"
          onClick={onCancel}
          className="focus-ring mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-[11px] font-medium text-white/75 hover:bg-white/10"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
          {t("h3_progress_cancel")}
        </button>
      )}
    </div>
  );
}
