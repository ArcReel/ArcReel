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
  /** Composite UI status — combines persisted state, queue, and optimistic flags.
   *  When omitted, falls back to `video_clip ? 'ready' : 'pending'`. */
  status?: UnitStatus;
  /** Latest task error message (if any) for the failed state. */
  errorMessage?: string | null;
  /**
   * Occupancy includes optimistic marks before actual queued rows persist, independently of status.
   * Optimistic status applies only without queue rows; retries/regeneration retain historical rows,
   * so status-only checks would miss disabling during optimistic windows.
   */
  busy?: boolean;
  /** Estimated cost for this unit (optional; rendered next to the CTA). */
  estimatedCost?: CostBreakdown;
  /** Actual already-spent cost; rendered in the metadata block. */
  actualCost?: CostBreakdown;
  onGenerate?: (unitId: string) => void;
  narrationText?: string;
  narrationGenerating?: boolean;
  narrationEstimatedCost?: CostBreakdown;
  onGenerateNarration?: (unitId: string) => void;
  /** Script unit requires replanning and cannot generate until repaired. */
  generationBlocked?: boolean;
  /** Upload final video, replacing AI output; omitted hides upload entry. */
  onUploadVideo?: (unitId: string, file: File) => void | Promise<void>;
  /** Upload in progress. */
  uploadingVideo?: boolean;
  /**
   * Unit version-restoration request in flight. Restoration creates no task rows and cannot enter tasks-store occupancy;
   * {@link VersionTimeMachine} reports through `onRestoringChange`, but state must live **outside this panel**:
   * narrow subtabs/wide sidebars mount it separately, unmounting across subpages/breakpoints without canceling requests;
   * panels also reuse across selected units, so local state would leak between them.
   */
  restoring?: boolean;
  onRestoringChange?: (unitId: string, restoring: boolean) => void;
  /**
   * Fresh restoration submit-time occupancy recheck: Agents/batches/polling may occupy units while panels stay open;
   * `restoring`/`busy` render snapshots only update after rendering flushes, leaving interim clicks able to request restore.
   */
  checkBusy?: (unitId: string) => boolean;
  /** Post-restoration refresh callback refetches units. */
  onRestored?: () => void | Promise<void>;
  /** Unsaved bodies label generation save-and-generate; `onGenerate` saves first. */
  saveFirst?: boolean;
  /** Body-save requests disable generation to avoid using presave content. */
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
  // Upload/restore keeps paths unchanged; fingerprint cache-busting makes <video> refetch.
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

  // Status may persist before video_clip, leaving effectiveStatus==="ready" but videoUrl
  // null; use inFlight placeholders instead of empty panels.
  const ready = effectiveStatus === "ready" && Boolean(videoUrl);
  const failed = effectiveStatus === "failed";
  // Include busy so retries/regeneration show placeholders during optimistic windows too.
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
    // Preview columns are width containers; portrait frames derive height from column width (100cqw).
    <div className="@container/preview relative flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3.5">
      <div className="flex items-center gap-1.5">
        <Film className="size-4 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-xs font-semibold text-subtle-foreground">{t("reference_preview_label")}</h3>
        <span className="flex-1" />
        {/* Upload is a same-unit sibling action, disabled together with primary CTA; while occupied it would
            write the same final file as running generation. */}
        {onUploadVideo && (
          <UploadIconButton
            accept={UPLOAD_VIDEO_ACCEPT}
            label={t("media_upload_video")}
            busy={uploadingVideo}
            disabled={inFlight || busy || restoring}
            onSelect={(f) => void onUploadVideo(unit.unit_id, f)}
          />
        )}
        {/* Version restoration also writes this unit's final file, disabled with upload/primary CTA:
            restoring while occupied could show success before running generation overwrites it. */}
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

      {/* Project-aspect frames: portrait 9:16 takes minimum of 55dvh and column-width-derived height; landscape 16:9 also caps at 55dvh.
          Frames do not shrink with column height; overflow scrolls the whole column. */}
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
          // Failure reasons may be long: frame holds only title/first lines, full text below it.
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

      {/* Unit headers already announce this status via alert; here explain only generation-button unavailability. */}
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
