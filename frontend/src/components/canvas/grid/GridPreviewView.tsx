import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import { groupBySegmentBreak, computeGridSize, matchGridsForGroup } from "@/utils/grid-layout";
import { GridPreviewPanel } from "./GridPreviewPanel";
import type { GridGeneration } from "@/types/grid";
import type { NarrationSegment, DramaScene } from "@/types";

type Segment = NarrationSegment | DramaScene;

type GenerateGrid = (episode: number, scriptFile: string, sceneIds?: string[]) => Promise<void> | void;

interface GridPreviewViewProps {
  projectName: string;
  episode: number;
  scriptFile?: string;
  segments: Segment[];
  contentMode: "narration" | "drama";
  onGenerateGrid?: GenerateGrid;
}

function getSegmentId(seg: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (seg as NarrationSegment).segment_id
    : (seg as DramaScene).scene_id;
}

function GridGroupCard({
  projectName,
  index,
  cellCount,
  rows,
  cols,
  gridIds,
  submitting,
  canGenerate,
  onGenerate,
  onRegenerated,
}: {
  projectName: string;
  index: number;
  cellCount: number;
  rows: number;
  cols: number;
  gridIds: string[];
  submitting: boolean;
  canGenerate: boolean;
  onGenerate: () => void;
  onRegenerated: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const activeGridIds = useActiveResourceIds("grid", projectName);
  // Group generation/panel actions write the same composites; disable each other while any request is in flight.
  const groupBusy = submitting || gridIds.some((id) => activeGridIds.has(id));

  const handleGenerate = () => {
    if (groupBusy) return;
    if (gridIds.some((id) => isResourceBusy("grid", projectName, id))) {
      useAppStore.getState().pushToast(t("grid_regenerate_busy"), "error");
      return;
    }
    onGenerate();
  };

  return (
    <section aria-labelledby={titleId} className="rounded-lg border border-border bg-card">
      <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-4 py-2">
        <h3 id={titleId} className="min-w-0 flex-1 text-sm font-medium">
          {t("grid_preview_batch_card_title", { index, cellCount, rows, cols })}
        </h3>
        {canGenerate && (
          <Button variant="outline" size="sm" disabled={groupBusy} onClick={handleGenerate}>
            {submitting ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Sparkles aria-hidden data-icon="inline-start" />
            )}
            {submitting
              ? t("submitting")
              : gridIds.length > 0
                ? t("grid_group_regenerate")
                : t("grid_preview_batch_generate")}
          </Button>
        )}
      </div>
      <GridPreviewPanel
        projectName={projectName}
        gridIds={gridIds}
        busy={submitting}
        canGenerate={canGenerate}
        onRegenerated={onRegenerated}
      />
    </section>
  );
}

/** Grid-storyboard view: groups by chapter breaks with one card per group; scroll the view and cap composites by its height. */
export function GridPreviewView({
  projectName,
  episode,
  scriptFile,
  segments,
  contentMode,
  onGenerateGrid,
}: GridPreviewViewProps) {
  const { t } = useTranslation("dashboard");
  const gridsRevision = useAppStore((s) => s.gridsRevision);
  const invalidateGrids = useAppStore((s) => s.invalidateGrids);
  const [grids, setGrids] = useState<GridGeneration[]>([]);
  const [generatingGroups, setGeneratingGroups] = useState<Set<string>>(new Set());
  // Backend supplies cell limits: 4×4 / 5×5 4K gating requires provider resolution; frontend derivation
  // inevitably drifts from enqueue rules. Missing limits use conservative computeGridSize defaults.
  const [maxCellCount, setMaxCellCount] = useState<number | undefined>(undefined);

  const groups = useMemo(() => groupBySegmentBreak(segments), [segments]);

  useEffect(() => {
    if (!projectName) return;
    const controller = new AbortController();
    API.getGridCapability(projectName, { signal: controller.signal })
      .then((cap) => {
        if (!controller.signal.aborted) setMaxCellCount(cap.max_cell_count);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [projectName]);

  // Global invalidation refetches grid lists; generation/upload/restoration call invalidateGrids.
  useEffect(() => {
    if (!projectName) return;
    const controller = new AbortController();
    API.listGrids(projectName, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setGrids(data);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [projectName, gridsRevision]);

  const handleGenerateGroup = useCallback(
    // Group keys join sorted sceneIds, preserving spinner ownership across reordering.
    async (groupKey: string, group: Segment[]) => {
      if (!onGenerateGrid || !scriptFile) return;
      const sceneIds = group.map((s) => getSegmentId(s, contentMode));
      setGeneratingGroups((prev) => new Set(prev).add(groupKey));
      try {
        await onGenerateGrid(episode, scriptFile, sceneIds);
      } finally {
        setGeneratingGroups((prev) => {
          const next = new Set(prev);
          next.delete(groupKey);
          return next;
        });
        invalidateGrids();
      }
    },
    [onGenerateGrid, scriptFile, contentMode, episode, invalidateGrids],
  );

  const stats = useMemo(() => {
    // Oversized groups split into multiple backend grids; count actual enqueued images.
    const batches = groups.reduce(
      (sum, group) => sum + computeGridSize(group.length, maxCellCount).batchCount,
      0,
    );
    const readyGroups = groups.filter((group) => {
      const sceneIds = group.map((s) => getSegmentId(s, contentMode));
      // Multi-image groups are ready only when every part has a matching completed grid.
      const groupGrids = matchGridsForGroup(grids, sceneIds, episode, maxCellCount);
      return (
        groupGrids.length === computeGridSize(group.length, maxCellCount).batchCount &&
        groupGrids.every((g) => g.status === "completed")
      );
    }).length;
    return { batches, cells: segments.length, groups: groups.length, ready: readyGroups };
  }, [groups, segments, grids, episode, contentMode, maxCellCount]);

  if (segments.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        {t("grid_preview_empty_episode")}
      </div>
    );
  }

  const canGenerate = Boolean(onGenerateGrid && scriptFile);

  return (
    <div className="@container-size/grid relative h-full overflow-y-auto [scrollbar-gutter:stable]">
      <div className="flex flex-col gap-4 px-6 py-5">
        <p className="text-sm text-muted-foreground">{t("grid_preview_summary", stats)}</p>
        {groups.map((group, idx) => {
          const sceneIds = group.map((s) => getSegmentId(s, contentMode));
          const layout = computeGridSize(group.length, maxCellCount);
          const gridIds = matchGridsForGroup(grids, sceneIds, episode, maxCellCount).map((g) => g.id);
          const groupKey = [...sceneIds].sort().join(",");
          return (
            <GridGroupCard
              key={groupKey}
              projectName={projectName}
              index={idx + 1}
              cellCount={group.length}
              rows={layout.rows}
              cols={layout.cols}
              gridIds={gridIds}
              submitting={generatingGroups.has(groupKey)}
              canGenerate={canGenerate}
              onGenerate={() => void handleGenerateGroup(groupKey, group)}
              onRegenerated={invalidateGrids}
            />
          );
        })}
      </div>
    </div>
  );
}
