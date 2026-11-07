import { useId, useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { AspectFrame } from "@/components/canvas/shared/AspectFrame";
import { InlineWarning } from "@/components/shared/InlineWarning";
import { useModelCapabilities } from "@/hooks/useModelCapabilities";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import type { EditorContentMode } from "@/utils/script-shape";
import { errMsg } from "@/utils/async";
import { useItemPositionLabel } from "@/components/shared/episode-item/useEpisodeItem";
import { EndFramePicker } from "./EndFramePicker";

interface EndFrameRowProps {
  projectName: string;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  segmentId: string;
  scriptFile: string;
  contentMode: EditorContentMode;
  aspectRatio: "9:16" | "16:9";
  /** Current end-frame snapshot project-relative path, null when unset. */
  endFramePath: string | null;
  /** Read-only context, e.g. uneditable scripts: display only, no writes. */
  readOnly?: boolean;
  /** Report submission occupancy so parents disable video generation/upload/restoration on the same card. */
  onSubmittingChange?: (submitting: boolean) => void;
  /** Video-card manual upload disables this row's writes to avoid conflicts in shared asset persistence. */
  videoUploadBusy?: boolean;
  /** Shot edits saving: save-and-generate immediately enqueues video afterward; changing end frames meanwhile would use old inputs. */
  shotSaving?: boolean;
}

/**
 * End-frame settings row: collapsed summary unset/set, expanded preview/explanation/replace/clear.
 *
 * Three occupancy checks follow repository rules: video-task occupancy disables both writes together (opening check +
 * sibling coordination), then direct-store recheck on submission because occupancy may change while open.
 * Source-image occupancy is irrelevant: snapshots are copies, independent of source generation, storyboard/grid tasks.
 *
 * Unsupported capabilities do not disable controls; persistent inline warnings explain instead. With an existing end frame
 * and unsupported model, show warning plus one-click clear below collapsed headers, visible even when closed:
 * backend execution would reject generation, and hiding warnings would reveal this only through individual failures. useModelCapabilities
 * supplies effective `lastFrame` including overrides, without frontend-owned criteria. Failed queries mean unknown,
 * not falsely unsupported. Model/override changes invalidate/refetch automatically; warnings do not depend on expanded panels.
 */
export function EndFrameRow({
  projectName,
  lastFrame,
  capabilitiesLoading,
  segmentId,
  scriptFile,
  contentMode,
  aspectRatio,
  endFramePath,
  readOnly = false,
  onSubmittingChange,
  videoUploadBusy = false,
  shotSaving = false,
}: EndFrameRowProps) {
  const { t } = useTranslation("dashboard");
  const itemLabel = useItemPositionLabel(segmentId);
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const viewOnly = useDemoWorkbench() || readOnly;

  const standaloneCapabilities = useModelCapabilities({ projectName, enabled: lastFrame === undefined });
  const effectiveLastFrame = lastFrame === undefined ? standaloneCapabilities.lastFrame : lastFrame;
  const capsLoading = capabilitiesLoading ?? standaloneCapabilities.loading;

  // Unfetched capabilities (loading/failure) are not unsupported; gate only explicit false.
  const unsupported = effectiveLastFrame === false;

  const videoBusyIds = useActiveResourceIds("video", projectName);
  // Occupancy ignores source (running queue/video upload): both write the same project.json,
  // so forbid concurrent writes. Unsupported capability is separate: warn only, never disable controls.
  const videoBusy = videoBusyIds.has(segmentId) || videoUploadBusy;
  const fp = useProjectsStore((s) => (endFramePath ? s.getAssetFingerprint(endFramePath) : null));

  // Sibling coordination: replace/clear/picker submission share this value.
  // Occupancy only: unsupported/unknown capabilities never gate. If unsupported does not block,
  // neither should waiting, which would transiently disable writes on model switches.
  const controlsDisabled = videoBusy || shotSaving || submitting || viewOnly;

  // Hover reasons for disabled controls.
  const disabledHint = viewOnly
    ? undefined
    : videoBusy
      ? t("end_frame_busy_hint")
      : shotSaving
        ? t("common:save_status_saving")
        : undefined;

  // Warn only for set end frames and explicitly unsupported models; unset shots have nothing rejectable to warn about.
  const showUnsupportedNotice = unsupported && !!endFramePath;

  /**
   * Fresh submit-time occupancy recheck: shots may queue after panel/picker opening; opening-only checks leave
   * a race. Reject matches with visible feedback.
   */
  const rejectIfDisabled = (): boolean => {
    if (shotSaving) {
      useAppStore.getState().pushToast(t("common:save_status_saving"), "info");
      return true;
    }
    if (videoUploadBusy) {
      useAppStore.getState().pushToast(t("end_frame_busy_hint"), "info");
      return true;
    }
    if (!isResourceBusy("video", projectName, segmentId)) return false;
    useAppStore.getState().pushToast(t("end_frame_busy_hint"), "info");
    return true;
  };

  const updateSubmitting = (value: boolean) => {
    setSubmitting(value);
    onSubmittingChange?.(value);
  };

  // End-frame set/clear execute immediately without success toasts.
  const runWrite = async (action: () => Promise<unknown>) => {
    if (rejectIfDisabled()) return;
    updateSubmitting(true);
    try {
      await action();
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("end_frame_action_failed", { message: errMsg(err) }), "error");
      return;
    } finally {
      updateSubmitting(false);
    }
    setPickerOpen(false);
    // Snapshot paths are fixed/overwritten in place; refetch project data for new fingerprints/cache-busting.
    // refreshProject absorbs request failures and returns outcomes, never rejects; writes already succeeded,
    // so report refresh failures separately, not as end-frame write failures. Project-switch cancellation is not failure;
    // skip silently, avoiding false refresh errors after setting then immediately switching projects.
    const refreshResult = await useProjectsStore.getState().refreshProject(projectName);
    if (refreshResult === "failed") {
      useAppStore.getState().pushToast(t("end_frame_refresh_failed"), "error");
    }
  };

  const handlePickProjectImage = (sourcePath: string) =>
    void runWrite(() => API.selectEndFrame(projectName, segmentId, scriptFile, sourcePath));

  const handlePickUpload = (file: File) =>
    void runWrite(() => API.uploadEndFrame(projectName, segmentId, scriptFile, file));

  const handleClear = () =>
    void runWrite(() => API.clearEndFrame(projectName, segmentId, scriptFile));

  // Summary/expanded previews are small; fetch 160-width thumbnails.
  const previewUrl = endFramePath ? API.getFileUrl(projectName, endFramePath, fp, { width: 160 }) : null;

  // Summaries state only set/unset; unsupported warnings below must not replace set indicators,
  // otherwise users cannot see an existing end frame awaiting clearing.
  const summary = capsLoading
    ? t("end_frame_capability_checking")
    : endFramePath
      ? t("end_frame_summary_set")
      : t("end_frame_summary_unset");

  return (
    <div className="mb-2.5 rounded-lg border border-border bg-muted/30">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        aria-controls={panelId}
        className="group/end-frame focus-ring flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left"
      >
        <ChevronRight
          aria-hidden
          className="size-3.5 text-muted-foreground transition-transform group-aria-expanded/end-frame:rotate-90"
        />
        <span className="text-xs font-medium text-subtle-foreground">{t("end_frame_title")}</span>
        <span className="flex-1" />
        {previewUrl && (
          <img
            src={previewUrl}
            alt=""
            aria-hidden
            decoding="async"
            className="h-4 w-2.5 rounded-xs border border-primary/25 object-cover"
          />
        )}
        <span
          // Summaries change asynchronously with capability queries (checking → set/unset); screen readers must follow.
          aria-live="polite"
          className={cn("text-xs", endFramePath ? "text-primary" : "text-muted-foreground")}
        >
          {summary}
        </span>
      </button>

      {/* Persistent collapsed-visible warnings reveal switching to unsupported models after setting end frames before enqueue. */}
      {showUnsupportedNotice && (
        <InlineWarning
          className="px-3 pb-2"
          message={t("end_frame_unsupported_notice")}
          action={
            viewOnly
              ? undefined
              : {
                  // Expanded panels have another clear button visible too; use full naming here
                  // so screen readers distinguish both buttons.
                  label: t("end_frame_clear_end_frame"),
                  onClick: handleClear,
                  disabled: controlsDisabled,
                  title: disabledHint,
                }
          }
        />
      )}

      {expanded && (
        <div id={panelId} className="flex items-start gap-3 border-t border-border/50 px-3 pt-2.5 pb-3">
          <div
            className={cn(
              "w-16 shrink-0 overflow-hidden rounded-sm border",
              previewUrl ? "border-primary/25" : "border-dashed border-input bg-muted/50",
            )}
          >
            <AspectFrame ratio={aspectRatio}>
              {previewUrl ? (
                <img
                  src={previewUrl}
                  alt={t("end_frame_preview_alt", { id: itemLabel })}
                  className="size-full object-cover"
                  decoding="async"
                />
              ) : (
                <div className="grid size-full place-items-center text-xs text-muted-foreground">
                  {t("end_frame_summary_unset")}
                </div>
              )}
            </AspectFrame>
          </div>
          <div className="flex flex-1 flex-col gap-2">
            <p className="text-xs leading-relaxed text-muted-foreground">{t("end_frame_description")}</p>
            {/* Expanded panels explain recovery through models/overrides, complementing warning consequences/clear;
                include even when unset so users know this model cannot use end frames before setting them. */}
            {unsupported && (
              <p className="text-xs leading-relaxed text-muted-foreground">{t("end_frame_unsupported_hint")}</p>
            )}
            {!viewOnly && (
              <div className="flex items-center gap-1.5">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (rejectIfDisabled()) return;
                    setPickerOpen(true);
                  }}
                  disabled={controlsDisabled}
                  title={disabledHint}
                >
                  {endFramePath ? t("end_frame_replace") : t("end_frame_choose")}
                </Button>
                {endFramePath && (
                  <Button variant="ghost" size="sm" onClick={handleClear} disabled={controlsDisabled} title={disabledHint}>
                    {t("end_frame_clear")}
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {pickerOpen && (
        <EndFramePicker
          projectName={projectName}
          scriptFile={scriptFile}
          contentMode={contentMode}
          aspectRatio={aspectRatio}
          submitting={submitting}
          disabled={videoBusy}
          onClose={() => setPickerOpen(false)}
          onPickProjectImage={handlePickProjectImage}
          onPickUpload={handlePickUpload}
        />
      )}
    </div>
  );
}
