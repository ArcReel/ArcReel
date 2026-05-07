import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { EpisodeHeader } from "../timeline/EpisodeHeader";
import { PreprocessingView } from "../timeline/PreprocessingView";
import { ShotSplitView } from "../timeline/ShotSplitView";
import { GridPreviewView } from "./GridPreviewView";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import type {
  EpisodeScript,
  NarrationEpisodeScript,
  DramaEpisodeScript,
  NarrationSegment,
  DramaScene,
  ProjectData,
} from "@/types";

type Segment = NarrationSegment | DramaScene;
type GridTab = "preprocessing" | "grid_preview" | "units";

interface GridImageToVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
  hasDraft?: boolean;
  episodeScript: EpisodeScript | null;
  scriptFile?: string;
  projectData: ProjectData | null;
  durationOptions?: number[];
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
    scriptFile?: string,
  ) => void | Promise<void>;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void;
  onGenerateGrid?: (episode: number, scriptFile: string, sceneIds?: string[]) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
}

export function GridImageToVideoCanvas({
  projectName,
  episode,
  episodeTitle,
  hasDraft,
  episodeScript,
  scriptFile,
  projectData,
  durationOptions,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  onGenerateGrid,
  onRestoreStoryboard,
  onRestoreVideo,
}: GridImageToVideoCanvasProps) {
  const { t } = useTranslation("dashboard");
  const contentMode = projectData?.content_mode ?? "narration";

  const hasScript = Boolean(episodeScript);
  const showTabs = Boolean(hasDraft);
  const defaultTab: GridTab = hasScript ? "units" : "preprocessing";
  const [activeTab, setActiveTab] = useState<GridTab>(defaultTab);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 脚本就绪时自动切到 units（与 TimelineCanvas 同行为）
    if (hasScript) setActiveTab("units");
  }, [hasScript]);

  const episodeCost = useCostStore((s) =>
    episodeScript ? s.getEpisodeCost(episodeScript.episode) : undefined,
  );
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);
  useEffect(() => {
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, episodeScript?.episode, debouncedFetch]);

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
          : ((episodeScript as DramaEpisodeScript).scenes ?? []),
    [contentMode, episodeScript, projectData],
  );

  const tasks = useTasksStore((s) => s.tasks);
  const isGenerating = useCallback(
    (taskType: "storyboard" | "video", segmentId: string): boolean =>
      tasks.some(
        (tk) =>
          tk.task_type === taskType &&
          tk.project_name === projectName &&
          tk.resource_id === segmentId &&
          (tk.status === "queued" || tk.status === "running"),
      ),
    [tasks, projectName],
  );
  const generatingStoryboard = useCallback(
    (segId: string) => isGenerating("storyboard", segId),
    [isGenerating],
  );
  const generatingVideo = useCallback(
    (segId: string) => isGenerating("video", segId),
    [isGenerating],
  );

  const invalidateGrids = useAppStore((s) => s.invalidateGrids);
  const [generatingAllGrids, setGeneratingAllGrids] = useState(false);
  const handleGenerateAllGrids = useCallback(() => {
    if (!onGenerateGrid || !scriptFile) return;
    setGeneratingAllGrids(true);
    onGenerateGrid(episode, scriptFile);
    setTimeout(() => {
      setGeneratingAllGrids(false);
      invalidateGrids();
    }, 3000);
  }, [onGenerateGrid, scriptFile, episode, invalidateGrids]);

  if (!projectData || (!episodeScript && !hasDraft)) {
    return (
      <div
        className="flex h-full items-center justify-center"
        style={{ color: "var(--color-text-4)" }}
      >
        {t("select_episode_hint")}
      </div>
    );
  }

  const totalDuration =
    episodeScript?.duration_seconds ??
    segments.reduce((sum, s) => sum + (s.duration_seconds ?? 0), 0);

  const currentEpisodeMeta = projectData?.episodes?.find((e) => e.episode === episode);
  const epMeta =
    currentEpisodeMeta ??
    ({
      episode,
      title: episodeTitle ?? episodeScript?.title ?? "",
      script_file: scriptFile ?? "",
      scenes_count: segments.length,
      duration_seconds: totalDuration,
      status: hasScript ? "in_production" : "draft",
    } as const);

  const handleUpdatePrompt = (
    segId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
  ) => onUpdatePrompt?.(segId, fieldOrPatch, value, scriptFile);
  const handleGenSb = (segId: string) => onGenerateStoryboard?.(segId, scriptFile);
  const handleGenVid = (segId: string) => onGenerateVideo?.(segId, scriptFile);

  const renderTabButton = (key: GridTab, label: string, disabled = false) => (
    <button
      type="button"
      onClick={() => !disabled && setActiveTab(key)}
      disabled={disabled}
      className="focus-ring relative px-3.5 py-2.5 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed"
      style={{
        color:
          activeTab === key
            ? "var(--color-text)"
            : disabled
              ? "var(--color-text-4)"
              : "var(--color-text-3)",
      }}
    >
      {label}
      {activeTab === key && (
        <span
          aria-hidden="true"
          className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded"
          style={{ background: "var(--color-accent)" }}
        />
      )}
    </button>
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <EpisodeHeader
        ep={epMeta}
        segmentCount={segments.length}
        totalDuration={totalDuration}
        episodeCost={episodeCost ?? undefined}
      />

      <div
        className="flex items-center gap-0.5 px-5"
        style={{
          borderBottom: "1px solid var(--color-hairline)",
          background: "oklch(0.19 0.012 250 / 0.5)",
        }}
      >
        {showTabs && renderTabButton("preprocessing", t("tab_preprocessing"))}
        {renderTabButton("grid_preview", t("tab_grid_preview"))}
        {renderTabButton("units", t("tab_timeline"), !hasScript)}
        <span className="flex-1" />

        {activeTab === "grid_preview" && hasScript && onGenerateGrid && scriptFile && (
          <div className="mr-1 inline-flex items-center gap-1.5">
            <button
              type="button"
              onClick={handleGenerateAllGrids}
              disabled={generatingAllGrids}
              className="sv-navbtn inline-flex items-center gap-1.5"
            >
              {generatingAllGrids ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="h-3 w-3" />
              )}
              <span>{generatingAllGrids ? t("submitting") : t("generate_all_grids")}</span>
            </button>
          </div>
        )}

        {activeTab === "units" && hasScript && (
          <div className="mr-1 inline-flex items-center gap-1.5">
            <button
              type="button"
              className="sv-navbtn inline-flex items-center gap-1.5"
              disabled
              title={t("batch_generate_videos")}
            >
              <Sparkles className="h-3 w-3" />
              <span>{t("batch_generate_videos")}</span>
            </button>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === "preprocessing" && hasDraft ? (
          <div className="h-full overflow-y-auto p-4">
            <PreprocessingView
              projectName={projectName}
              episode={episode}
              contentMode={contentMode}
            />
          </div>
        ) : activeTab === "grid_preview" ? (
          <GridPreviewView
            projectName={projectName}
            episode={episode}
            scriptFile={scriptFile}
            segments={segments}
            contentMode={contentMode}
            aspectRatio={aspectRatio}
            onGenerateGrid={onGenerateGrid}
          />
        ) : episodeScript && segments.length > 0 ? (
          <ShotSplitView
            segments={segments}
            contentMode={contentMode}
            aspectRatio={aspectRatio}
            projectName={projectName}
            isGridMode
            onUpdatePrompt={handleUpdatePrompt}
            onGenerateStoryboard={handleGenSb}
            onGenerateVideo={handleGenVid}
            onRestoreStoryboard={onRestoreStoryboard}
            onRestoreVideo={onRestoreVideo}
            generatingStoryboard={generatingStoryboard}
            generatingVideo={generatingVideo}
            durationOptions={durationOptions}
          />
        ) : null}
      </div>
    </div>
  );
}
