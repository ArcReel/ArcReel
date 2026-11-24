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
  episode?: number;
  scriptFile?: string;
  onUpdatePrompt?: (segmentId: string, patch: Record<string, unknown>) => Promise<boolean>;
  onMoveShot?: (shotId: string, afterId: string | null) => Promise<boolean>;
  onInsertShot?: InsertShotHandler;
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
  durationEndpointFixed?: boolean;
  lastFrame?: boolean | null;
  capabilitiesLoading?: boolean;
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
}

interface Selection {
  id: string | null;
  index: number;
  staleIds?: readonly string[];
}

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
  const handleMoveStep = handleMoveShot
    ? (shotId: string, direction: "earlier" | "later") => {
        const afterId = stepAnchor(ids, ids.indexOf(shotId), direction);
        if (afterId !== undefined) return handleMoveShot(shotId, afterId);
      }
    : undefined;

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
        previousStoryboardPath={segments[safeIndex - 1]?.generated_assets?.storyboard_image}
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
