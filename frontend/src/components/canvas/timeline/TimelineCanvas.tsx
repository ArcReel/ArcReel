import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Sparkles, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { SegmentCard } from "./SegmentCard";
import { GridSegmentGroup } from "./GridSegmentGroup";
import { PreprocessingView } from "./PreprocessingView";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { useCostStore } from "@/stores/cost-store";
import { formatCost, totalBreakdown } from "@/utils/cost-format";
import { API } from "@/api";
import type { GridGeneration } from "@/types/grid";
import type {
  EpisodeScript,
  NarrationEpisodeScript,
  DramaEpisodeScript,
  NarrationSegment,
  DramaScene,
  ProjectData,
} from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Segment = NarrationSegment | DramaScene;

function getSegmentId(segment: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (segment as NarrationSegment).segment_id
    : (segment as DramaScene).scene_id;
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

/** Compute grid size for a group based on scene count and aspect ratio. */
function computeGridSize(
  count: number,
  aspectRatio: string = "9:16",
): { gridSize: string | null; rows: number; cols: number } {
  if (count < 4) return { gridSize: null, rows: 0, cols: 0 };
  const [w, h] = aspectRatio.split(":").map(Number);
  const isHorizontal = w > h;
  if (count <= 4) return { gridSize: "grid_4", rows: 2, cols: 2 };
  if (count <= 6) {
    return {
      gridSize: "grid_6",
      rows: isHorizontal ? 3 : 2,
      cols: isHorizontal ? 2 : 3,
    };
  }
  return { gridSize: "grid_9", rows: 3, cols: 3 };
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface TimelineCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
  hasDraft?: boolean;
  episodeScript: EpisodeScript | null;
  scriptFile?: string;
  projectData: ProjectData | null;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown, scriptFile?: string) => void;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void;
  onGenerateGrid?: (episode: number, scriptFile: string, sceneIds?: string[]) => void;
  durationOptions?: number[];
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Main canvas container that renders a vertical list of SegmentCards for
 * the currently selected episode.
 *
 * Shows episode header (title, segment count, duration), followed by the
 * full timeline of segment cards with spacing.
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentMode = projectData?.content_mode ?? "narration";

  const hasScript = Boolean(episodeScript);
  const showTabs = Boolean(hasDraft);
  const defaultTab = hasScript ? "timeline" : "preprocessing";
  const [activeTab, setActiveTab] = useState<"preprocessing" | "timeline">(defaultTab);

  // Auto-switch to timeline when script becomes available
  useEffect(() => {
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

  // Determine aspect ratio — use project config if available, otherwise defaults
  const aspectRatio =
    typeof projectData?.aspect_ratio === "string"
      ? projectData.aspect_ratio
      : projectData?.aspect_ratio?.storyboard ??
        (contentMode === "narration" ? "9:16" : "16:9");

  // Pick the correct array (segments for narration, scenes for drama)
  const segments = useMemo<Segment[]>(
    () =>
      !episodeScript || !projectData
        ? []
        : contentMode === "narration"
          ? ((episodeScript as NarrationEpisodeScript).segments ?? [])
          : ((episodeScript as DramaEpisodeScript).scenes ?? []),
    [contentMode, episodeScript, projectData],
  );
  const segmentIndexMap = useMemo(
    () =>
      new Map(
        segments.map((segment, index) => [getSegmentId(segment, contentMode), index]),
      ),
    [contentMode, segments],
  );

  // Grid mode state
  const isGridMode = projectData?.generation_mode === "grid";
  const segmentGroups = useMemo(
    () => (isGridMode ? groupBySegmentBreak(segments) : []),
    [isGridMode, segments],
  );
  const [generatingGridGroups, setGeneratingGridGroups] = useState<Set<number>>(new Set());
  const [generatingAllGrids, setGeneratingAllGrids] = useState(false);
  const [grids, setGrids] = useState<GridGeneration[]>([]);

  // Fetch grids list for the current episode when in grid mode
  useEffect(() => {
    if (!isGridMode || !projectName) return;
    API.listGrids(projectName).then(setGrids).catch(() => {/* silently ignore */});
  }, [isGridMode, projectName, episodeScript]);

  /**
   * Build a map from sorted-scene-key → gridId for matching groups.
   * Uses the grid's scene_ids set intersection with a group's scene IDs.
   */
  const gridIdByGroupScenes = useMemo(() => {
    const map = new Map<string, string>();
    for (const grid of grids) {
      const key = [...grid.scene_ids].sort().join(",");
      map.set(key, grid.id);
    }
    return map;
  }, [grids]);

  function getGridIdForGroup(groupScenes: Segment[]): string | null {
    const key = groupScenes.map((s) => getSegmentId(s, contentMode)).sort().join(",");
    return gridIdByGroupScenes.get(key) ?? null;
  }

  const handleGenerateGroupGrid = useCallback(
    (groupIndex: number, groupScenes: Segment[]) => {
      if (!onGenerateGrid || !scriptFile) return;
      const sceneIds = groupScenes.map((s) => getSegmentId(s, contentMode));
      setGeneratingGridGroups((prev) => new Set(prev).add(groupIndex));
      // Fire and let the toast/task system handle the result
      onGenerateGrid(episode, scriptFile, sceneIds);
      // Clear loading after a short delay (actual progress tracked via task queue)
      setTimeout(() => {
        setGeneratingGridGroups((prev) => {
          const next = new Set(prev);
          next.delete(groupIndex);
          return next;
        });
      }, 3000);
    },
    [onGenerateGrid, scriptFile, contentMode, episode],
  );

  const handleGenerateAllGrids = useCallback(() => {
    if (!onGenerateGrid || !scriptFile) return;
    setGeneratingAllGrids(true);
    onGenerateGrid(episode, scriptFile);
    setTimeout(() => setGeneratingAllGrids(false), 3000);
  }, [onGenerateGrid, scriptFile, episode]);

  const virtualizer = useVirtualizer({
    count: segments.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 200,
    overscan: 5,
    measureElement: (element) => element?.getBoundingClientRect().height ?? 200,
  });
  const prepareScrollTarget = useCallback(
    (target: { id: string }) => {
      const index = segmentIndexMap.get(target.id);
      if (index == null) {
        return false;
      }
      virtualizer.scrollToIndex(index, { align: "center" });
      return true;
    },
    [segmentIndexMap, virtualizer],
  );

  // Respond to agent-triggered scroll targets for segments
  useScrollTarget("segment", { prepareTarget: prepareScrollTarget });

  // Empty state — no episode selected or no content at all
  if (!projectData || (!episodeScript && !hasDraft)) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        请在左侧选择剧集
      </div>
    );
  }

  // Compute total duration from actual segments if available
  const totalDuration =
    episodeScript?.duration_seconds ??
    segments.reduce((sum, s) => sum + s.duration_seconds, 0);

  // Label depends on content mode
  const segmentLabel = contentMode === "narration" ? "个片段" : "个场景";
  const virtualItems = virtualizer.getVirtualItems();

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto">
      <div className="p-4">
        {/* ---- Episode header ---- */}
        <div className="mb-4">
          <h2 className="text-lg font-semibold text-gray-100">
            {episodeScript
              ? `E${episodeScript.episode}: ${episodeScript.title}`
              : `E${episode}${episodeTitle ? `: ${episodeTitle}` : ""}`}
          </h2>
          {episodeScript && (
            <p className="text-xs text-gray-500">
              {segments.length} {segmentLabel} · 约 {totalDuration}s
            </p>
          )}
          {episodeCost && (
            <div className="mt-2 flex items-center gap-4 rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-xs tabular-nums">
              <span className="text-gray-600">预估</span>
              <span className="text-gray-500">分镜 <span className="text-gray-300">{formatCost(episodeCost.totals.estimate.image)}</span></span>
              <span className="text-gray-500">视频 <span className="text-gray-300">{formatCost(episodeCost.totals.estimate.video)}</span></span>
              <span className="text-gray-500">总计 <span className="font-medium text-amber-400">{formatCost(totalBreakdown(episodeCost.totals.estimate))}</span></span>
              <span className="text-gray-700">|</span>
              <span className="text-gray-600">实际</span>
              <span className="text-gray-500">分镜 <span className="text-gray-300">{formatCost(episodeCost.totals.actual.image)}</span></span>
              <span className="text-gray-500">视频 <span className="text-gray-300">{formatCost(episodeCost.totals.actual.video)}</span></span>
              <span className="text-gray-500">总计 <span className="font-medium text-emerald-400">{formatCost(totalBreakdown(episodeCost.totals.actual))}</span></span>
            </div>
          )}
        </div>

        {/* ---- Tab bar (only when draft exists) ---- */}
        {showTabs && (
          <div className="mb-4 flex gap-0 border-b border-gray-800">
            <button
              type="button"
              onClick={() => setActiveTab("preprocessing")}
              className={`border-b-2 px-4 py-2 text-sm transition-colors focus-ring rounded-t ${
                activeTab === "preprocessing"
                  ? "border-indigo-500 text-indigo-400 font-medium"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              预处理
            </button>
            <button
              type="button"
              onClick={() => hasScript && setActiveTab("timeline")}
              disabled={!hasScript}
              className={`border-b-2 px-4 py-2 text-sm transition-colors focus-ring rounded-t ${
                activeTab === "timeline"
                  ? "border-indigo-500 text-indigo-400 font-medium"
                  : !hasScript
                    ? "border-transparent text-gray-700 cursor-not-allowed"
                    : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              剧本时间线
            </button>
          </div>
        )}

        {/* ---- Tab content ---- */}
        {activeTab === "preprocessing" && hasDraft ? (
          <PreprocessingView
            projectName={projectName}
            episode={episode}
            contentMode={contentMode}
          />
        ) : episodeScript ? (
          isGridMode && segmentGroups.length > 0 ? (
            /* ---- Grid mode: grouped segments without virtualization ---- */
            <div>
              {/* Batch generate all grids button */}
              {onGenerateGrid && scriptFile && (
                <div className="mb-4">
                  <motion.button
                    type="button"
                    onClick={handleGenerateAllGrids}
                    disabled={generatingAllGrids}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
                      generatingAllGrids
                        ? "bg-blue-700 opacity-70 cursor-not-allowed"
                        : "bg-blue-600 hover:bg-blue-500"
                    }`}
                    animate={
                      generatingAllGrids
                        ? { opacity: [0.7, 1, 0.7] }
                        : { opacity: 1 }
                    }
                    transition={
                      generatingAllGrids
                        ? { duration: 1.5, repeat: Infinity, ease: "easeInOut" }
                        : { duration: 0.3 }
                    }
                  >
                    <AnimatePresence mode="wait" initial={false}>
                      {generatingAllGrids ? (
                        <motion.span
                          key="loader"
                          initial={{ opacity: 0, rotate: -90 }}
                          animate={{ opacity: 1, rotate: 0 }}
                          exit={{ opacity: 0, rotate: 90 }}
                          transition={{ duration: 0.2 }}
                        >
                          <Loader2 className="h-4 w-4 animate-spin" />
                        </motion.span>
                      ) : (
                        <motion.span
                          key="sparkles"
                          initial={{ opacity: 0, scale: 0.5 }}
                          animate={{ opacity: 1, scale: 1 }}
                          exit={{ opacity: 0, scale: 0.5 }}
                          transition={{ duration: 0.2 }}
                        >
                          <Sparkles className="h-4 w-4" />
                        </motion.span>
                      )}
                    </AnimatePresence>
                    {generatingAllGrids ? "提交中..." : "一键生成全部宫格"}
                  </motion.button>
                </div>
              )}

              {segmentGroups.map((group, groupIdx) => {
                const { gridSize } = computeGridSize(group.length, aspectRatio);
                return (
                  <GridSegmentGroup
                    key={groupIdx}
                    groupIndex={groupIdx}
                    scenes={group}
                    gridSize={gridSize}
                    sceneCount={group.length}
                    onGenerateGrid={() => handleGenerateGroupGrid(groupIdx, group)}
                    generatingGrid={generatingGridGroups.has(groupIdx)}
                    gridId={getGridIdForGroup(group)}
                    projectName={projectName}
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
                            clues={projectData.clues}
                            projectName={projectName}
                            durationOptions={durationOptions}
                            onUpdatePrompt={onUpdatePrompt && ((id, field, value) => onUpdatePrompt(id, field, value, scriptFile))}
                            onGenerateStoryboard={onGenerateStoryboard && ((id) => onGenerateStoryboard(id, scriptFile))}
                            onGenerateVideo={onGenerateVideo && ((id) => onGenerateVideo(id, scriptFile))}
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
          ) : (
            /* ---- Normal mode: virtualized flat list ---- */
            <div
              className="relative"
              style={{ height: `${virtualizer.getTotalSize()}px` }}
            >
              {virtualItems.map((virtualItem) => {
                const segment = segments[virtualItem.index];
                const segId = getSegmentId(segment, contentMode);
                return (
                  <div
                    id={`segment-${segId}`}
                    key={segId}
                    data-index={virtualItem.index}
                    ref={virtualizer.measureElement}
                    className="absolute left-0 top-0 w-full"
                    style={{
                      transform: `translateY(${virtualItem.start}px)`,
                      paddingBottom: virtualItem.index === segments.length - 1 ? 0 : 16,
                    }}
                  >
                    <SegmentCard
                      segment={segment}
                      contentMode={contentMode}
                      aspectRatio={aspectRatio}
                      characters={projectData.characters}
                      clues={projectData.clues}
                      projectName={projectName}
                      durationOptions={durationOptions}
                      onUpdatePrompt={onUpdatePrompt && ((id, field, value) => onUpdatePrompt(id, field, value, scriptFile))}
                      onGenerateStoryboard={onGenerateStoryboard && ((id) => onGenerateStoryboard(id, scriptFile))}
                      onGenerateVideo={onGenerateVideo && ((id) => onGenerateVideo(id, scriptFile))}
                      onRestoreStoryboard={onRestoreStoryboard}
                      onRestoreVideo={onRestoreVideo}
                    />
                  </div>
                );
              })}
            </div>
          )
        ) : null}

        {/* Bottom spacer for scroll comfort */}
        <div className="h-16" />
      </div>
    </div>
  );
}
