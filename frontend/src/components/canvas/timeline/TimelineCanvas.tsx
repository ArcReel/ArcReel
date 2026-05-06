import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SegmentCard } from "./SegmentCard";
import { GridSegmentGroup } from "./GridSegmentGroup";
import { PreprocessingView } from "./PreprocessingView";
import { ShotSplitView } from "./ShotSplitView";
import { EpisodeHeader } from "./EpisodeHeader";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { useTasksStore } from "@/stores/tasks-store";
import { API } from "@/api";
import { effectiveMode } from "@/utils/generation-mode";
import type { GridGeneration } from "@/types/grid";
import type {
  EpisodeScript,
  NarrationEpisodeScript,
  DramaEpisodeScript,
  NarrationSegment,
  DramaScene,
  ProjectData,
} from "@/types";

type Segment = NarrationSegment | DramaScene;

function getSegmentId(segment: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (segment as NarrationSegment).segment_id
    : (segment as DramaScene).scene_id;
}

/**
 * Registers segment scroll target for grid mode (where ShotSplitView's own
 * registration is absent). All grid SegmentCard wrappers carry id="segment-...",
 * so we only need to register the listener here — no prepareTarget needed.
 */
function GridScrollTargetRegistrar() {
  useScrollTarget("segment");
  return null;
}

/** Group segments by segment_break into contiguous groups. */
function groupBySegmentBreak(segments: Segment[]): Segment[][] {
  const groups: Segment[][] = [];
  let current: Segment[] = [];
  for (const seg of segments) {
    if (seg.segment_break && current.length > 0) {
      groups.push(current);
      current = [];
    }
    current.push(seg);
  }
  if (current.length > 0) groups.push(current);
  return groups;
}

/** Compute grid layout. Mirrors backend calculate_grid_layout. */
function computeGridSize(
  count: number,
  aspectRatio: string = "9:16",
): { gridSize: string | null; rows: number; cols: number; cellCount: number; batchCount: number } {
  if (count < 1) return { gridSize: null, rows: 0, cols: 0, cellCount: 0, batchCount: 0 };
  const [w, h] = aspectRatio.split(":").map(Number);
  const isHorizontal = w > h;
  const effective = Math.min(count, 9);

  let gridSize: string;
  let cellCount: number;
  let rows: number;
  let cols: number;

  if (effective <= 4) {
    gridSize = "grid_4";
    cellCount = 4;
    rows = 2;
    cols = 2;
  } else if (effective <= 6) {
    gridSize = "grid_6";
    cellCount = 6;
    rows = isHorizontal ? 3 : 2;
    cols = isHorizontal ? 2 : 3;
  } else {
    gridSize = "grid_9";
    cellCount = 9;
    rows = 3;
    cols = 3;
  }

  const batchCount = count > cellCount ? Math.ceil(count / cellCount) : 1;
  return { gridSize, rows, cols, cellCount, batchCount };
}

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
  onGenerateGrid?: (episode: number, scriptFile: string, sceneIds?: string[]) => void;
  durationOptions?: number[];
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
}

