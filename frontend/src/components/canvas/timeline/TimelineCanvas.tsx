import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PreprocessingView } from "./PreprocessingView";
import { ShotSplitView } from "./ShotSplitView";
import { EpisodeHeader } from "./EpisodeHeader";
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

interface TimelineCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
  hasDraft?: boolean;
  episodeScript: EpisodeScript | null;
  scriptFile?: string;
  projectData: ProjectData | null;
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
    scriptFile?: string,
  ) => void | Promise<void>;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void;
  durationOptions?: number[];
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
}

export function TimelineCanvas({
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
  onRestoreStoryboard,
  onRestoreVideo,
}: TimelineCanvasProps) {
  const { t } = useTranslation("dashboard");
  const contentMode = projectData?.content_mode ?? "narration";

  const hasScript = Boolean(episodeScript);
  const showTabs = Boolean(hasDraft);
  const defaultTab = hasScript ? "timeline" : "preprocessing";
  const [activeTab, setActiveTab] = useState<"preprocessing" | "timeline">(defaultTab);

  // Auto-switch to timeline when script becomes available
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- script 就绪时自动切到 timeline tab，是 navigation 驱动的有意切换
    if (hasScript) setActiveTab("timeline");
  }, [hasScript]);

  const episodeCost = useCostStore((s) =>
    episodeScript ? s.getEpisodeCost(episodeScript.episode) : undefined,
  );
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);

  useEffect(() => {
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, episodeScript?.episode, debouncedFetch]);

  // 解析 aspect ratio（仅支持 9:16 / 16:9 两档，3:4/1:1 也回退到 16:9）
  const rawAspect =
    typeof projectData?.aspect_ratio === "string"
      ? projectData.aspect_ratio
      : projectData?.aspect_ratio?.storyboard ??
        (contentMode === "narration" ? "9:16" : "16:9");
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

  // 任务派生 loading
  const tasks = useTasksStore((s) => s.tasks);
  const isGenerating = useCallback(
    (taskType: "storyboard" | "video", segmentId: string): boolean =>
      tasks.some(
        (t) =>
          t.task_type === taskType &&
          t.project_name === projectName &&
          t.resource_id === segmentId &&
          (t.status === "queued" || t.status === "running"),
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

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 集 header */}
      <EpisodeHeader
        ep={epMeta}
        segmentCount={segments.length}
        totalDuration={totalDuration}
        episodeCost={episodeCost ?? undefined}
      />

      {/* Tab bar + 批量按钮 */}
      <div
        className="flex items-center gap-0.5 px-5"
        style={{
          borderBottom: "1px solid var(--color-hairline)",
          background: "oklch(0.19 0.012 250 / 0.5)",
        }}
      >
        {showTabs && (
          <button
            type="button"
            onClick={() => setActiveTab("preprocessing")}
            className="relative px-3.5 py-2.5 text-[12.5px] font-medium transition-colors focus-ring"
            style={{
              color:
                activeTab === "preprocessing"
                  ? "var(--color-text)"
                  : "var(--color-text-3)",
            }}
          >
            {t("tab_preprocessing")}
            {activeTab === "preprocessing" && (
              <span
                aria-hidden="true"
                className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded"
                style={{ background: "var(--color-accent)" }}
              />
            )}
          </button>
        )}
        <button
          type="button"
          onClick={() => hasScript && setActiveTab("timeline")}
          disabled={!hasScript}
          className="relative px-3.5 py-2.5 text-[12.5px] font-medium transition-colors focus-ring disabled:cursor-not-allowed"
          style={{
            color:
              activeTab === "timeline"
                ? "var(--color-text)"
                : !hasScript
                  ? "var(--color-text-4)"
                  : "var(--color-text-3)",
          }}
        >
          {t("tab_timeline")}
          {activeTab === "timeline" && (
            <span
              aria-hidden="true"
              className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded"
              style={{ background: "var(--color-accent)" }}
            />
          )}
        </button>
        <span className="flex-1" />

        {activeTab === "timeline" && hasScript && (
          <div className="mr-1 inline-flex items-center gap-1.5">
            <button
              type="button"
              className="sv-navbtn inline-flex items-center gap-1.5"
              disabled
              title={t("batch_generate_storyboards")}
            >
              <Sparkles className="h-3 w-3" />
              <span>{t("batch_generate_storyboards")}</span>
            </button>
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

      {/* 主体 */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === "preprocessing" && hasDraft ? (
          <div className="h-full overflow-y-auto p-4">
            <PreprocessingView
              projectName={projectName}
              episode={episode}
              contentMode={contentMode}
            />
          </div>
        ) : episodeScript && segments.length > 0 ? (
          <ShotSplitView
            segments={segments}
            contentMode={contentMode}
            aspectRatio={aspectRatio}
            projectName={projectName}
            isGridMode={false}
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
