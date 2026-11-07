import { useCallback, useMemo, useState } from "react";
import { Sparkles, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { BatchFillButton, useBatchGap } from "../episode-page/BatchFillButton";
import { EpisodeHeaderActions } from "../episode-page/EpisodeHeaderActions";
import type { EpisodeCanvasContext } from "../episode-page/EpisodePage";
import { ScriptReviewGate } from "../timeline/ScriptReviewGate";
import { PromptAuthoringButton } from "../shared/PromptAuthoringButton";
import { RetainedEditUnit } from "@/components/shared/edit-unit/RetainedEditUnit";
import { ShotSplitView } from "../timeline/ShotSplitView";
import { EmptyScriptState } from "../timeline/EmptyScriptState";
import { StoryboardBatchDialog } from "../timeline/StoryboardBatchDialog";
import type { InsertShotHandler } from "../timeline/ShotStructureActions";
import { GridPreviewView } from "./GridPreviewView";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useActiveResourceIds, useHasActiveTaskForScriptFile } from "@/stores/tasks-store";
import { getScriptItemId } from "@/utils/script-shape";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import type {
  EpisodeScript,
  NarrationEpisodeScript,
  DramaEpisodeScript,
  NarrationSegment,
  DramaScene,
  ProjectData,
} from "@/types";

type Segment = NarrationSegment | DramaScene;

interface GridImageToVideoCanvasProps extends EpisodeCanvasContext {
  projectName: string;
  episode: number;
  hasDraft?: boolean;
  episodeScript: EpisodeScript | null;
  scriptFile?: string;
  projectData: ProjectData | null;
  durationOptions?: number[];
  /** Content-confirmation script-plan duration tiers, differing from `durationOptions` when endpoints fix duration. */
  planDurationOptions?: number[];
  durationEndpointFixed?: boolean;
  videoModelUnresolved?: boolean;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  /** Cause classification for saved out-of-range durations; omitted makes ShotDetail use generic warnings without cause distinction. */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  /** Save shot fields, throwing on failure; resolves whether local scripts refreshed to persisted contents. */
  onUpdatePrompt?: (segmentId: string, patch: Record<string, unknown>, scriptFile?: string) => Promise<boolean>;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string, scriptFile?: string) => void;
  onGenerateEpisodeNarration?: (scriptFile?: string) => void;
  onGenerateGrid?: (
    episode: number,
    scriptFile: string,
    sceneIds?: string[],
  ) => Promise<void> | void;
  onRestoreStoryboard?: () => Promise<unknown> | void;
  onRestoreVideo?: () => Promise<unknown> | void;
  /** Reorder shots after afterId, null to front; resolves success. */
  onMoveShot?: (shotId: string, afterId: string | null, scriptFile?: string) => Promise<boolean>;
  /** Add a shot, including narration body; null afterId appends; resolves success. */
  onInsertShot?: (afterId: string | null, novelText: string | undefined, scriptFile?: string) => Promise<boolean>;
  /** Remove a shot; resolves success. */
  onRemoveShot?: (itemId: string, scriptFile?: string) => Promise<boolean>;
}

