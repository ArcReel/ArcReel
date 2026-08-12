import { useTranslation } from "react-i18next";
import { Film, Loader2, Sparkles, RotateCcw, AlertTriangle } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { UPLOAD_VIDEO_ACCEPT, UploadIconButton } from "@/components/ui/UploadIconButton";
import { formatCost } from "@/utils/cost-format";
import { StatusBadge, resolveUnitStatus } from "./unit-status";
import type { CostBreakdown, ReferenceVideoUnit, UnitStatus } from "@/types";

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
   * status 的乐观分支只在无任务行时生效（保持 cancelling 不显示为生成中），
   * 重试与重新生成这两条路径上旧任务行始终在，仅看 status 会在乐观窗口内漏禁用。
   */
  busy?: boolean;
  /** 最新任务行是否处于取消中——占用集会计入 cancelling，但不应展示为「生成中」。 */
  cancelling?: boolean;
  /** Estimated cost for this unit (optional; rendered next to the CTA). */
  estimatedCost?: CostBreakdown;
  /** Actual already-spent cost; rendered in the metadata block. */
  actualCost?: CostBreakdown;
  onGenerate?: (unitId: string) => void;
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
  cancelling = false,
  estimatedCost,
  actualCost,
  onGenerate,
  generationBlocked = false,
  onUploadVideo,
  uploadingVideo,
  restoring = false,
  onRestoringChange,
  checkBusy,
  onRestored,
}: UnitPreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const clip = unit?.generated_assets.video_clip ?? null;
  // 上传/还原后路径不变，靠 fingerprint cache-bust 让 <video> 重新拉取
  const clipFp = useProjectsStore((s) => (clip ? s.getAssetFingerprint(clip) : null));

  if (!unit) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-[var(--color-text-4)]">
        {t("reference_preview_empty")}
      </div>
    );
  }

  const effectiveStatus = status ?? resolveUnitStatus(unit);
  const videoUrl = clip && projectName ? API.getFileUrl(projectName, clip, clipFp) : null;

  // 状态先于 video_clip 落库的窗口里，effectiveStatus==="ready" 但 videoUrl
  // 还为 null —— 这种情况下走 inFlight 占位避免空白面板。
  const ready = effectiveStatus === "ready" && Boolean(videoUrl);
  const failed = effectiveStatus === "failed";
  // busy 一并计入，使重试/重新生成在乐观窗口内也占位；但 cancelling 时排除在外——
  // 取消中不是「生成中」，展示层沿用取消前的状态，仅按钮仍需保持禁用（见下方 disabled）。
  const inFlight =
    (busy && !cancelling) ||
    effectiveStatus === "running" ||
    (effectiveStatus === "ready" && !videoUrl);

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
        {/* 上传是同一 unit 上的兄弟控件，与主 CTA 同步接线禁用：cancelling 期间
            inFlight 为假但占用仍在，上传会与在跑的生成回写同一个成片文件 */}
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

        {failed && !inFlight && (
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
          disabled={inFlight || busy || restoring || generationBlocked}
          className={`focus-ring inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2.5 text-sm font-semibold transition-colors ${
            inFlight || busy || restoring || generationBlocked
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

      {generationBlocked && (
        <p role="alert" className="text-xs text-amber-300">
          {t("reference_needs_replan")}
        </p>
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
