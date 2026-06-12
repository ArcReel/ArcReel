import { useEffect, useRef, useState } from "react";
import type { NarrationSegment, DramaScene, AdShot } from "@/types";
import { useAppStore } from "@/stores/app-store";
import { getScriptItemId, type EditorContentMode } from "@/utils/script-shape";
import { ShotList } from "./ShotList";
import { ShotDetail } from "./ShotDetail";

type Segment = NarrationSegment | DramaScene | AdShot;

interface ShotSplitViewProps {
  segments: Segment[];
  contentMode: EditorContentMode;
  aspectRatio: "9:16" | "16:9";
  projectName: string;
  /** 当前剧集剧本文件名，分镜图/视频自主上传需要它定位剧本条目 */
  scriptFile?: string;
  isGridMode?: boolean;
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
  ) => void | Promise<void>;
  /** ad 模式镜头顺序调整，resolve 为是否移动成功 */
  onMoveShot?: (shotId: string, direction: "earlier" | "later") => Promise<boolean>;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void;
  onGenerateNarration?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  generatingStoryboard?: (segmentId: string) => boolean;
  generatingVideo?: (segmentId: string) => boolean;
  generatingNarration?: (segmentId: string) => boolean;
  durationOptions?: number[];
}


/**
 * 分镜分屏：左 ShotList + 右 ShotDetail。窄屏时左列折叠到 44px。
 */
export function ShotSplitView({
  segments,
  contentMode,
  aspectRatio,
  projectName,
  scriptFile,
  isGridMode,
  onUpdatePrompt,
  onMoveShot,
  onGenerateStoryboard,
  onGenerateVideo,
  onGenerateNarration,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard,
  generatingVideo,
  generatingNarration,
  durationOptions,
}: ShotSplitViewProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 1100,
  );
  const [movePending, setMovePending] = useState(false);
  const listScrollRef = useRef<HTMLDivElement>(null);

  // 镜头重排：请求在途时丢弃后续点击（快速连点会基于过期顺序计算出相同排列），
  // 移动成功后把选中态跟随到镜头的新位置——选中按索引存储，不跟随会静默切到被换位的邻居。
  const handleMoveShot = onMoveShot
    ? async (shotId: string, direction: "earlier" | "later") => {
        if (movePending) return;
        setMovePending(true);
        try {
          const moved = await onMoveShot(shotId, direction);
          if (moved) {
            setSelectedIndex((i) =>
              direction === "earlier" ? Math.max(0, i - 1) : Math.min(segments.length - 1, i + 1),
            );
          }
        } finally {
          setMovePending(false);
        }
      }
    : undefined;

  // 切镜时索引超界保护
  useEffect(() => {
    if (selectedIndex >= segments.length && segments.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 段数变更时夹紧索引
      setSelectedIndex(segments.length - 1);
    }
  }, [segments.length, selectedIndex]);

  // SSE 自动定位：分屏布局只需切换 selectedIndex，不做 DOM 滚动
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  useEffect(() => {
    if (scrollTarget?.type !== "segment") return;
    const idx = segments.findIndex((s) => getScriptItemId(s, contentMode) === scrollTarget.id);
    if (idx !== -1) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 订阅 SSE 项目事件 store，触发后切换选中分镜
      setSelectedIndex(idx);
      clearScrollTarget(scrollTarget.request_id);
    } else if (Date.now() >= scrollTarget.expires_at) {
      // 当前 segments 不含该分镜（如事件指向其他剧集），过期后清理避免下次 segments 变更误触发
      clearScrollTarget(scrollTarget.request_id);
    }
  }, [scrollTarget, segments, contentMode, clearScrollTarget]);

  if (segments.length === 0) {
    return null;
  }

  const safeIndex = Math.min(selectedIndex, segments.length - 1);
  const segment = segments[safeIndex];
  const segmentId = getScriptItemId(segment, contentMode);

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
        scriptFile={scriptFile}
        isGridMode={isGridMode}
        selectedIndex={safeIndex}
        totalCount={segments.length}
        onPrev={() => setSelectedIndex((i) => Math.max(0, i - 1))}
        onNext={() => setSelectedIndex((i) => Math.min(segments.length - 1, i + 1))}
        onUpdatePrompt={onUpdatePrompt}
        onMoveShot={handleMoveShot}
        movePending={movePending}
        onGenerateStoryboard={onGenerateStoryboard}
        onGenerateVideo={onGenerateVideo}
        onGenerateNarration={onGenerateNarration}
        onRestoreStoryboard={onRestoreStoryboard}
        onRestoreVideo={onRestoreVideo}
        generatingStoryboard={generatingStoryboard?.(segmentId)}
        generatingVideo={generatingVideo?.(segmentId)}
        generatingNarration={generatingNarration?.(segmentId)}
        durationOptions={durationOptions}
      />
    </div>
  );
}
