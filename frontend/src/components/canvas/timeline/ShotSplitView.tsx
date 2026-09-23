import { useEffect, useRef, useState } from "react";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import type {
  NarrationSegment,
  DramaScene,
  AdShot,
  ReferenceGenerationRequestOptions,
} from "@/types";
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
  /** 广告/短片分镜顺序调整，resolve 为是否移动成功 */
  onMoveShot?: (shotId: string, direction: "earlier" | "later") => Promise<boolean>;
  /** 在分镜之后新增分镜（旁白带正文），resolve 为是否成功 */
  onInsertShot?: (afterId: string, novelText?: string) => Promise<boolean>;
  /** 移除分镜，resolve 为是否成功 */
  onRemoveShot?: (itemId: string) => Promise<boolean>;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (
    segmentId: string,
    requestOptions?: ReferenceGenerationRequestOptions,
  ) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  generatingStoryboard?: (segmentId: string) => boolean;
  generatingVideo?: (segmentId: string) => boolean;
  generatingNarration?: (segmentId: string) => boolean;
  durationOptions?: number[];
  /** 档位为空是因为这一维由端点固定（workflow 自己定片长），不是型号没登记时长。 */
  durationEndpointFixed?: boolean;
  /** 已保存时长越界的成因判定；缺省时 ShotDetail 退回不区分成因的通用警告文案。 */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
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
  onInsertShot,
  onRemoveShot,
  onGenerateStoryboard,
  onGenerateVideo,
  onGenerateNarration,
  onRestoreStoryboard,
  onRestoreVideo,
  generatingStoryboard,
  generatingVideo,
  generatingNarration,
  durationOptions,
  durationEndpointFixed,
  durationWarningReason,
}: ShotSplitViewProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 1100,
  );
  const [movePending, setMovePending] = useState(false);
  const [structurePending, setStructurePending] = useState(false);
  const listScrollRef = useRef<HTMLDivElement>(null);

  // 分镜重排：请求在途时丢弃后续点击（快速连点会基于过期顺序计算出相同排列），
  // 移动成功后把选中态跟随到分镜的新位置——选中按索引存储，不跟随会静默切到被换位的邻居。
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

  // 新增 / 移除分镜：请求在途锁定切镜与增删入口。新增成功后选中紧随其后的新分镜；
  // 移除成功后索引不动，落到原来的下一条（末条时由越界保护夹紧到新的末条）。
  const runStructureChange = async (change: () => Promise<boolean>, onSuccess: () => void) => {
    if (structurePending) return false;
    setStructurePending(true);
    try {
      const changed = await change();
      if (changed) onSuccess();
      return changed;
    } finally {
      setStructurePending(false);
    }
  };
  const handleInsertShot = onInsertShot
    ? (afterId: string, novelText?: string) =>
        runStructureChange(
          () => onInsertShot(afterId, novelText),
          () => setSelectedIndex((i) => i + 1),
        )
    : undefined;
  const handleRemoveShot = onRemoveShot
    ? (itemId: string) => runStructureChange(() => onRemoveShot(itemId), () => {})
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
        onInsertShot={handleInsertShot}
        onRemoveShot={handleRemoveShot}
        structurePending={structurePending}
        onGenerateStoryboard={onGenerateStoryboard}
        onGenerateVideo={onGenerateVideo}
        onGenerateNarration={onGenerateNarration}
        onRestoreStoryboard={onRestoreStoryboard}
        onRestoreVideo={onRestoreVideo}
        generatingStoryboard={generatingStoryboard?.(segmentId)}
        generatingVideo={generatingVideo?.(segmentId)}
        generatingNarration={generatingNarration?.(segmentId)}
        durationOptions={durationOptions}
        durationEndpointFixed={durationEndpointFixed}
        durationWarningReason={durationWarningReason}
      />
    </div>
  );
}
