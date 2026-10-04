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
import { StatusBadge, resolveUnitStatus } from "./unit-status";
import type { CostBreakdown, ReferenceVideoUnit, UnitStatus } from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";
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
   * 占用集（含入队后真实任务行落库前的乐观标记）命中与否，独立于 status：
   * status 的乐观分支只在无任务行时生效，重试与重新生成这两条路径上旧任务行始终在，
   * 仅看 status 会在乐观窗口内漏禁用。
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
  /** 剧本单元需重新规划，在修复前不可生成。 */
  generationBlocked?: boolean;
  /** 上传成片视频（替换该单元的 AI 生成视频）；未提供时不显示上传入口 */
  onUploadVideo?: (unitId: string, file: File) => void | Promise<void>;
  /** 上传进行中 */
  uploadingVideo?: boolean;
  /**
   * 该 unit 的版本恢复请求在途。恢复不产生任务行、进不了 tasks-store 占用集，状态由
   * {@link VersionTimeMachine} 经 `onRestoringChange` 上报，但必须存在**本面板之外**：
   * 本面板在窄屏 sub-tab 与宽屏右栏是两处挂载点，切换子页或跨越断点都会卸载它，而在途
   * 的恢复请求不会因此取消；且同一面板会随选中项切换复用，状态存在这里还会串到别的 unit。
   */
  restoring?: boolean;
  onRestoringChange?: (unitId: string, restoring: boolean) => void;
  /**
   * 恢复提交时刻的占用复核（新鲜读）：面板打开着而 Agent、批量入口或轮询随后占用该 unit
   * 时，`restoring`/`busy` 这类渲染快照要等 render 冲刷才生效，其间的点击仍会发出恢复请求。
   */
  checkBusy?: (unitId: string) => boolean;
  /** 版本恢复后的刷新回调（重新拉取 units） */
  onRestored?: () => void | Promise<void>;
  /** 正文有未保存修改：生成按钮写「保存并生成」，`onGenerate` 负责先保存。 */
  saveFirst?: boolean;
  /** 正文保存请求在途：生成按钮置灰，避免按保存前的内容生成。 */
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
  // 上传/还原后路径不变，靠 fingerprint cache-bust 让 <video> 重新拉取
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

  // 状态先于 video_clip 落库的窗口里，effectiveStatus==="ready" 但 videoUrl
  // 还为 null —— 这种情况下走 inFlight 占位避免空白面板。
  const ready = effectiveStatus === "ready" && Boolean(videoUrl);
  const failed = effectiveStatus === "failed";
  // busy 一并计入，使重试/重新生成在乐观窗口内也占位。
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
    // 预览栏是宽度容器：竖屏画框的高度按栏宽换算（100cqw）。
    <div className="@container/preview relative flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3.5">
      <div className="flex items-center gap-1.5">
        <Film className="size-4 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-xs font-semibold text-subtle-foreground">{t("reference_preview_label")}</h3>
        <span className="flex-1" />
        {/* 上传是同一 unit 上的兄弟控件，与主 CTA 同步接线禁用：占用期间上传会与
            在跑的生成回写同一个成片文件 */}
        {onUploadVideo && (
          <UploadIconButton
            accept={UPLOAD_VIDEO_ACCEPT}
            label={t("media_upload_video")}
            busy={uploadingVideo}
            disabled={inFlight || busy || restoring}
            onSelect={(f) => void onUploadVideo(unit.unit_id, f)}
          />
        )}
        {/* 版本恢复同样写这个 unit 的成片文件，与上传、主 CTA 同步接线禁用：
            占用期间恢复旧版本会显示成功、随后被在跑的生成任务覆盖 */}
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
        <StatusBadge status={effectiveStatus} />
      </div>

      {/* 画框按项目画幅：竖屏 9:16、高度取 55dvh 与栏宽换算值中较小的一个；横屏 16:9、高度同样不超过 55dvh。
          画框不随栏高收缩，栏放不下时整栏滚动。 */}
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
          // 失败原因可能很长：画框里只留标题与前几行，全文放在画框下方。
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

      {/* 单元头部已用 alert 播报这一状态；这里只在生成按钮旁说明它为何不可用 */}
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
          generateDisabled={!hasNarrationText}
          generateDisabledHint={!hasNarrationText ? t("no_original_text") : undefined}
          estimatedCost={narrationEstimatedCost}
          onGenerate={onGenerateNarration ? () => onGenerateNarration(unit.unit_id) : undefined}
        />
      )}

      <section className="rounded-lg border border-border bg-card p-3">
        <h3 className="mb-2 text-xs font-medium text-muted-foreground">{t("reference_preview_metadata")}</h3>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-3.5 gap-y-1.5 text-xs">
          <dt className="text-muted-foreground">{t("reference_meta_unit")}</dt>
          <dd className="font-mono text-subtle-foreground" translate="no">
            {itemIdWithinEpisode(unit.unit_id)}
          </dd>
          <dt className="text-muted-foreground">{t("reference_meta_duration")}</dt>
          <dd className="font-mono tabular-nums text-subtle-foreground">
            {t("reference_editor_unit_meta", { duration: unit.duration_seconds })}
          </dd>
          <dt className="text-muted-foreground">{t("reference_meta_status")}</dt>
          <dd>
            <StatusBadge status={effectiveStatus} />
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
