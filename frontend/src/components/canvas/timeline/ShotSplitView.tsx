import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
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
import { RetainedEditUnit } from "@/components/shared/edit-unit/RetainedEditUnit";
import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
import { ShotList } from "./ShotList";
import { ShotDetail, type ShotEditHandle } from "./ShotDetail";
import { ShotShortcutHint } from "./ShotShortcutHint";
import type { InsertShotHandler } from "./ShotStructureActions";
import { useShotShortcuts } from "./useShotShortcuts";

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
  /** 提交分镜字段的增量修改，失败时抛错，resolve 为项目是否已刷新；缺省时只读展示。 */
  onUpdatePrompt?: (segmentId: string, patch: Record<string, unknown>) => Promise<boolean>;
  /** 分镜改序：移到 afterId 之后，null 移到最前；resolve 为是否移动成功 */
  onMoveShot?: (shotId: string, afterId: string | null) => Promise<boolean>;
  /** 新增分镜（旁白带正文）：afterId 为 null 时追加到末尾；resolve 为是否成功 */
  onInsertShot?: InsertShotHandler;
  /** 移除分镜，resolve 为是否成功 */
  onRemoveShot?: (itemId: string) => Promise<boolean>;
  onGenerateStoryboard?: (segmentId: string) => void;
  onGenerateVideo?: (segmentId: string) => void | Promise<void>;
  onGenerateNarration?: (segmentId: string) => void;
  onRestoreStoryboard?: () => Promise<unknown> | void;
  onRestoreVideo?: () => Promise<unknown> | void;
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
 * 选中项按分镜 ID 记，列表被重排或在前面插入分镜时选中态不跳走；
 * ID 为 null 或不在当前列表里（刚被移除）时退回记下的位置，并在渲染时认定该位置的 ID。
 */
interface Selection {
  id: string | null;
  index: number;
  /** 新增分镜后按位置选中：列表还是这一份时不认定 ID，等刷新出新分镜。 */
  staleIds?: readonly string[];
}

