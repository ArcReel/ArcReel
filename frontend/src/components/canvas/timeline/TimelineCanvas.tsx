import { useCallback, useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { SegmentCard } from "./SegmentCard";
import { useScrollTarget } from "@/hooks/useScrollTarget";
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

  // Empty state — no episode selected
  if (!episodeScript || !projectData) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        请在左侧选择剧集
      </div>
    );
  }

  // Compute total duration from actual segments if available
  const totalDuration =
    episodeScript.duration_seconds ??
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
            E{episodeScript.episode}: {episodeScript.title}
          </h2>
          <p className="text-xs text-gray-500">
            {segments.length} {segmentLabel} · 约 {totalDuration}s
          </p>
        </div>

        {/* ---- Segment cards ---- */}
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

        {/* Bottom spacer for scroll comfort */}
        <div className="h-16" />
      </div>
    </div>
  );
}
