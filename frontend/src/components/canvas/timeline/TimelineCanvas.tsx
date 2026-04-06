import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { SegmentCard } from "./SegmentCard";
import { PreprocessingView } from "./PreprocessingView";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { useCostStore } from "@/stores/cost-store";
import { formatCost, totalBreakdown } from "@/utils/cost-format";
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
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
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
    projectData?.aspect_ratio?.storyboard ??
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
        ) : null}

        {/* Bottom spacer for scroll comfort */}
        <div className="h-16" />
      </div>
    </div>
  );
}
