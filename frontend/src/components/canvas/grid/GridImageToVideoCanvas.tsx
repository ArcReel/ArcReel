import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { EpisodeHeader } from "../timeline/EpisodeHeader";
import { ScriptReviewGate } from "../timeline/ScriptReviewGate";
import { PromptAuthoringButton } from "../shared/PromptAuthoringButton";
import { ShotSplitView } from "../timeline/ShotSplitView";
import { EmptyScriptState } from "../timeline/EmptyScriptState";
import { StoryboardBatchDialog } from "../timeline/StoryboardBatchDialog";
import type { InsertShotHandler } from "../timeline/ShotStructureActions";
import { GridPreviewView } from "./GridPreviewView";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { useEpisodeSurfaceRequest } from "@/stores/episode-surface-store";
import { useActiveResourceIds, useHasActiveTaskForScriptFile } from "@/stores/tasks-store";
import { getScriptItemId, sumItemDuration } from "@/utils/script-shape";
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
  /** 内容确认页的剧本规划档位；时长由端点固定时与 `durationOptions` 不同。 */
  planDurationOptions?: number[];
  durationEndpointFixed?: boolean;
  videoModelUnresolved?: boolean;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  /** 已保存时长越界的成因判定；缺省时 ShotDetail 退回不区分成因的通用警告文案。 */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
    scriptFile?: string,
  ) => void | Promise<void>;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string, scriptFile?: string) => void;
  onGenerateEpisodeNarration?: (scriptFile?: string) => void;
  onGenerateGrid?: (
    episode: number,
    scriptFile: string,
    sceneIds?: string[],
  ) => Promise<void> | void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  /** 分镜改序：移到 afterId 之后，null 移到最前；resolve 为是否成功 */
  onMoveShot?: (shotId: string, afterId: string | null, scriptFile?: string) => Promise<boolean>;
  /** 新增分镜（旁白带正文）：afterId 为 null 时追加到末尾；resolve 为是否成功 */
  onInsertShot?: (afterId: string | null, novelText: string | undefined, scriptFile?: string) => Promise<boolean>;
  /** 移除分镜，resolve 为是否成功 */
  onRemoveShot?: (itemId: string, scriptFile?: string) => Promise<boolean>;
  onSaveTitle?: (next: string) => Promise<void>;
  canEditTitle?: boolean;
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
  onSaveTitle,
  canEditTitle,
}: GridImageToVideoCanvasProps) {
  const { t } = useTranslation("dashboard");
  const contentMode = projectData?.content_mode ?? "narration";
  // grid 画布仅服务 narration/drama（ad 不开放宫格生视频）；
  // 子视图按窄类型接收，ad 显式不进（不落 drama 兜底）。
  // 未知/脏 content_mode 沿用历史兜底落 drama 视图，仅 ad 显式排除。
  const editorContentMode: "narration" | "drama" | null =
    contentMode === "narration" ? "narration" : contentMode === "ad" ? null : "drama";

  const hasScript = Boolean(episodeScript);
  const showTabs = Boolean(hasDraft);
  const defaultTab: GridTab = hasScript ? "units" : "preprocessing";
  const [activeTab, setActiveTab] = useState<GridTab>(defaultTab);
  const [videoBatchOpen, setVideoBatchOpen] = useState(false);
  const demoReadOnly = useDemoWorkbench();

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 剧本加载完成后切到 units 标签页，由 hasScript 变化驱动
    if (hasScript) setActiveTab("units");
  }, [hasScript]);

  useEpisodeSurfaceRequest(projectName, episode, "script_plan", () => {
    if (showTabs) setActiveTab("preprocessing");
  });

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
          : contentMode === "drama"
            ? ((episodeScript as DramaEpisodeScript).scenes ?? [])
            : [],
    [contentMode, episodeScript, projectData],
  );

  // 任务派生 loading：活跃 + 最新行胜出下沉到 store selector（各 task_type 一组活跃 resource）
  const storyboardBusyIds = useActiveResourceIds("storyboard", projectName);
  const videoBusyIds = useActiveResourceIds("video", projectName);
  const ttsBusyIds = useActiveResourceIds("tts", projectName);
  // 本集有宫格任务在跑：切割阶段会覆写本集内多个分镜的 storyboard 文件，grid 任务的
  // resource_id 是 grid_id、无法归入按分镜 resource_id 判定的 storyboardBusyIds，
  // 故按 scriptFile 粗粒度判定，启用宫格装配时禁用分镜编辑入口，避免并发写同一文件。
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
  // 批量旁白进行中：当前分集还有未完结的 tts 任务时禁用批量按钮，避免重复入队
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
      <div
        className="flex h-full items-center justify-center"
        style={{ color: "var(--muted-foreground)" }}
      >
        {t("select_episode_hint")}
      </div>
    );
  }

  const totalDuration = sumItemDuration(segments);

  const currentEpisodeMeta = projectData?.episodes?.find((e) => e.episode === episode);
  const epMeta =
    currentEpisodeMeta ??
    ({
      episode,
      title: episodeTitle ?? episodeScript?.title ?? "",
      script_file: scriptFile ?? "",
      item_count: segments.length,
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
  const handleGenNarration = onGenerateNarration
    ? (segId: string) => onGenerateNarration(segId, scriptFile)
    : undefined;
  // 结构操作与时间线一致；演示态只读，不给入口。
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

  const renderTabButton = (key: GridTab, label: string, disabled = false) => (
    <button
      type="button"
      role="tab"
      aria-selected={activeTab === key}
      onClick={() => !disabled && setActiveTab(key)}
      disabled={disabled}
      className="focus-ring relative px-3.5 py-2.5 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed"
      style={{
        color:
          activeTab === key
            ? "var(--foreground)"
            : disabled
              ? "var(--muted-foreground)"
              : "var(--muted-foreground)",
      }}
    >
      {label}
      {activeTab === key && (
        <span
          aria-hidden="true"
          className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded-sm"
          style={{ background: "var(--primary)" }}
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
        onSaveTitle={onSaveTitle}
        canEditTitle={canEditTitle}
      />

      <div
        role="tablist"
        aria-label={t("grid_canvas_tab_aria")}
        className="flex items-center gap-0.5 px-5"
        style={{
          borderBottom: "1px solid var(--border)",
          background: "oklch(0.19 0.012 250 / 0.5)",
        }}
      >
        {showTabs && renderTabButton("preprocessing", t("tab_script_plan"))}
        {renderTabButton("grid_preview", t("tab_grid_preview"))}
        {renderTabButton("units", t("tab_timeline"), !hasScript)}
        <span className="flex-1" />

        {activeTab === "grid_preview" && hasScript && onGenerateGrid && scriptFile && (
          <div className="mr-1 inline-flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => void handleGenerateAllGrids()}
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
            <PromptAuthoringButton
              projectName={projectName}
              episode={episode}
              scope="pending"
              className="sv-navbtn gap-1.5"
            />
            <button
              type="button"
              className="sv-navbtn inline-flex items-center gap-1.5"
              disabled={demoReadOnly}
              onClick={() => setVideoBatchOpen(true)}
              title={t("batch_generate_videos")}
              aria-label={t("batch_generate_videos")}
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

      {videoBatchOpen && (
        <StoryboardBatchDialog
          projectName={projectName}
          episode={episode}
          kind="videos"
          onClose={() => setVideoBatchOpen(false)}
        />
      )}

      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === "preprocessing" && hasDraft && editorContentMode ? (
          <div className="h-full overflow-y-auto p-4">
            <ScriptReviewGate
              key={`${projectName}:${episode}`}
              projectName={projectName}
              episode={episode}
              contentMode={editorContentMode}
              videoModelUnresolved={videoModelUnresolved}
              durationOptions={planDurationOptions}
              durationEndpointFixed={durationEndpointFixed}
              durationWarningReason={durationWarningReason}
              onOpenTimeline={hasScript ? () => setActiveTab("units") : undefined}
            />
          </div>
        ) : activeTab === "grid_preview" && editorContentMode ? (
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
        ) : null}
      </div>
    </div>
  );
}
