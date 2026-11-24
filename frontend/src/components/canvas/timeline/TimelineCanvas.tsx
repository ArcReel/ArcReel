import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScriptReviewGate } from "./ScriptReviewGate";
import { PromptAuthoringButton } from "@/components/canvas/shared/PromptAuthoringButton";
import { RetainedEditUnit } from "@/components/shared/edit-unit/RetainedEditUnit";
import { ShotSplitView } from "./ShotSplitView";
import { StoryboardBatchDialog } from "./StoryboardBatchDialog";
import { EmptyScriptState } from "./EmptyScriptState";
import type { InsertShotHandler } from "./ShotStructureActions";
import { AdScriptButton, AdScriptProgress } from "@/components/canvas/shared/AdScriptDialog";
import { NoScriptBlankState } from "@/components/canvas/shared/StartBlankScriptButton";
import { BatchFillButton, useBatchGap } from "@/components/canvas/episode-page/BatchFillButton";
import { EpisodeHeaderActions } from "@/components/canvas/episode-page/EpisodeHeaderActions";
import type { EpisodeCanvasContext } from "@/components/canvas/episode-page/EpisodePage";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { getScriptItemId } from "@/utils/script-shape";
import { previewAspect } from "@/utils/preview-aspect";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import type {
  EpisodeScript,
  NarrationEpisodeScript,
  DramaEpisodeScript,
  AdEpisodeScript,
  NarrationSegment,
  DramaScene,
  AdShot,
  ProjectData,
  StoryboardBatchKind,
} from "@/types";

type Segment = NarrationSegment | DramaScene | AdShot;

interface TimelineCanvasProps extends EpisodeCanvasContext {
  projectName: string;
  episode: number;
  hasDraft?: boolean;
  episodeScript: EpisodeScript | null;
  scriptFile?: string;
  projectData: ProjectData | null;
  onUpdatePrompt?: (segmentId: string, patch: Record<string, unknown>, scriptFile?: string) => Promise<boolean>;
  onMoveShot?: (shotId: string, afterId: string | null, scriptFile?: string) => Promise<boolean>;
  onInsertShot?: (afterId: string | null, novelText: string | undefined, scriptFile?: string) => Promise<boolean>;
  onRemoveShot?: (itemId: string, scriptFile?: string) => Promise<boolean>;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string, scriptFile?: string) => void;
  onGenerateEpisodeNarration?: (scriptFile?: string) => void;
  durationOptions?: number[];
  planDurationOptions?: number[];
  durationEndpointFixed?: boolean;
  videoModelUnresolved?: boolean;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  onRestoreStoryboard?: () => Promise<unknown> | void;
  onRestoreVideo?: () => Promise<unknown> | void;
}

const DEMO_READ_ONLY_PROPS = {
  onUpdatePrompt: undefined,
  onMoveShot: undefined,
  onInsertShot: undefined,
  onRemoveShot: undefined,
  onGenerateNarration: undefined,
  onGenerateEpisodeNarration: undefined,
} as const satisfies Partial<TimelineCanvasProps>;

