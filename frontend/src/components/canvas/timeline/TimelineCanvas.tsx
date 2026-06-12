import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PreprocessingView } from "./PreprocessingView";
import { ShotSplitView } from "./ShotSplitView";
import { EpisodeHeader } from "./EpisodeHeader";
import { AdReferenceUnitsPanel } from "./AdReferenceUnitsPanel";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import { effectiveMode } from "@/utils/generation-mode";
import { getScriptItemId } from "@/utils/script-shape";
import type {
  EpisodeScript,
  NarrationEpisodeScript,
  DramaEpisodeScript,
  AdEpisodeScript,
  NarrationSegment,
  DramaScene,
  AdShot,
  ProjectData,
} from "@/types";

type Segment = NarrationSegment | DramaScene | AdShot;

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
  /** ad 模式镜头顺序调整（向前/向后移动一位），resolve 为是否移动成功 */
  onMoveShot?: (shotId: string, direction: "earlier" | "later", scriptFile?: string) => Promise<boolean>;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void;
  onGenerateNarration?: (segmentId: string, scriptFile?: string) => void;
  onGenerateEpisodeNarration?: (scriptFile?: string) => void;
  durationOptions?: number[];
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  onSaveTitle?: (next: string) => Promise<void>;
  canEditTitle?: boolean;
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
  onMoveShot,
  onGenerateStoryboard,
  onGenerateVideo,
  onGenerateNarration,
  onGenerateEpisodeNarration,
  onRestoreStoryboard,
  onRestoreVideo,
  onSaveTitle,
  canEditTitle,
}: TimelineCanvasProps) {
  const { t } = useTranslation("dashboard");
  const contentMode = projectData?.content_mode ?? "narration";
  // 分镜编辑子视图按剧本形状显式分派：narration（segments）/ drama（scenes）/ ad（shots）。
  // 未知/脏 content_mode 沿用历史兜底落 drama 视图。
  const editorContentMode: "narration" | "drama" | "ad" =
    contentMode === "narration" ? "narration" : contentMode === "ad" ? "ad" : "drama";

  const hasScript = Boolean(episodeScript);
  // ad 一键生成不走预处理中间文件，预处理 tab 对 ad 无意义，仅 timeline 单 tab
  const showTabs = Boolean(hasDraft) && editorContentMode !== "ad";
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

  // 解析 aspect ratio（仅支持 9:16 / 16:9 两档，3:4/1:1 也回退到 16:9）；
  // 缺省回退：narration / ad 竖屏，drama 与未知/脏值横屏——与后端
  // ScriptGenerator._resolve_aspect_ratio 同口径，避免预览与产物比例错位。
  const rawAspect =
    typeof projectData?.aspect_ratio === "string"
      ? projectData.aspect_ratio
      : projectData?.aspect_ratio?.storyboard ??
        (contentMode === "narration" || contentMode === "ad" ? "9:16" : "16:9");
  const aspectRatio: "9:16" | "16:9" =
    rawAspect === "9:16" || rawAspect === "16:9" ? rawAspect : "16:9";

  // 仅三种已注册模式显式取数；未知/脏 content_mode 返回空列表（不渲染可编辑视图）——
  // 否则会以 drama 形状渲染、保存却按真实 content_mode 分派到错误端点。
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

  // 任务派生 loading
  const tasks = useTasksStore((s) => s.tasks);
  const isGenerating = useCallback(
    (taskType: "storyboard" | "video" | "tts", segmentId: string): boolean =>
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
  const generatingNarration = useCallback(
    (segId: string) => isGenerating("tts", segId),
    [isGenerating],
  );
  // 批量旁白进行中：当前分集还有未完结的 tts 任务时禁用批量按钮，避免重复入队；
  // 按本集 segment 范围判定，不影响其他分集的批量入口
  const currentSegmentIds = useMemo(
    () => new Set(segments.map((s) => getScriptItemId(s, editorContentMode))),
    [segments, editorContentMode],
  );
  const narrationBatchBusy = useMemo(
    () =>
      tasks.some(
        (t) =>
          t.task_type === "tts" &&
          t.project_name === projectName &&
          currentSegmentIds.has(t.resource_id) &&
          (t.status === "queued" || t.status === "running"),
      ),
    [tasks, projectName, currentSegmentIds],
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
  // ad + reference_video：镜头按派生分组直出视频，展示分组面板（其余路径不渲染）
  const adReference =
    contentMode === "ad" && effectiveMode(projectData, currentEpisodeMeta) === "reference_video";
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
  const handleMoveShot = onMoveShot
    ? (shotId: string, direction: "earlier" | "later") => onMoveShot(shotId, direction, scriptFile)
    : undefined;
  // 生成回调保持可选透传：未提供（如 ad + reference_video 不开放逐镜头图生视频）时
  // 编辑器隐藏对应生成入口，而非渲染一个点了没反应的按钮。
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
    <div className="flex h-full flex-col overflow-hidden">
      {/* 集 header */}
      <EpisodeHeader
        ep={epMeta}
        segmentCount={segments.length}
        totalDuration={totalDuration}
        episodeCost={episodeCost ?? undefined}
        onSaveTitle={onSaveTitle}
        canEditTitle={canEditTitle}
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
            {contentMode === "narration" && onGenerateEpisodeNarration && (
              <button
                type="button"
                className="sv-navbtn inline-flex items-center gap-1.5"
                disabled={narrationBatchBusy}
                onClick={() => onGenerateEpisodeNarration(scriptFile)}
                title={t("batch_generate_narration")}
              >
                <Sparkles className="h-3 w-3" />
                <span>{t("batch_generate_narration")}</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* 主体 */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === "preprocessing" && hasDraft && editorContentMode !== "ad" ? (
          <div className="h-full overflow-y-auto p-4">
            <PreprocessingView
              projectName={projectName}
              episode={episode}
              contentMode={editorContentMode}
            />
          </div>
        ) : episodeScript && segments.length > 0 ? (
          <div className="flex h-full flex-col">
            {adReference && (
              <AdReferenceUnitsPanel
                projectName={projectName}
                episode={episode}
                shots={segments as AdShot[]}
              />
            )}
            <div className="min-h-0 flex-1 overflow-hidden">
              <ShotSplitView
                segments={segments}
                contentMode={editorContentMode}
                aspectRatio={aspectRatio}
                projectName={projectName}
                scriptFile={scriptFile}
                isGridMode={false}
                onUpdatePrompt={handleUpdatePrompt}
                onMoveShot={handleMoveShot}
                onGenerateStoryboard={handleGenSb}
                onGenerateVideo={handleGenVid}
                onGenerateNarration={handleGenNarration}
                onRestoreStoryboard={onRestoreStoryboard}
                onRestoreVideo={onRestoreVideo}
                generatingStoryboard={generatingStoryboard}
                generatingVideo={generatingVideo}
                generatingNarration={generatingNarration}
                durationOptions={durationOptions}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