/**
 * 分镜分屏：左侧分镜列表（220px，可收为 44px）+ 右侧分镜详情。
 * 切换分镜经离开拦截：当前分镜有未保存修改时先询问。J / K 切换分镜，⌘S / Ctrl+S 保存当前分镜。
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
  const { t } = useTranslation("common");
  const [selection, setSelection] = useState<Selection>({ id: null, index: 0 });
  const [collapsed, setCollapsed] = useState(false);
  const [movePending, setMovePending] = useState(false);
  const [structurePending, setStructurePending] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const editRef = useRef<ShotEditHandle | null>(null);
  const confirmLeave = useConfirmLeave();

  const ids = useMemo(() => segments.map((s) => getScriptItemId(s, contentMode)), [segments, contentMode]);
  const selectedById = selection.id === null ? -1 : ids.indexOf(selection.id);
  const safeIndex = selectedById !== -1 ? selectedById : Math.max(0, Math.min(selection.index, ids.length - 1));
  const segmentId = ids[safeIndex];
  if (selection.id === null && segmentId !== undefined && selection.staleIds !== ids) {
    setSelection({ id: segmentId, index: safeIndex });
  }

  /** 切到第 index 个分镜；当前分镜有未保存修改时先由离开拦截询问。 */
  const select = useCallback((index: number) => {
    const id = ids[index];
    if (id === undefined) return;
    if (id === selection.id) {
      setSelection({ id, index });
      return;
    }
    confirmLeave(() => setSelection({ id, index }), { saveLabel: t("save_and_switch") });
  }, [ids, selection.id, confirmLeave, t]);
  const selectPrev = useCallback(() => select(Math.max(0, safeIndex - 1)), [select, safeIndex]);
  const selectNext = useCallback(() => select(Math.min(ids.length - 1, safeIndex + 1)), [select, ids.length, safeIndex]);
  const saveSelected = useCallback(() => editRef.current?.save(), []);

  // 分镜改序：请求在途时丢弃后续操作（快速连点会基于过期顺序计算锚点）。选中态按 ID 跟随原来的分镜。
  const handleMoveShot = onMoveShot
    ? async (shotId: string, afterId: string | null) => {
        if (movePending) return;
        setMovePending(true);
        try {
          await onMoveShot(shotId, afterId);
        } finally {
          setMovePending(false);
        }
      }
    : undefined;
  // 详情里的前移、后移一位换算成锚点。
  const handleMoveStep = handleMoveShot
    ? (shotId: string, direction: "earlier" | "later") => {
        const afterId = stepAnchor(ids, ids.indexOf(shotId), direction);
        if (afterId !== undefined) return handleMoveShot(shotId, afterId);
      }
    : undefined;

  // 新增 / 移除分镜：请求在途锁定切镜与增删入口。新增成功后选中紧随其后的新分镜；
  // 移除成功后位置不动，落到原来的下一条（末条时夹紧到新的末条）。
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
          // 新分镜的 ID 要等列表刷新后才知道，先按位置选中；切走前同样经离开拦截。
          () => {
            const index = afterId === null ? ids.length : safeIndex + 1;
            confirmLeave(() => setSelection({ id: null, index, staleIds: ids }), { saveLabel: t("save_and_switch") });
          },
        )
    : undefined;
  const handleRemoveShot = onRemoveShot
    ? (itemId: string) =>
        runStructureChange(
          () => onRemoveShot(itemId),
          () => setSelection({ id: null, index: safeIndex }),
        )
    : undefined;

  useShotShortcuts({
    rootRef,
    onPrev: selectPrev,
    onNext: selectNext,
    onSave: onUpdatePrompt ? saveSelected : undefined,
    navDisabled: movePending || structurePending,
  });

  // 定位到分镜（通知、制作进度、应用内链接、Agent 改动）：分屏布局只需切换选中分镜，不做 DOM 滚动。
  // 切走前同样经离开拦截；Agent 改动带来的自动定位在有未保存修改时已由事件流自己略过，不会打断编辑。
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  useEffect(() => {
    if (scrollTarget?.type !== "segment") return;
    const idx = ids.indexOf(scrollTarget.id);
    if (idx !== -1) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 响应外部 store 的一次性定位请求切换选中分镜：没有修改时当场切换，有修改时等离开拦截放行后再切
      select(idx);
      clearScrollTarget(scrollTarget.request_id);
    } else if (Date.now() >= scrollTarget.expires_at) {
      // 当前 segments 不含该分镜（如事件指向其他剧集），过期后清理避免下次 segments 变更误触发
      clearScrollTarget(scrollTarget.request_id);
    }
  }, [scrollTarget, ids, select, clearScrollTarget]);



  const segment = segments[safeIndex];

  return (
    <div
      ref={rootRef}
      className={cn(
        "grid h-full min-h-0 min-w-0 grid-rows-[minmax(0,1fr)]",
        collapsed ? "grid-cols-[44px_minmax(0,1fr)]" : "grid-cols-[220px_minmax(0,1fr)]",
      )}
    >
      <ShotList
        segments={segments}
        selectedIndex={safeIndex}
        onSelect={select}
        contentMode={contentMode}
        projectName={projectName}
        aspectRatio={aspectRatio}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        onAppend={handleInsertShot}
        appendDisabled={structurePending || movePending}
        onMove={handleMoveShot}
        moveDisabled={structurePending || movePending}
        footer={<ShotShortcutHint canSave={Boolean(onUpdatePrompt)} />}
      />
      <RetainedEditUnit identity={segmentId ?? "missing"} value={
      segmentId !== undefined && segment ? <ShotDetail
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
        onPrev={selectPrev}
        onNext={selectNext}
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
        editRef={editRef}
      /> : null
      } message={t("dashboard:shot_externally_removed")}>
        {(detail) => detail}
      </RetainedEditUnit>
    </div>
  );
}
