import { useTranslation } from "react-i18next";
import { Film, Loader2, Sparkles, RotateCcw, AlertTriangle } from "lucide-react";
import { API } from "@/api";
import { formatCost } from "@/utils/cost-format";
import { StatusBadge, deriveUnitStatus } from "./unit-status";
import type { CostBreakdown, ReferenceVideoUnit, UnitStatus } from "@/types";

export interface UnitPreviewPanelProps {
  unit: ReferenceVideoUnit | null;
  projectName?: string;
  /** Composite UI status — combines persisted state, queue, and optimistic flags.
   *  When omitted, falls back to `video_clip ? 'ready' : 'pending'`. */
  status?: UnitStatus;
  /** Latest task error message (if any) for the failed state. */
  errorMessage?: string | null;
  /** Estimated cost for this unit (optional; rendered next to the CTA). */
  estimatedCost?: CostBreakdown;
  /** Actual already-spent cost; rendered in the metadata block. */
  actualCost?: CostBreakdown;
  onGenerate?: (unitId: string) => void;
}

function hasCost(b: CostBreakdown | undefined): boolean {
  if (!b) return false;
  for (const v of Object.values(b)) if (v > 0) return true;
  return false;
}

export function UnitPreviewPanel({
  unit,
  projectName,
  status,
  errorMessage,
  estimatedCost,
  actualCost,
  onGenerate,
}: UnitPreviewPanelProps) {
  const { t } = useTranslation("dashboard");

  if (!unit) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-[var(--color-text-4)]">
        {t("reference_preview_empty")}
      </div>
    );
  }

  const effectiveStatus = deriveUnitStatus(unit, status ? { [unit.unit_id]: status } : undefined);
  const ready = effectiveStatus === "ready";
  const failed = effectiveStatus === "failed";
  const inFlight = effectiveStatus === "running";

  const clip = unit.generated_assets.video_clip;
  const videoUrl = clip && projectName ? API.getFileUrl(projectName, clip) : null;

  const ctaLabel = ready
    ? t("reference_preview_regenerate")
    : failed
      ? t("reference_preview_retry")
      : t("reference_preview_generate");

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto px-3.5 py-3.5">
      <div className="flex items-center gap-1.5">
        <Film className="h-4 w-4 text-[var(--color-text-3)]" aria-hidden="true" />
        <span className="text-xs font-semibold text-[var(--color-text-2)]">
          {t("reference_preview_label")}
        </span>
        <span className="flex-1" />
        <StatusBadge status={effectiveStatus} size="md" />
      </div>

      <div
        className={`relative aspect-video w-full overflow-hidden rounded-lg border border-[var(--color-hairline)] shadow-[0_16px_40px_-16px_oklch(0_0_0_/_0.7)] ${
          ready
            ? "bg-[linear-gradient(135deg,oklch(0.32_0.04_240),oklch(0.18_0.02_280))]"
            : "bg-[oklch(0.18_0.010_265_/_0.5)]"
        }`}
      >
        {ready && videoUrl && (
          <>
            {/* eslint-disable-next-line jsx-a11y/media-has-caption -- AI-generated video clips have no caption track */}
            <video
              src={videoUrl}
              aria-label={t("reference_preview_video_aria", { id: unit.unit_id })}
              controls
              preload="metadata"
              playsInline
              className="h-full w-full object-contain"
            />
            <div
              className="pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded border border-white/10 bg-black/55 px-2 py-0.5 font-mono text-[10px] text-white/85 backdrop-blur"
              translate="no"
            >
              {clip}
            </div>
          </>
        )}

        {inFlight && !ready && (
          <div className="absolute inset-0 grid place-items-center">
            <div className="text-center">
              <div className="mx-auto mb-2.5 h-9 w-9 animate-spin rounded-full border-2 border-[var(--color-accent-soft)] border-t-[var(--color-accent)]" />
              <div className="text-[11.5px] text-[var(--color-text-2)]">
                {t("reference_preview_in_flight")}
              </div>
              <div className="mt-1 text-[10.5px] text-[var(--color-text-4)]">
                {t("reference_preview_in_flight_meta", {
                  refs: unit.references.length,
                  duration: unit.duration_seconds,
                })}
              </div>
            </div>
          </div>
        )}

        {failed && (
          <div className="absolute inset-0 grid place-items-center p-5">
            <div className="max-w-[280px] text-center">
              <div className="mx-auto mb-2.5 grid h-9 w-9 place-items-center rounded-full border border-red-400/60 bg-red-500/15 text-red-300">
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="mb-1 text-xs font-semibold text-red-300">
                {t("reference_preview_failed_title")}
              </div>
              <div className="text-[11px] leading-relaxed text-[var(--color-text-3)]">
                {errorMessage ?? t("reference_preview_failed_unknown")}
              </div>
            </div>
          </div>
        )}

        {!ready && !inFlight && !failed && (
          <div className="absolute inset-0 grid place-items-center">
            <div className="text-center">
              <Film
                className="mx-auto mb-2 h-5 w-5 text-[var(--color-text-4)]"
                aria-hidden="true"
              />
              <div className="text-[11.5px] text-[var(--color-text-4)]">
                {t("reference_preview_empty_unit")}
              </div>
            </div>
          </div>
        )}
      </div>

      {onGenerate && (
        <button
          type="button"
          onClick={() => onGenerate(unit.unit_id)}
          disabled={inFlight}
          className={`focus-ring inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2.5 text-sm font-semibold transition-colors ${
            inFlight
              ? "cursor-not-allowed border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.6)] text-[var(--color-text-3)]"
              : "text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_4px_14px_-4px_var(--color-accent-glow)]"
          }`}
        >
          {inFlight ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              <span>{t("reference_preview_generating")}</span>
            </>
          ) : (
            <>
              {failed ? (
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              <span>{ctaLabel}</span>
              {hasCost(estimatedCost) && (
                <span className="ml-1 font-mono text-[11px] tabular-nums opacity-70">
                  ≈ {formatCost(estimatedCost)}
                </span>
              )}
            </>
          )}
        </button>
      )}

      <div className="rounded-lg border border-[var(--color-hairline-soft)] bg-[oklch(0.18_0.010_265_/_0.5)] p-3">
        <div className="mb-2 font-mono text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-4)]">
          {t("reference_preview_metadata")}
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3.5 gap-y-1.5 text-[11.5px]">
          <dt className="text-[var(--color-text-4)]">{t("reference_meta_unit")}</dt>
          <dd className="font-mono text-[var(--color-text-2)]" translate="no">
            {unit.unit_id}
          </dd>
          <dt className="text-[var(--color-text-4)]">{t("reference_meta_duration")}</dt>
          <dd className="font-mono tabular-nums text-[var(--color-text-2)]">
            {unit.duration_seconds}s
          </dd>
          <dt className="text-[var(--color-text-4)]">{t("reference_meta_shots")}</dt>
          <dd className="font-mono tabular-nums text-[var(--color-text-2)]">{unit.shots.length}</dd>
          <dt className="text-[var(--color-text-4)]">{t("reference_meta_references")}</dt>
          <dd className="font-mono tabular-nums text-[var(--color-text-2)]">
            {unit.references.length}
          </dd>
          <dt className="text-[var(--color-text-4)]">{t("reference_meta_status")}</dt>
          <dd>
            <StatusBadge status={effectiveStatus} size="md" />
          </dd>
          {hasCost(actualCost) && (
            <>
              <dt className="text-[var(--color-text-4)]">{t("reference_meta_cost")}</dt>
              <dd className="font-mono tabular-nums text-emerald-300">
                {formatCost(actualCost)}
                <span className="ml-1 text-[var(--color-text-4)]">
                  {t("reference_meta_cost_spent")}
                </span>
              </dd>
            </>
          )}
        </dl>
      </div>
    </div>
  );
}
