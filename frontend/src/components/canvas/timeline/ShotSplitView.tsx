import { useEffect, useState } from "react";
import { cn } from "cn";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import type {
  NarrationSegment,
  DramaScene,
  AdShot,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { getScriptItemId, type EditorContentMode } from "@/utils/script-shape";
import { stepAnchor } from "@/utils/move-anchor";
import { ShotList } from "./ShotList";
import { ShotDetail } from "./ShotDetail";
import type { InsertShotHandler } from "./ShotStructureActions";

type Segment = NarrationSegment | DramaScene | AdShot;

interface ShotSplitViewProps {
  segments: Segment[];
  contentMode: EditorContentMode;
  aspectRatio: "9:16" | "16:9";
  projectName: string;
  /** 当前集号；给了才在分镜详情里提供单条「编写提示词」入口 */
  episode?: number;
  /** 当前剧集剧本文件名，分镜图/视频自主上传需要它定位剧本条目 */
  scriptFile?: string;
  onUpdatePrompt?: (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
  ) => void | Promise<void>;
  /** 分镜改序：移到 afterId 之后，null 移到最前；resolve 为是否移动成功 */
  onMoveShot?: (shotId: string, afterId: string | null) => Promise<boolean>;
  /** 新增分镜（旁白带正文）：afterId 为 null 时追加到末尾；resolve 为是否成功 */
  onInsertShot?: InsertShotHandler;
  /** 移除分镜，resolve 为是否成功 */
  onRemoveShot?: (itemId: string) => Promise<boolean>;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
  generatingStoryboard?: (segmentId: string) => boolean;
  generatingVideo?: (segmentId: string) => boolean;
  generatingNarration?: (segmentId: string) => boolean;
  durationOptions?: number[];
  /** 档位为空是因为这一维由端点固定（workflow 自己定片长），不是型号没登记时长。 */
  durationEndpointFixed?: boolean;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  /** 已保存时长越界的成因判定；缺省时 ShotDetail 退回不区分成因的通用警告文案。 */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
}


/**
 * 分镜分屏：左侧分镜列表（220px，可收为 44px）+ 右侧分镜详情。
 */
export function ShotSplitView({
  segments,
  contentMode,
  aspectRatio,
  projectName,
  episode,
  scriptFile,
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
  lastFrame,
  capabilitiesLoading,
  durationWarningReason,
}: ShotSplitViewProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const [movePending, setMovePending] = useState(false);
  const [structurePending, setStructurePending] = useState(false);

  // 分镜改序：请求在途时丢弃后续操作（快速连点会基于过期顺序计算锚点）。
  // 选中按索引存储，移动成功后按新顺序把选中态跟随到原来选中的分镜。
  const handleMoveShot = onMoveShot
    ? async (shotId: string, afterId: string | null) => {
        if (movePending) return;
        const ids = segments.map((s) => getScriptItemId(s, contentMode));
        const selectedId = ids[Math.min(selectedIndex, ids.length - 1)];
        setMovePending(true);
        try {
          const moved = await onMoveShot(shotId, afterId);
          if (moved) {
            const reordered = ids.filter((id) => id !== shotId);
            reordered.splice(afterId === null ? 0 : reordered.indexOf(afterId) + 1, 0, shotId);
            setSelectedIndex(Math.max(0, reordered.indexOf(selectedId)));
          }
        } finally {
          setMovePending(false);
        }
      }
    : undefined;
  // 详情里的前移、后移一位换算成锚点。
  const handleMoveStep = handleMoveShot
    ? (shotId: string, direction: "earlier" | "later") => {
        const ids = segments.map((s) => getScriptItemId(s, contentMode));
        const afterId = stepAnchor(ids, ids.indexOf(shotId), direction);
        if (afterId !== undefined) return handleMoveShot(shotId, afterId);
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
  const handleInsertShot: InsertShotHandler | undefined = onInsertShot
    ? (afterId, novelText) =>
        runStructureChange(
          () => onInsertShot(afterId, novelText),
          // 插在当前分镜之后的选中紧随其后的新分镜；追加到末尾的选中新的末条。
          () => setSelectedIndex((i) => (afterId === null ? segments.length : i + 1)),
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
      className={cn(
        "grid h-full min-h-0 min-w-0 grid-rows-[minmax(0,1fr)]",
        collapsed ? "grid-cols-[44px_minmax(0,1fr)]" : "grid-cols-[220px_minmax(0,1fr)]",
      )}
    >
      <ShotList
        segments={segments}
        selectedIndex={safeIndex}
        onSelect={setSelectedIndex}
        contentMode={contentMode}
        projectName={projectName}
        aspectRatio={aspectRatio}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        onAppend={handleInsertShot}
        appendDisabled={structurePending || movePending}
        onMove={handleMoveShot}
        moveDisabled={structurePending || movePending}
      />
      <ShotDetail
        key={segmentId}
        segment={segment}
        segmentId={segmentId}
        contentMode={contentMode}
        aspectRatio={aspectRatio}
        projectName={projectName}
        episode={episode}
        scriptFile={scriptFile}
        selectedIndex={safeIndex}
        totalCount={segments.length}
        onPrev={() => setSelectedIndex((i) => Math.max(0, i - 1))}
        onNext={() => setSelectedIndex((i) => Math.min(segments.length - 1, i + 1))}
        onUpdatePrompt={onUpdatePrompt}
        onMoveShot={handleMoveStep}
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
        lastFrame={lastFrame}
        capabilitiesLoading={capabilitiesLoading}
        durationWarningReason={durationWarningReason}
      />
    </div>
  );
}
