import { useCallback, useEffect, useRef, useState } from "react";
import type { NarrationSegment, DramaScene } from "@/types";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { ShotList } from "./ShotList";
import { ShotDetail } from "./ShotDetail";

type Segment = NarrationSegment | DramaScene;

interface ShotSplitViewProps {
  segments: Segment[];
  contentMode: "narration" | "drama";
  aspectRatio: "9:16" | "16:9";
  projectName: string;
  isGridMode?: boolean;
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
  ) => void | Promise<void>;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  generatingStoryboard?: (segmentId: string) => boolean;
  generatingVideo?: (segmentId: string) => boolean;
}

function getSegmentId(seg: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (seg as NarrationSegment).segment_id
    : (seg as DramaScene).scene_id;
}

/**
 * 分镜分屏：左 ShotList + 右 ShotDetail。窄屏时左列折叠到 44px。
 */
export function ShotSplitView({
  segments,
  contentMode,
  aspectRatio,
  projectName,
  isGridMode,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard,
  generatingVideo,
}: ShotSplitViewProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 1100,
  );
  const listScrollRef = useRef<HTMLDivElement>(null);

  // 切镜时索引超界保护
  useEffect(() => {
    if (selectedIndex >= segments.length && segments.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 段数变更时夹紧索引
      setSelectedIndex(segments.length - 1);
    }
  }, [segments.length, selectedIndex]);

  const prepareScroll = useCallback(
    (target: { id: string }) => {
      const idx = segments.findIndex((s) => getSegmentId(s, contentMode) === target.id);
      if (idx === -1) return false;
      setSelectedIndex(idx);
      return true;
    },
    [segments, contentMode],
  );
  useScrollTarget("segment", { prepareTarget: prepareScroll });

  if (segments.length === 0) {
    return null;
  }

  const safeIndex = Math.min(selectedIndex, segments.length - 1);
  const segment = segments[safeIndex];
  const segmentId = getSegmentId(segment, contentMode);

  return (
    <div
      className="grid h-full min-w-0 overflow-hidden"
      style={{
        gridTemplateColumns: collapsed ? "44px minmax(0, 1fr)" : "220px minmax(0, 1fr)",
        gridTemplateRows: "minmax(0, 1fr)",
      }}
    >
      <ShotList
        segments={segments}
        selectedIndex={safeIndex}
        onSelect={setSelectedIndex}
        contentMode={contentMode}
        projectName={projectName}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        scrollContainerRef={listScrollRef}
      />
      <ShotDetail
        key={segmentId}
        segment={segment}
        segmentId={segmentId}
        contentMode={contentMode}
        aspectRatio={aspectRatio}
        projectName={projectName}
        isGridMode={isGridMode}
        selectedIndex={safeIndex}
        totalCount={segments.length}
        onPrev={() => setSelectedIndex((i) => Math.max(0, i - 1))}
        onNext={() => setSelectedIndex((i) => Math.min(segments.length - 1, i + 1))}
        onUpdatePrompt={onUpdatePrompt}
        onGenerateStoryboard={onGenerateStoryboard}
        onGenerateVideo={onGenerateVideo}
        onRestoreStoryboard={onRestoreStoryboard}
        onRestoreVideo={onRestoreVideo}
        generatingStoryboard={generatingStoryboard?.(segmentId)}
        generatingVideo={generatingVideo?.(segmentId)}
      />
    </div>
  );
}
