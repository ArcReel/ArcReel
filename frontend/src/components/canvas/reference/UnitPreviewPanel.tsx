import { useTranslation } from "react-i18next";
import { Film, Loader2, Sparkles, RotateCcw, AlertTriangle } from "lucide-react";
import { API } from "@/api";
import { usePlaybackStart } from "@/hooks/usePlaybackStart";
import { useProjectsStore } from "@/stores/projects-store";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { PresentationPlayer } from "@/components/shared/PresentationPlayer";
import { NarrationAudioCard } from "@/components/canvas/timeline/NarrationAudioCard";
import { UPLOAD_VIDEO_ACCEPT, UploadIconButton } from "@/components/canvas/shared/UploadIconButton";
import { formatCost } from "@/utils/cost-format";
import { resolveUnitStatus } from "./unit-status";
import { UnitStatusBadge } from "./UnitStatusBadge";
import type { CostBreakdown, ReferenceVideoUnit, UnitStatus } from "@/types";
import { previewAspect } from "@/utils/preview-aspect";
import { Button } from "@/components/ui/button";

export interface UnitPreviewPanelProps {
  unit: ReferenceVideoUnit | null;
  projectName?: string;
  status?: UnitStatus;
  errorMessage?: string | null;
  busy?: boolean;
  estimatedCost?: CostBreakdown;
  actualCost?: CostBreakdown;
  onGenerate?: (unitId: string) => void;
  narrationText?: string;
  narrationGenerating?: boolean;
  narrationEstimatedCost?: CostBreakdown;
  onGenerateNarration?: (unitId: string) => void;
  generationBlocked?: boolean;
  onUploadVideo?: (unitId: string, file: File) => void | Promise<void>;
  uploadingVideo?: boolean;
  restoring?: boolean;
  onRestoringChange?: (unitId: string, restoring: boolean) => void;
  checkBusy?: (unitId: string) => boolean;
  onRestored?: () => void | Promise<void>;
  saveFirst?: boolean;
  saving?: boolean;
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
  busy = false,
  estimatedCost,
  actualCost,
  onGenerate,
  narrationText,
  narrationGenerating,
  narrationEstimatedCost,
  onGenerateNarration,
  generationBlocked = false,
  onUploadVideo,
  uploadingVideo,
  restoring = false,
  onRestoringChange,
  checkBusy,
  onRestored,
  saveFirst = false,
  saving = false,
}: UnitPreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const clip = unit?.generated_assets?.video_clip ?? null;
  const clipFp = useProjectsStore((s) => (clip ? s.getAssetFingerprint(clip) : null));
  const playbackStart = usePlaybackStart("reference_videos", unit?.unit_id ?? "");
  const aspect = useProjectsStore((s) => previewAspect(s.currentProjectData));

  if (!unit) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {t("reference_preview_empty")}
      </div>
    );
  }

  const effectiveStatus = status ?? resolveUnitStatus(unit);
  const videoUrl = clip && projectName ? API.getFileUrl(projectName, clip, clipFp) : null;
  const hasNarrationText = Boolean(narrationText?.trim());
  const narrationAudio = unit.generated_assets?.narration_audio ?? null;

  const ready = effectiveStatus === "ready" && Boolean(videoUrl);
  const failed = effectiveStatus === "failed";
  const inFlight =
    busy ||
    effectiveStatus === "running" ||
    (effectiveStatus === "ready" && !videoUrl);
  const generateDisabled = inFlight || busy || restoring || generationBlocked || saving;

  const ctaLabel = saveFirst
    ? t("common:save_and_generate")
    : ready
      ? t("reference_preview_regenerate")
      : failed
        ? t("reference_preview_retry")
        : t("reference_preview_generate");

  return (
    <div className="@container/preview relative flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3.5">
      <div className="flex items-center gap-1.5">
        <Film className="size-4 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-xs font-semibold text-subtle-foreground">{t("reference_preview_label")}</h3>
        <span className="flex-1" />
        {onUploadVideo && (
          <UploadIconButton
            accept={UPLOAD_VIDEO_ACCEPT}
            label={t("media_upload_video")}
            busy={uploadingVideo}
            disabled={inFlight || busy || restoring}
            onSelect={(f) => void onUploadVideo(unit.unit_id, f)}
          />
        )}
        {projectName && (
          <VersionTimeMachine
            projectName={projectName}
            resourceType="reference_videos"
            resourceId={unit.unit_id}
            onRestore={onRestored}
            busy={inFlight || busy || Boolean(uploadingVideo) || restoring}
            onRestoringChange={(r) => onRestoringChange?.(unit.unit_id, r)}
            checkBusy={checkBusy ? () => checkBusy(unit.unit_id) : undefined}
            iconOnly
          />
        )}
        <UnitStatusBadge status={effectiveStatus} />
      </div>

      <div
        data-testid="reference-preview-frame"
        data-aspect={aspect}
        className={`relative mx-auto shrink-0 overflow-hidden rounded-lg border border-border bg-muted/40 ${
          aspect === "9:16"
            ? "aspect-9/16 h-[min(55dvh,calc(100cqw*16/9))]"
            : "aspect-video w-full max-w-[calc(55dvh*16/9)]"
        }`}
      >
        {ready && videoUrl && projectName && (
          <>
            <PresentationPlayer
              key={`${unit.unit_id}:${clipFp ?? "current"}`}
              projectName={projectName}
              resourceType="reference_videos"
              resourceId={unit.unit_id}
              posterPath={unit.generated_assets?.video_thumbnail ?? null}
              {...playbackStart}
            />
            <div
              className="pointer-events-none absolute top-2 left-2 max-w-[calc(100%-1rem)] truncate rounded-sm bg-background/80 px-2 py-0.5 font-mono text-xs text-subtle-foreground"
              translate="no"
            >
              {clip}
            </div>
          </>
        )}

        {inFlight && !ready && (
          <div className="absolute inset-0 grid place-items-center p-4 text-center">
            <div>
              <Loader2 className="mx-auto mb-2.5 size-8 animate-spin text-primary" aria-hidden="true" />
              <p className="text-xs text-subtle-foreground">{t("reference_preview_in_flight")}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("reference_preview_in_flight_meta", { duration: unit.duration_seconds })}
              </p>
            </div>
          </div>
        )}

        {failed && !inFlight && (
          <div className="absolute inset-0 grid place-items-center p-4 text-center">
            <div>
              <span className="mx-auto mb-2.5 grid size-9 place-items-center rounded-full bg-destructive/10 text-destructive">
                <AlertTriangle className="size-4" aria-hidden="true" />
              </span>
              <p className="text-xs font-semibold text-destructive">{t("reference_preview_failed_title")}</p>
            </div>
          </div>
        )}

        {!ready && !inFlight && !failed && (
          <div className="absolute inset-0 grid place-items-center text-center">
            <div>
              <Film className="mx-auto mb-2 size-5 text-muted-foreground" aria-hidden="true" />
              <p className="text-xs text-muted-foreground">{t("reference_preview_empty_unit")}</p>
            </div>
          </div>
        )}
      </div>

      {failed && !inFlight && (
        <p className="text-xs leading-relaxed wrap-break-word text-muted-foreground">
          {errorMessage ?? t("reference_preview_failed_unknown")}
        </p>
      )}

      {onGenerate && (
        <Button className="w-full shrink-0" onClick={() => onGenerate(unit.unit_id)} disabled={generateDisabled}>
          {inFlight ? (
            <>
              <Loader2 className="animate-spin" aria-hidden="true" data-icon="inline-start" />
              {t("reference_preview_generating")}
            </>
          ) : (
            <>
              {failed && !saveFirst ? (
                <RotateCcw aria-hidden="true" data-icon="inline-start" />
              ) : (
                <Sparkles aria-hidden="true" data-icon="inline-start" />
              )}
              {ctaLabel}
              {hasCost(estimatedCost) && (
                <span className="font-mono tabular-nums">≈ {formatCost(estimatedCost)}</span>
              )}
            </>
          )}
        </Button>
      )}

      {generationBlocked && (
        <p className="text-xs text-warn">
          {t("reference_needs_replan")}
        </p>
      )}

      {(hasNarrationText || narrationAudio) && projectName && (
        <NarrationAudioCard
          projectName={projectName}
          segmentId={unit.unit_id}
          novelText={narrationText ?? ""}
          assetPath={narrationAudio}
          generating={narrationGenerating}
          generateDisabled={!hasNarrationText || saving}
          generateDisabledHint={!hasNarrationText ? t("no_original_text") : undefined}
          generateLabel={saveFirst ? t("common:save_and_generate") : undefined}
          estimatedCost={narrationEstimatedCost}
          onGenerate={onGenerateNarration ? () => onGenerateNarration(unit.unit_id) : undefined}
        />
      )}

      <section className="rounded-lg border border-border bg-card p-3">
        <h3 className="mb-2 text-xs font-medium text-muted-foreground">{t("reference_preview_metadata")}</h3>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-3.5 gap-y-1.5 text-xs">
          <dt className="text-muted-foreground">{t("reference_meta_duration")}</dt>
          <dd className="font-mono tabular-nums text-subtle-foreground">
            {t("reference_editor_unit_meta", { duration: unit.duration_seconds })}
          </dd>
          <dt className="text-muted-foreground">{t("reference_meta_status")}</dt>
          <dd>
            <UnitStatusBadge status={effectiveStatus} />
          </dd>
          {hasCost(actualCost) && (
            <>
              <dt className="text-muted-foreground">{t("reference_meta_cost")}</dt>
              <dd className="font-mono tabular-nums text-good">
                {formatCost(actualCost)}
                <span className="ml-1 text-muted-foreground">{t("reference_meta_cost_spent")}</span>
              </dd>
            </>
          )}
        </dl>
      </section>
    </div>
  );
}