export function GridImageToVideoCanvas({
  projectName,
  episode,
  view,
  onViewChange,
  hasDraft,
  episodeScript,
  scriptFile,
  projectData,
  durationOptions,
  planDurationOptions,
  durationEndpointFixed,
  videoModelUnresolved,
  lastFrame,
  capabilitiesLoading,
  durationWarningReason,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  onGenerateNarration,
  onGenerateEpisodeNarration,
  onGenerateGrid,
  onRestoreStoryboard,
  onRestoreVideo,
  onMoveShot,
  onInsertShot,
  onRemoveShot,
}: GridImageToVideoCanvasProps) {
  const { t } = useTranslation("dashboard");
  const contentMode = projectData?.content_mode ?? "narration";
  // Grid canvas serves narration/drama only; ad excludes grid-to-video;
  // narrow subview types explicitly exclude ad instead of drama fallback.
  // Unknown/corrupt content_mode retains historical drama fallback; only ad is explicitly excluded.
  const editorContentMode: "narration" | "drama" | null =
    contentMode === "narration" ? "narration" : contentMode === "ad" ? null : "drama";

  const hasScript = Boolean(episodeScript);
  const [videoBatchOpen, setVideoBatchOpen] = useState(false);
  const demoReadOnly = useDemoWorkbench();
  const videoGap = useBatchGap(projectName, episode, "videos", "video");
  const narrationGap = useBatchGap(projectName, episode, "narration", "tts");

  const rawAspect =
    typeof projectData?.aspect_ratio === "string"
      ? projectData.aspect_ratio
      : (projectData?.aspect_ratio?.storyboard ??
        (contentMode === "narration" ? "9:16" : "16:9"));
  const aspectRatio: "9:16" | "16:9" =
    rawAspect === "9:16" || rawAspect === "16:9" ? rawAspect : "16:9";

  const segments = useMemo<Segment[]>(
    () =>
      !episodeScript || !projectData
        ? []
        : contentMode === "narration"
          ? ((episodeScript as NarrationEpisodeScript).segments ?? [])
          : contentMode === "drama"
            ? ((episodeScript as DramaEpisodeScript).scenes ?? [])
            : [],
    [contentMode, episodeScript, projectData],
  );

  // Task-derived loading delegates active/latest-row rules to store selectors, one active-resource set per task_type.
  const storyboardBusyIds = useActiveResourceIds("storyboard", projectName);
  const videoBusyIds = useActiveResourceIds("video", projectName);
  const ttsBusyIds = useActiveResourceIds("tts", projectName);
  // Running episode grid tasks overwrite multiple storyboard files during splitting; grid task
  // resource_id is grid_id, outside per-shot resource_id storyboardBusyIds,
  // so use scriptFile coarse occupancy, disabling shot editing during grid assembly to avoid concurrent file writes.
  const gridActiveForEpisode = useHasActiveTaskForScriptFile("grid", scriptFile, projectName);
  const generatingStoryboard = useCallback(
    (segId: string) => storyboardBusyIds.has(segId) || gridActiveForEpisode,
    [storyboardBusyIds, gridActiveForEpisode],
  );
  const generatingVideo = useCallback(
    (segId: string) => videoBusyIds.has(segId),
    [videoBusyIds],
  );
  const generatingNarration = useCallback(
    (segId: string) => ttsBusyIds.has(segId),
    [ttsBusyIds],
  );
  // Batch narration: disable while any episode TTS task remains unfinished to avoid duplicate enqueueing.
  const currentSegmentIds = useMemo(
    () => new Set(segments.map((s) => getScriptItemId(s, editorContentMode ?? "drama"))),
    [segments, editorContentMode],
  );
  const narrationBatchBusy = useMemo(
    () => [...currentSegmentIds].some((id) => ttsBusyIds.has(id)),
    [ttsBusyIds, currentSegmentIds],
  );

  const invalidateGrids = useAppStore((s) => s.invalidateGrids);
  const [generatingAllGrids, setGeneratingAllGrids] = useState(false);
  const handleGenerateAllGrids = useCallback(async () => {
    if (!onGenerateGrid || !scriptFile) return;
    setGeneratingAllGrids(true);
    try {
      await onGenerateGrid(episode, scriptFile);
    } finally {
      setGeneratingAllGrids(false);
      invalidateGrids();
    }
  }, [onGenerateGrid, scriptFile, episode, invalidateGrids]);

  if (!projectData || (!episodeScript && !hasDraft)) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        {t("select_episode_hint")}
      </div>
    );
  }

  const handleUpdatePrompt = onUpdatePrompt
    ? (segId: string, patch: Record<string, unknown>) => onUpdatePrompt(segId, patch, scriptFile)
    : undefined;
  const handleGenSb = (segId: string) => onGenerateStoryboard?.(segId, scriptFile);
  const handleGenVid = (segId: string) => onGenerateVideo?.(segId, scriptFile);
  const handleGenNarration = onGenerateNarration
    ? (segId: string) => onGenerateNarration(segId, scriptFile)
    : undefined;
  // Structural actions match timelines; read-only demos expose none.
  const handleMoveShot =
    onMoveShot && !demoReadOnly
      ? (shotId: string, afterId: string | null) => onMoveShot(shotId, afterId, scriptFile)
      : undefined;
  const handleInsertShot: InsertShotHandler | undefined =
    onInsertShot && !demoReadOnly
      ? (afterId, novelText) => onInsertShot(afterId, novelText, scriptFile)
      : undefined;
  const handleRemoveShot =
    onRemoveShot && !demoReadOnly ? (itemId: string) => onRemoveShot(itemId, scriptFile) : undefined;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {view === "grid" && hasScript && onGenerateGrid && scriptFile && (
        <EpisodeHeaderActions>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleGenerateAllGrids()}
                  disabled={generatingAllGrids}
                />
              }
            >
              {generatingAllGrids ? (
                <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
              ) : (
                <Sparkles aria-hidden data-icon="inline-start" />
              )}
              {generatingAllGrids ? t("submitting") : t("generate_all_grids")}
            </TooltipTrigger>
            <TooltipContent>{t("generate_all_grids_hint")}</TooltipContent>
          </Tooltip>
        </EpisodeHeaderActions>
      )}

      {view === "board" && hasScript && (
        <EpisodeHeaderActions>
          <PromptAuthoringButton projectName={projectName} episode={episode} scope="pending" />
          <BatchFillButton
            kind="videos"
            count={videoGap}
            disabled={demoReadOnly}
            onClick={() => setVideoBatchOpen(true)}
          />
          {contentMode === "narration" && onGenerateEpisodeNarration && (
            <BatchFillButton
              kind="narration"
              count={narrationGap}
              disabled={narrationBatchBusy}
              onClick={() => onGenerateEpisodeNarration(scriptFile)}
            />
          )}
        </EpisodeHeaderActions>
      )}

      {videoBatchOpen && (
        <StoryboardBatchDialog
          projectName={projectName}
          episode={episode}
          kind="videos"
          onClose={() => setVideoBatchOpen(false)}
        />
      )}

      <div className="min-h-0 flex-1 overflow-hidden">
        <RetainedEditUnit identity={episodeScript && segments.length > 0 ? "shots" : "empty"} message={t("shot_externally_removed")} value={view === "plan" && hasDraft && editorContentMode ? (
          <div className="relative h-full overflow-y-auto p-4">
            <ScriptReviewGate
              key={`${projectName}:${episode}`}
              projectName={projectName}
              episode={episode}
              contentMode={editorContentMode}
              videoModelUnresolved={videoModelUnresolved}
              durationOptions={planDurationOptions}
              durationEndpointFixed={durationEndpointFixed}
              durationWarningReason={durationWarningReason}
              onOpenTimeline={hasScript ? () => onViewChange("board") : undefined}
            />
          </div>
        ) : view === "grid" && editorContentMode ? (
          <GridPreviewView
            projectName={projectName}
            episode={episode}
            scriptFile={scriptFile}
            segments={segments}
            contentMode={editorContentMode}
            onGenerateGrid={onGenerateGrid}
          />
        ) : episodeScript && segments.length > 0 && editorContentMode ? (
          <ShotSplitView
            segments={segments}
            contentMode={editorContentMode}
            aspectRatio={aspectRatio}
            projectName={projectName}
            episode={episode}
            scriptFile={scriptFile}
            onUpdatePrompt={handleUpdatePrompt}
            onMoveShot={handleMoveShot}
            onInsertShot={handleInsertShot}
            onRemoveShot={handleRemoveShot}
            onGenerateStoryboard={handleGenSb}
            onGenerateVideo={handleGenVid}
            onGenerateNarration={handleGenNarration}
            onRestoreStoryboard={onRestoreStoryboard}
            onRestoreVideo={onRestoreVideo}
            generatingStoryboard={generatingStoryboard}
            generatingVideo={generatingVideo}
            generatingNarration={generatingNarration}
            durationOptions={durationOptions}
            durationEndpointFixed={durationEndpointFixed}
            lastFrame={lastFrame}
            capabilitiesLoading={capabilitiesLoading}
            durationWarningReason={durationWarningReason}
          />
        ) : episodeScript && editorContentMode ? (
          <EmptyScriptState
            contentMode={editorContentMode}
            onInsert={handleInsertShot}
          />
        ) : null}>
          {(content) => content}
        </RetainedEditUnit>
      </div>
    </div>
  );
}
