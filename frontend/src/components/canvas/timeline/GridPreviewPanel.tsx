import { useState, useEffect } from "react";
import {
  ChevronRight,
  RefreshCw,
  Loader2,
  Grid2x2,
  Film,
  AlertCircle,
  CheckCircle2,
  Clock,
  Scissors,
  User,
  Search,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { GridGeneration, ReferenceImage } from "@/types/grid";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GridPreviewPanelProps {
  projectName: string;
  gridIds: string[];
  /** Called after a regeneration is submitted (for parent to refresh grids list). */
  onRegenerated?: () => void;
  /** Changed when grids list is refreshed, triggers re-fetch of panel data. */
  refreshKey?: number;
  /** Render in expanded state on first mount. Used by the dedicated grid preview tab. */
  defaultExpanded?: boolean;
}

// ---------------------------------------------------------------------------
// StatusBadge
// ---------------------------------------------------------------------------

type GridStatus = GridGeneration["status"];

function StatusBadge({ status, t }: { status: GridStatus; t: (key: string) => string }) {
  const STATUS_KEY: Record<GridStatus, string> = {
    pending: "grid_status_pending",
    generating: "grid_status_generating",
    splitting: "grid_status_splitting",
    completed: "grid_status_completed",
    failed: "grid_status_failed",
  };

  const configs: Record<
    GridStatus,
    { icon: React.ReactNode; cls: string }
  > = {
    pending: {
      icon: <Clock className="h-3 w-3" />,
      cls: "bg-gray-800/80 text-gray-400 border-gray-700/50",
    },
    generating: {
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
      cls: "bg-blue-950/60 text-blue-300 border-blue-700/40",
    },
    splitting: {
      icon: <Scissors className="h-3 w-3" />,
      cls: "bg-violet-950/60 text-violet-300 border-violet-700/40",
    },
    completed: {
      icon: <CheckCircle2 className="h-3 w-3" />,
      cls: "bg-emerald-950/60 text-emerald-400 border-emerald-700/40",
    },
    failed: {
      icon: <AlertCircle className="h-3 w-3" />,
      cls: "bg-red-950/60 text-red-400 border-red-700/40",
    },
  };

  const { icon, cls } = configs[status];
  const label = t(STATUS_KEY[status]);

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium tracking-wide ${cls}`}
    >
      {icon}
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ReferenceImageStrip
// ---------------------------------------------------------------------------

function ReferenceImageStrip({
  references,
  projectName,
  refreshKey,
}: {
  references: ReferenceImage[];
  projectName: string;
  refreshKey: number;
}) {
  return (
    <div className="flex gap-2.5 overflow-x-auto pb-1 scrollbar-thin">
      {references.map((ref, idx) => {
        const isChar = ref.ref_type === "character";
        return (
          <motion.div
            key={ref.path}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: idx * 0.05, duration: 0.2 }}
            className="group flex w-14 shrink-0 flex-col items-center gap-1"
          >
            <div
              className={`w-full overflow-hidden rounded border bg-gray-900/50 transition-all duration-200 ${
                isChar
                  ? "border-amber-800/30 group-hover:border-amber-500/50"
                  : "border-sky-800/30 group-hover:border-sky-500/50"
              }`}
            >
              <img
                src={API.getFileUrl(projectName, ref.path, refreshKey)}
                alt={ref.name}
                className="block aspect-square w-full object-cover transition-transform duration-200 group-hover:scale-105"
              />
            </div>
            <div className="flex max-w-full items-center gap-0.5">
              {isChar ? (
                <User className="h-2 w-2 shrink-0 text-amber-500/50" />
              ) : (
                <Search className="h-2 w-2 shrink-0 text-sky-500/50" />
              )}
              <span
                className={`truncate text-[8px] leading-tight ${
                  isChar ? "text-amber-400/50" : "text-sky-400/50"
                }`}
                title={ref.name}
              >
                {ref.name}
              </span>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// GridPreviewPanel
// ---------------------------------------------------------------------------

export function GridPreviewPanel({
  projectName,
  gridIds,
  onRegenerated,
  refreshKey = 0,
  defaultExpanded = false,
}: GridPreviewPanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [grid, setGrid] = useState<GridGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const { t } = useTranslation("dashboard");

  const hasGrids = gridIds.length > 0;
  const multipleGrids = gridIds.length > 1;
  const safeIdx = Math.min(selectedIdx, Math.max(0, gridIds.length - 1));
  const selectedGridId = gridIds[safeIdx] ?? null;

  // safeIdx already clamps selectedIdx to valid range; no effect needed

  // Fetch grid data when expanded and selectedGridId is available
  useEffect(() => {
    if (!expanded || !selectedGridId) return;

    let cancelled = false;
    // Clear stale data and show spinner when switching batches
    if (!grid || grid.id !== selectedGridId) {
      setLoading(true);
      setGrid(null);
    }
    setError(null);

    API.getGrid(projectName, selectedGridId)
      .then((data) => {
        if (!cancelled) {
          setGrid(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(errMsg(err, t("grid_load_failed")));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- grid 用于判断是否切换批次，加入 deps 会在每次拉取完成后触发重新拉取，导致无限循环；t 稳定可忽略
  }, [expanded, selectedGridId, projectName, refreshKey]);

  const isInProgress =
    grid?.status === "pending" || grid?.status === "generating" || grid?.status === "splitting";

  const imageUrl =
    grid?.grid_image_path
      ? API.getFileUrl(projectName, grid.grid_image_path, refreshKey)
      : null;

  const refs = grid?.reference_images ?? [];

  return (
    <div>
      {/* Toggle header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 border-t border-amber-800/20 px-4 py-1.5 text-left transition-colors hover:bg-amber-900/10 focus-ring"
      >
        <motion.span
          animate={{ rotate: expanded ? 90 : 0 }}
          transition={{ duration: 0.18, ease: "easeInOut" }}
          className="text-amber-600/70"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </motion.span>

        <Film className="h-3.5 w-3.5 text-amber-500/60" />

        <span className="text-xs font-medium text-amber-400/70">{t("grid_preview_title")}</span>

        {!hasGrids && (
          <span className="ml-1 text-[10px] text-gray-600">{t("grid_not_generated")}</span>
        )}

        {multipleGrids && !expanded && (
          <span className="ml-1 text-[10px] text-gray-600">
            {t("grid_batch_unit", { count: gridIds.length })}
          </span>
        )}
      </button>

      {/* Collapsible content */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="panel-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-3 pt-2">
              {/* No grid yet */}
              {!hasGrids ? (
                <div className="flex flex-col items-center gap-2 py-6 text-center">
                  <Grid2x2 className="h-8 w-8 text-gray-700" />
                  <p className="text-xs text-gray-600">{t("grid_no_grids_yet")}</p>
                  <p className="text-[10px] text-gray-700">
                    {t("grid_generate_instruction")}
                  </p>
                </div>
              ) : loading && !grid ? (
                /* Loading state (initial) */
                <div className="flex items-center justify-center gap-2 py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-amber-500/50" />
                  <span className="text-xs text-gray-600">{t("grid_loading_data")}</span>
                </div>
              ) : error ? (
                /* Error state */
                <div className="flex items-center gap-2 rounded-md border border-red-900/30 bg-red-950/20 px-3 py-2.5">
                  <AlertCircle className="h-4 w-4 shrink-0 text-red-500/70" />
                  <span className="text-xs text-red-400/80">{error}</span>
                </div>
              ) : grid ? (
                /* Grid loaded */
                <div className="flex flex-col gap-3">
                  {/* Top bar: batch pills + status + regen */}
                  <div className="flex items-center gap-2">
                    {multipleGrids && (
                      <div className="flex items-center gap-0.5 rounded-md bg-gray-900/50 p-0.5">
                        {gridIds.map((_, idx) => (
                          <button
                            key={idx}
                            type="button"
                            onClick={() => setSelectedIdx(idx)}
                            className={`inline-flex h-5 min-w-[1.375rem] items-center justify-center rounded px-1 text-[10px] font-medium tabular-nums transition-all duration-150 ${
                              idx === safeIdx
                                ? "bg-amber-700/50 text-amber-200 shadow-sm"
                                : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/60"
                            }`}
                          >
                            {idx + 1}
                          </button>
                        ))}
                      </div>
                    )}

                    <StatusBadge status={grid.status} t={t} />

                    <span className="text-[10px] text-gray-600">
                      {grid.model}
                    </span>

                    {grid.error_message && (
                      <span
                        className="truncate text-[10px] text-red-400/70"
                        title={grid.error_message}
                      >
                        {grid.error_message}
                      </span>
                    )}

                    <motion.button
                      type="button"
                      disabled={regenerating || isInProgress}
                      onClick={() => {
                        if (!selectedGridId || regenerating || isInProgress) return;
                        setRegenerating(true);
                        API.regenerateGrid(projectName, selectedGridId)
                          .then(() => {
                            setGrid((prev) => prev ? { ...prev, status: "pending" } : prev);
                            onRegenerated?.();
                          })
                          .catch((err: unknown) => {
                            setError(errMsg(err, t("grid_regenerate_failed")));
                          })
                          .finally(() => setRegenerating(false));
                      }}
                      className={`ml-auto shrink-0 whitespace-nowrap inline-flex items-center gap-1 rounded border border-amber-800/30 bg-amber-950/30 px-2 py-1 text-[10px] font-medium text-amber-400/80 transition-colors ${
                        regenerating || isInProgress ? "opacity-50 cursor-not-allowed" : "hover:bg-amber-900/40 hover:text-amber-300"
                      }`}
                      whileTap={regenerating || isInProgress ? {} : { scale: 0.95 }}
                    >
                      <RefreshCw className={`h-3 w-3 ${regenerating || isInProgress ? "animate-spin" : ""}`} />
                      {regenerating ? t("grid_regenerating") : isInProgress ? t("generating_grid") : t("grid_regenerate_btn")}
                    </motion.button>
                  </div>

                  {/* Composite image + metadata */}
                  {imageUrl ? (
                    <div className="overflow-hidden rounded-md border border-gray-800/50 bg-gray-900/40">
                      <img
                        src={imageUrl}
                        alt={t("grid_composite_image_alt")}
                        className="block max-h-64 w-full object-contain bg-black/20"
                      />
                      <div className="flex items-center gap-2 border-t border-gray-800/50 px-2.5 py-1.5">
                        <span className="font-mono text-[10px] text-gray-500">
                          {t("grid_cell_info", { count: grid.cell_count, size: grid.grid_size })}
                        </span>
                        <span className="text-[10px] text-gray-700">
                          {grid.rows}×{grid.cols}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="flex h-24 items-center justify-center rounded-md border border-gray-800/40 bg-gray-900/30">
                      <span className="text-[10px] text-gray-700">
                        {grid.status === "generating" || grid.status === "pending"
                          ? t("generating_grid")
                          : t("grid_no_image")}
                      </span>
                    </div>
                  )}

                  {/* Reference images strip */}
                  {refs.length > 0 && (
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[9px] font-medium uppercase tracking-widest text-gray-600">
                        {t("grid_reference_images")}
                      </span>
                      <ReferenceImageStrip
                        references={refs}
                        projectName={projectName}
                        refreshKey={refreshKey}
                      />
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