export function TimelineCanvas(props: TimelineCanvasProps) {
  const demoReadOnly = useDemoWorkbench();
  const {
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
    onMoveShot,
    onInsertShot,
    onRemoveShot,
    onGenerateStoryboard,
    onGenerateVideo,
    onGenerateNarration,
    onGenerateEpisodeNarration,
    onRestoreStoryboard,
    onRestoreVideo,
  } = demoReadOnly ? { ...props, ...DEMO_READ_ONLY_PROPS } : props;

  const { t } = useTranslation("dashboard");
  const contentMode = projectData?.content_mode ?? "narration";
  const editorContentMode: "narration" | "drama" | "ad" =
    contentMode === "narration" ? "narration" : contentMode === "ad" ? "ad" : "drama";

  const hasScript = Boolean(episodeScript);
  const [batchKind, setBatchKind] = useState<StoryboardBatchKind | null>(null);
  const storyboardGap = useBatchGap(projectName, episode, "storyboards", "storyboard");
  const videoGap = useBatchGap(projectName, episode, "videos", "video");
  const narrationGap = useBatchGap(projectName, episode, "narration", "tts");

  const aspectRatio = previewAspect(projectData);

  const segments = useMemo<Segment[]>(
    () =>
      !episodeScript || !projectData
        ? []
        : contentMode === "narration"
          ? ((episodeScript as NarrationEpisodeScript).segments ?? [])
          : contentMode === "ad"
            ? ((episodeScript as AdEpisodeScript).shots ?? [])
            : contentMode === "drama"
              ? ((episodeScript as DramaEpisodeScript).scenes ?? [])
              : [],
    [contentMode, episodeScript, projectData],
  );

  const storyboardBusyIds = useActiveResourceIds("storyboard", projectName);
  const videoBusyIds = useActiveResourceIds("video", projectName);
  const ttsBusyIds = useActiveResourceIds("tts", projectName);
  const generatingStoryboard = useCallback(
    (segId: string) => storyboardBusyIds.has(segId),
    [storyboardBusyIds],
  );
  const generatingVideo = useCallback(
    (segId: string) => videoBusyIds.has(segId),
    [videoBusyIds],
  );
  const generatingNarration = useCallback(
    (segId: string) => ttsBusyIds.has(segId),
    [ttsBusyIds],
  );
  const currentSegmentIds = useMemo(
    () => new Set(segments.map((s) => getScriptItemId(s, editorContentMode))),
    [segments, editorContentMode],
  );
  const narrationBatchBusy = useMemo(
    () => [...currentSegmentIds].some((id) => ttsBusyIds.has(id)),
    [ttsBusyIds, currentSegmentIds],
  );

  if (projectData && !episodeScript && !hasDraft && editorContentMode === "ad" && !demoReadOnly) {
    return <NoScriptBlankState projectName={projectName} episode={episode} className="h-full text-sm" />;
  }

  if (!projectData || (!episodeScript && !hasDraft)) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t("select_episode_hint")}
      </div>
    );
  }

  const handleUpdatePrompt = onUpdatePrompt
    ? (segId: string, patch: Record<string, unknown>) => onUpdatePrompt(segId, patch, scriptFile)
    : undefined;
  const handleMoveShot = onMoveShot
    ? (shotId: string, afterId: string | null) => onMoveShot(shotId, afterId, scriptFile)
    : undefined;
  const handleInsertShot: InsertShotHandler | undefined = onInsertShot
    ? (afterId, novelText) => onInsertShot(afterId, novelText, scriptFile)
    : undefined;
  const handleRemoveShot = onRemoveShot
    ? (itemId: string) => onRemoveShot(itemId, scriptFile)
    : undefined;
  const handleGenSb = onGenerateStoryboard
    ? (segId: string) => onGenerateStoryboard(segId, scriptFile)
    : undefined;
  const handleGenVid = onGenerateVideo
    ? (segId: string) => onGenerateVideo(segId, scriptFile)
    : undefined;
  const handleGenNarration = onGenerateNarration
    ? (segId: string) => onGenerateNarration(segId, scriptFile)
    : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {view === "board" && hasScript && (
        <EpisodeHeaderActions>
          {editorContentMode === "ad" && !demoReadOnly && (
            <AdScriptButton projectName={projectName} episode={episode} regenerate />
          )}
          <PromptAuthoringButton projectName={projectName} episode={episode} scope="pending" />
          <BatchFillButton
            kind="storyboards"
            count={storyboardGap}
            disabled={demoReadOnly}
            onClick={() => setBatchKind("storyboards")}
          />
          <BatchFillButton
            kind="videos"
            count={videoGap}
            disabled={demoReadOnly}
            onClick={() => setBatchKind("videos")}
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

      {batchKind && (
        <StoryboardBatchDialog
          projectName={projectName}
          episode={episode}
          kind={batchKind}
          onClose={() => setBatchKind(null)}
        />
      )}

      {editorContentMode === "ad" && hasScript && (
        <AdScriptProgress projectName={projectName} episode={episode} noScript={false} className="mx-4 mt-3" />
      )}

      <div className="flex min-h-0 flex-1 flex-col" data-onboarding={ONBOARDING_ANCHORS.workbenchTimeline}>
        <RetainedEditUnit identity={episodeScript && segments.length > 0 ? "shots" : "empty"} message={t("shot_externally_removed")} value={view === "plan" && hasDraft && editorContentMode !== "ad" ? (
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
        ) : episodeScript && segments.length > 0 ? (
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
        ) : episodeScript && contentMode === editorContentMode ? (
          <EmptyScriptState contentMode={editorContentMode} onInsert={handleInsertShot} />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {hasScript ? t("timeline_no_editable_segments") : t("timeline_script_not_ready")}
          </div>
        )}>
          {(content) => content}
        </RetainedEditUnit>
      </div>
    </div>
  );
}
