import { Grid2x2, Loader2, Sparkles } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useTranslation } from "react-i18next";
import type { NarrationSegment, DramaScene } from "@/types";
import { GridPreviewPanel } from "./GridPreviewPanel";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Segment = NarrationSegment | DramaScene;

export interface GridSegmentGroupProps {
  groupIndex: number;
  scenes: Segment[];
  gridSize: string | null; // "grid_4" | "grid_6" | "grid_9" or null if < 1
  sceneCount: number;
  /** Number of grid batches (> 1 when scene count exceeds cell capacity) */
  batchCount?: number;
  onGenerateGrid: () => void;
  generatingGrid: boolean;
  children: React.ReactNode;
  /** Grid IDs for showing preview panels (one per batch) */
  gridIds?: string[];
  /** Project name — required when gridIds is provided */
  projectName?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const GRID_LABEL: Record<string, string> = {
  grid_4: "grid_4 (2\u00d72)",
  grid_6: "grid_6 (2\u00d73)",
  grid_9: "grid_9 (3\u00d73)",
};

function groupLabel(index: number): string {
  // A, B, C, ... Z, AA, AB, ...
  let label = "";
  let n = index;
  do {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return label;
}

// ---------------------------------------------------------------------------
// GridSegmentGroup
// ---------------------------------------------------------------------------

/**
 * Wrapper component for a group of SegmentCards in grid generation mode.
 * Shows a header with group label, grid size info, and a generate button.
 */
export function GridSegmentGroup({
  groupIndex,
  scenes,
  gridSize,
  sceneCount,
  batchCount = 1,
  onGenerateGrid,
  generatingGrid,
  children,
  gridIds = [],
  projectName = "",
}: GridSegmentGroupProps) {
  const { t } = useTranslation("dashboard");
  const label = groupLabel(groupIndex);
  const gridInfo = gridSize ? GRID_LABEL[gridSize] ?? gridSize : null;
  const canGenerate = gridSize !== null;

  // Derive scene IDs for the preview panel
  const sceneIds = scenes.map((s) =>
    "scene_id" in s ? (s as DramaScene).scene_id : (s as NarrationSegment).segment_id
  );

  return (
    <div className="mb-6">
      {/* ---- Group header ---- */}
      <div className="mb-3 flex items-center justify-between rounded-lg border border-amber-800/30 bg-amber-950/20 px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <Grid2x2 className="h-4 w-4 text-amber-500/70" />
          <span className="text-sm font-medium text-amber-400/90">
            Segment {label}
          </span>
          <span className="text-xs text-gray-500">
            {t("grid_scene_count", { count: sceneCount })}
            {gridInfo && batchCount > 1 ? (
              <>
                {" \u2192 "}
                <span className="font-mono text-amber-500/70">{t("grid_batch_count", { count: batchCount })}</span>
                <span className="text-gray-600">{" "}({gridInfo})</span>
              </>
            ) : gridInfo ? (
              <>
                {" \u2192 "}
                <span className="font-mono text-amber-500/70">{gridInfo}</span>
              </>
            ) : null}
          </span>
        </div>

        {/* Generate grid button */}
        <motion.button
          type="button"
          onClick={onGenerateGrid}
          disabled={!canGenerate || generatingGrid}
          title={!canGenerate ? t("insufficient_scenes_for_grid") : undefined}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
            generatingGrid
              ? "bg-blue-700 text-white"
              : canGenerate
                ? "bg-blue-600 text-white hover:bg-blue-500"
                : "bg-gray-800 text-gray-600 cursor-not-allowed"
          } ${!canGenerate || generatingGrid ? "opacity-50 cursor-not-allowed" : ""}`}
          animate={
            generatingGrid
              ? { opacity: [0.7, 1, 0.7] }
              : { opacity: !canGenerate ? 0.5 : 1 }
          }
          transition={
            generatingGrid
              ? { duration: 1.5, repeat: Infinity, ease: "easeInOut" }
              : { duration: 0.3 }
          }
        >
          <AnimatePresence mode="wait" initial={false}>
            {generatingGrid ? (
              <motion.span
                key="loader"
                initial={{ opacity: 0, rotate: -90 }}
                animate={{ opacity: 1, rotate: 0 }}
                exit={{ opacity: 0, rotate: 90 }}
                transition={{ duration: 0.2 }}
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              </motion.span>
            ) : (
              <motion.span
                key="sparkles"
                initial={{ opacity: 0, scale: 0.5 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.5 }}
                transition={{ duration: 0.2 }}
              >
                <Sparkles className="h-3.5 w-3.5" />
              </motion.span>
            )}
          </AnimatePresence>
          <span>{generatingGrid ? t("generating_grid") : t("generate_grid_btn")}</span>
        </motion.button>
      </div>

      {/* ---- Grid Preview Panels (one per batch, above cards) ---- */}
      {projectName && gridIds.length > 0
        ? gridIds.map((gid) => (
            <GridPreviewPanel
              key={gid}
              projectName={projectName}
              gridId={gid}
              sceneIds={sceneIds}
              onRegenerate={onGenerateGrid}
            />
          ))
        : projectName && (
            <GridPreviewPanel
              projectName={projectName}
              gridId={null}
              sceneIds={sceneIds}
              onRegenerate={onGenerateGrid}
            />
          )}

      {/* ---- Children (SegmentCards) ---- */}
      <div className="flex flex-col gap-4">{children}</div>
    </div>
  );
}
