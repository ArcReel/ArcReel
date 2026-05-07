import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { groupBySegmentBreak, computeGridSize } from "@/utils/grid-layout";
import { GridPreviewPanel } from "@/components/canvas/timeline/GridPreviewPanel";
import type { GridGeneration } from "@/types/grid";
import type { NarrationSegment, DramaScene } from "@/types";

type Segment = NarrationSegment | DramaScene;

interface GridPreviewViewProps {
  projectName: string;
  episode: number;
  scriptFile?: string;
  segments: Segment[];
  contentMode: "narration" | "drama";
  aspectRatio: "9:16" | "16:9";
  onGenerateGrid?: (episode: number, scriptFile: string, sceneIds?: string[]) => void;
}

function getSegmentId(seg: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (seg as NarrationSegment).segment_id
    : (seg as DramaScene).scene_id;
}

export function GridPreviewView({
  projectName,
  episode,
  scriptFile,
  segments,
  contentMode,
  aspectRatio,
  onGenerateGrid,
}: GridPreviewViewProps) {
  const { t } = useTranslation("dashboard");
  const gridsRevision = useAppStore((s) => s.gridsRevision);
  const [grids, setGrids] = useState<GridGeneration[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [generatingGroups, setGeneratingGroups] = useState<Set<number>>(new Set());

  const groups = useMemo(() => groupBySegmentBreak(segments), [segments]);

  const refreshGrids = useCallback(() => {
    if (!projectName) return;
    API.listGrids(projectName)
      .then((data) => {
        setGrids(data);
        setRefreshKey((v) => v + 1);
      })
      .catch(() => {});
  }, [projectName]);

  useEffect(() => {
    refreshGrids();
  }, [refreshGrids, gridsRevision]);

  const getGridIdsForGroup = useCallback(
    (groupSegs: Segment[]): string[] => {
      const idSet = new Set(groupSegs.map((s) => getSegmentId(s, contentMode)));
      const matched = grids.filter(
        (g) =>
          g.episode === episode &&
          g.scene_ids.length > 0 &&
          g.scene_ids.every((id) => idSet.has(id)),
      );
      const byKey = new Map<string, (typeof matched)[number]>();
      for (const g of matched) {
        const key = [...g.scene_ids].sort().join(",");
        const existing = byKey.get(key);
        if (!existing || g.created_at > existing.created_at) {
          byKey.set(key, g);
        }
      }
      return Array.from(byKey.values())
        .sort((a, b) => a.created_at.localeCompare(b.created_at))
        .map((g) => g.id);
    },
    [grids, episode, contentMode],
  );

  const handleGenerateGroup = useCallback(
    (groupIdx: number, group: Segment[]) => {
      if (!onGenerateGrid || !scriptFile) return;
      const sceneIds = group.map((s) => getSegmentId(s, contentMode));
      setGeneratingGroups((prev) => new Set(prev).add(groupIdx));
      onGenerateGrid(episode, scriptFile, sceneIds);
      setTimeout(() => {
        setGeneratingGroups((prev) => {
          const next = new Set(prev);
          next.delete(groupIdx);
          return next;
        });
        refreshGrids();
      }, 3000);
    },
    [onGenerateGrid, scriptFile, contentMode, episode, refreshGrids],
  );

  const stats = useMemo(() => {
    const batches = groups.length;
    const cells = segments.length;
    const readyBatches = groups.filter((group) => {
      const ids = new Set(group.map((s) => getSegmentId(s, contentMode)));
      return grids.some(
        (g) =>
          g.episode === episode &&
          g.status === "completed" &&
          g.scene_ids.length > 0 &&
          g.scene_ids.every((id) => ids.has(id)),
      );
    }).length;
    const percent = batches > 0 ? Math.round((readyBatches / batches) * 100) : 0;
    return { batches, cells, percent };
  }, [groups, segments, grids, episode, contentMode]);

  if (segments.length === 0) {
    return (
      <div
        className="flex h-full items-center justify-center text-sm"
        style={{ color: "var(--color-text-4)" }}
      >
        {t("grid_preview_empty_episode")}
      </div>
    );
  }

  const canGenerate = Boolean(onGenerateGrid && scriptFile);

  return (
    <div className="h-full overflow-y-auto px-5 py-4">
      <div
        className="mb-4 flex flex-wrap items-center gap-2 rounded-md border px-3.5 py-2.5"
        style={{
          borderColor: "var(--color-hairline-soft)",
          background: "oklch(0.18 0.010 265 / 0.5)",
        }}
      >
        <span
          className="num text-[11.5px] tabular-nums"
          style={{ color: "var(--color-text-3)", fontFamily: "var(--font-mono)" }}
        >
          {t("grid_preview_summary", stats)}
        </span>
      </div>

      <div className="flex flex-col gap-3">
        {groups.map((group, idx) => {
          const layout = computeGridSize(group.length, aspectRatio);
          const ids = getGridIdsForGroup(group);
          const generating = generatingGroups.has(idx);
          return (
            <div
              key={idx}
              className="overflow-hidden rounded-md border"
              style={{
                borderColor: "var(--color-hairline-soft)",
                background: "oklch(0.20 0.011 265 / 0.35)",
              }}
            >
              <div
                className="flex items-center gap-2 px-4 py-2"
                style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
              >
                <span
                  className="num text-[11px] font-semibold uppercase tracking-wider"
                  style={{
                    color: "var(--color-text-3)",
                    fontFamily: "var(--font-mono)",
                    letterSpacing: "0.6px",
                  }}
                >
                  {t("grid_preview_batch_card_title", {
                    index: idx + 1,
                    cellCount: group.length,
                    rows: layout.rows,
                    cols: layout.cols,
                  })}
                </span>
                <span className="flex-1" />
                {canGenerate && (
                  <button
                    type="button"
                    onClick={() => handleGenerateGroup(idx, group)}
                    disabled={generating}
                    className="sv-navbtn inline-flex items-center gap-1.5"
                  >
                    {generating ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Sparkles className="h-3 w-3" />
                    )}
                    <span>
                      {generating
                        ? t("submitting")
                        : ids.length > 0
                          ? t("grid_regenerate_btn")
                          : t("grid_preview_batch_generate")}
                    </span>
                  </button>
                )}
              </div>
              <GridPreviewPanel
                projectName={projectName}
                gridIds={ids}
                onRegenerated={refreshGrids}
                refreshKey={refreshKey}
                defaultExpanded
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
