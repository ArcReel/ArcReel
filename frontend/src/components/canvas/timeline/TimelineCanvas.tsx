import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Sparkles, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useTranslation } from "react-i18next";
import { SegmentCard } from "./SegmentCard";
import { GridSegmentGroup } from "./GridSegmentGroup";
import { PreprocessingView } from "./PreprocessingView";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { useAppStore } from "@/stores/app-store";
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

/** Compute grid size for a group based on scene count and aspect ratio.
 *  Mirrors backend calculate_grid_layout + chunking logic in grids.py. */
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
  const { t } = useTranslation("dashboard");
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
  const gridsRevision = useAppStore((s) => s.gridsRevision);
  const isGridMode = projectData?.generation_mode === "grid";
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
    API.listGrids(projectName).then((data) => {
      setGrids(data);
      setGridsVersion((v) => v + 1);
    }).catch(() => {/* silently ignore */});
  }, [isGridMode, projectName]);

  // Fetch grids list for the current episode when in grid mode
  // Also re-fetch when gridsRevision changes (triggered by grid_ready SSE events)
  useEffect(() => {
    refreshGrids();
  }, [refreshGrids, episodeScript, gridsRevision]);

  /**
   * Find all grid IDs whose scene_ids are a subset of the given group.
   * Handles batched grids: a group with 32 scenes may have 4 grids of ~9 each.
   */
  function getGridIdsForGroup(groupScenes: Segment[]): string[] {
    const groupIdSet = new Set(groupScenes.map((s) => getSegmentId(s, contentMode)));
    return grids
      .filter((g) => g.scene_ids.length > 0 && g.scene_ids.every((id) => groupIdSet.has(id)))
      .map((g) => g.id);
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
        {t("select_episode_hint")}
      </div>
    );
  }

  // Compute total duration from actual segments if available
  const totalDuration =
    episodeScript?.duration_seconds ??
    segments.reduce((sum, s) => sum + s.duration_seconds, 0);

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
              {contentMode === "narration"
                ? t("segment_count", { count: segments.length })
                : t("scene_count_label", { count: segments.length })} · ~{totalDuration}s
            </p>
          )}
          {episodeCost && (
            <div className="mt-2 flex items-center gap-4 rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-xs tabular-nums">
              <span className="text-gray-600">{t("cost_estimate_short")}</span>
              <span className="text-gray-500">{t("cost_storyboard_short")} <span className="text-gray-300">{formatCost(episodeCost.totals.estimate.image)}</span></span>
              <span className="text-gray-500">{t("cost_video_short")} <span className="text-gray-300">{formatCost(episodeCost.totals.estimate.video)}</span></span>
              <span className="text-gray-500">{t("cost_total_short")} <span className="font-medium text-amber-400">{formatCost(totalBreakdown(episodeCost.totals.estimate))}</span></span>
              <span className="text-gray-700">|</span>
              <span className="text-gray-600">{t("cost_actual_short")}</span>
              <span className="text-gray-500">{t("cost_storyboard_short")} <span className="text-gray-300">{formatCost(episodeCost.totals.actual.image)}</span></span>
              <span className="text-gray-500">{t("cost_video_short")} <span className="text-gray-300">{formatCost(episodeCost.totals.actual.video)}</span></span>
              <span className="text-gray-500">{t("cost_total_short")} <span className="font-medium text-emerald-400">{formatCost(totalBreakdown(episodeCost.totals.actual))}</span></span>
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
              {t("preprocessing_tab")}
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
              {t("timeline_tab")}
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
                    className={`inline-flex items-center gap-1.5 rounded-lg border px-4 py-2 text-sm font-medium transition-colors ${
                      generatingAllGrids
                        ? "border-blue-700 text-blue-400 opacity-70 cursor-not-allowed"
                        : "border-blue-600 text-blue-400 hover:bg-blue-600/10"
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
                    {generatingAllGrids ? t("submitting") : t("generate_all_grids")}
                  </motion.button>
                </div>
              )}

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
                            clues={projectData.clues}
                            projectName={projectName}
                            durationOptions={durationOptions}
                            isGridMode
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