/**
 * 集工作区：EpisodeHeader + Tab 切换（预处理 / 剧本时间线）+ 分镜分屏（非 grid 模式）
 * 或 GridSegmentGroup 列表（grid 模式）。
 */
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
  onGenerateGrid,
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

  // Grid mode state
  const gridsRevision = useAppStore((s) => s.gridsRevision);
  const currentEpisodeMeta = projectData?.episodes?.find((e) => e.episode === episode);
  const isGridMode = effectiveMode(projectData, currentEpisodeMeta) === "grid";
  const segmentGroups = useMemo(
    () => (isGridMode ? groupBySegmentBreak(segments) : []),
    [isGridMode, segments],
  );
  const [generatingGridGroups, setGeneratingGridGroups] = useState<Set<number>>(new Set());
  const [generatingAllGrids, setGeneratingAllGrids] = useState(false);
  const [grids, setGrids] = useState<GridGeneration[]>([]);
  const [gridsVersion, setGridsVersion] = useState(0);

  const refreshGrids = useCallback(() => {
    if (!isGridMode || !projectName) return;
    API.listGrids(projectName)
      .then((data) => {
        setGrids(data);
        setGridsVersion((v) => v + 1);
      })
      .catch(() => {});
  }, [isGridMode, projectName]);

  useEffect(() => {
    refreshGrids();
  }, [refreshGrids, episodeScript, gridsRevision]);

  function getGridIdsForGroup(groupScenes: Segment[]): string[] {
    const groupIdSet = new Set(groupScenes.map((s) => getSegmentId(s, contentMode)));
    const matched = grids.filter(
      (g) =>
        g.episode === episode &&
        g.scene_ids.length > 0 &&
        g.scene_ids.every((id) => groupIdSet.has(id)),
    );
    const byKey = new Map<string, (typeof matched)[number]>();
    for (const g of matched) {
      const key = [...g.scene_ids].sort().join(",");
      const existing = byKey.get(key);
      if (!existing || g.created_at > existing.created_at) {
        byKey.set(key, g);
      }
    }
    return Array.from(byKey.values())
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map((g) => g.id);
  }

  const handleGenerateGroupGrid = useCallback(
    (groupIndex: number, groupScenes: Segment[]) => {
      if (!onGenerateGrid || !scriptFile) return;
      const sceneIds = groupScenes.map((s) => getSegmentId(s, contentMode));
      setGeneratingGridGroups((prev) => new Set(prev).add(groupIndex));
      onGenerateGrid(episode, scriptFile, sceneIds);
      setTimeout(() => {
        setGeneratingGridGroups((prev) => {
          const next = new Set(prev);
          next.delete(groupIndex);
          return next;
        });
        refreshGrids();
      }, 3000);
    },
    [onGenerateGrid, scriptFile, contentMode, episode, refreshGrids],
  );

  const handleGenerateAllGrids = useCallback(() => {
    if (!onGenerateGrid || !scriptFile) return;
    setGeneratingAllGrids(true);
    onGenerateGrid(episode, scriptFile);
    setTimeout(() => {
      setGeneratingAllGrids(false);
      refreshGrids();
    }, 3000);
  }, [onGenerateGrid, scriptFile, episode, refreshGrids]);

  // Empty state — no episode selected or no content at all
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

  // 若有 currentEpisodeMeta 用其值；否则构造一个最小 EpisodeMeta 用于 header
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

        {activeTab === "timeline" && hasScript && !isGridMode && (
          <div className="mr-1 inline-flex items-center gap-1.5">
            {/* 设计稿示例：批量按钮（暂不实现批量逻辑，仅占位入口） */}
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

        {activeTab === "timeline" && hasScript && isGridMode && onGenerateGrid && scriptFile && (
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
              <span>
                {generatingAllGrids ? t("submitting") : t("generate_all_grids")}
              </span>
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
        ) : episodeScript && !isGridMode && segments.length > 0 ? (
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
        ) : episodeScript && isGridMode && segmentGroups.length > 0 ? (
          /* Grid mode 保留原有 GridSegmentGroup + SegmentCard 渲染（短期内不重构） */
          <div className="h-full overflow-y-auto p-4">
            <GridScrollTargetRegistrar />
            {segmentGroups.map((group, groupIdx) => {
              const gridResult = computeGridSize(group.length, aspectRatio);
              return (
                <GridSegmentGroup
                  key={groupIdx}
                  groupIndex={groupIdx}
                  scenes={group}
                  gridSize={gridResult.gridSize}
                  sceneCount={group.length}
                  batchCount={gridResult.batchCount}
                  onGenerateGrid={() => handleGenerateGroupGrid(groupIdx, group)}
                  generatingGrid={generatingGridGroups.has(groupIdx)}
                  gridIds={getGridIdsForGroup(group)}
                  projectName={projectName}
                  onGridRegenerated={refreshGrids}
                  gridsVersion={gridsVersion}
                >
                  {group.map((segment) => {
                    const segId = getSegmentId(segment, contentMode);
                    return (
                      <div id={`segment-${segId}`} key={segId}>
                        <SegmentCard
                          segment={segment}
                          contentMode={contentMode}
                          aspectRatio={aspectRatio}
                          characters={projectData.characters}
                          scenes={projectData.scenes ?? {}}
                          props={projectData.props ?? {}}
                          projectName={projectName}
                          durationOptions={durationOptions}
                          isGridMode
                          onUpdatePrompt={handleUpdatePrompt}
                          onGenerateStoryboard={handleGenSb}
                          onGenerateVideo={handleGenVid}
                          onRestoreStoryboard={onRestoreStoryboard}
                          onRestoreVideo={onRestoreVideo}
                        />
                      </div>
                    );
                  })}
                </GridSegmentGroup>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
