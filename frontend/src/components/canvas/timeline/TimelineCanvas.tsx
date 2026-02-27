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
  projectData: ProjectData | null;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void;
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
  projectData,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
}: TimelineCanvasProps) {
  // Respond to agent-triggered scroll targets for segments
  useScrollTarget("segment");

  // Empty state — no episode selected
  if (!episodeScript || !projectData) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        请在左侧选择剧集
      </div>
    );
  }

  const contentMode = projectData.content_mode;

  // Determine aspect ratio — use project config if available, otherwise defaults
  const aspectRatio =
    projectData.aspect_ratio?.storyboard ??
    (contentMode === "narration" ? "9:16" : "16:9");

  // Pick the correct array (segments for narration, scenes for drama)
  const segments: Segment[] =
    contentMode === "narration"
      ? ((episodeScript as NarrationEpisodeScript).segments ?? [])
      : ((episodeScript as DramaEpisodeScript).scenes ?? []);

  // Compute total duration from actual segments if available
  const totalDuration =
    episodeScript.duration_seconds ??
    segments.reduce((sum, s) => sum + s.duration_seconds, 0);

  // Label depends on content mode
  const segmentLabel = contentMode === "narration" ? "个片段" : "个场景";

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-4 space-y-1">
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
        <div className="space-y-4">
          {segments.map((segment) => {
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
                  onUpdatePrompt={onUpdatePrompt}
                  onGenerateStoryboard={onGenerateStoryboard}
                  onGenerateVideo={onGenerateVideo}
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
