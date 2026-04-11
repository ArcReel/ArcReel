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
import { API } from "@/api";
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
}

// ---------------------------------------------------------------------------
// StatusBadge
// ---------------------------------------------------------------------------

type GridStatus = GridGeneration["status"];

function StatusBadge({ status }: { status: GridStatus }) {
  const configs: Record<
    GridStatus,
    { label: string; icon: React.ReactNode; cls: string }
  > = {
    pending: {
      label: "待处理",
      icon: <Clock className="h-3 w-3" />,
      cls: "bg-gray-800/80 text-gray-400 border-gray-700/50",
    },
    generating: {
      label: "生成中",
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
      cls: "bg-blue-950/60 text-blue-300 border-blue-700/40",
    },
    splitting: {
      label: "切分中",
      icon: <Scissors className="h-3 w-3" />,
      cls: "bg-violet-950/60 text-violet-300 border-violet-700/40",
    },
    completed: {
      label: "已完成",
      icon: <CheckCircle2 className="h-3 w-3" />,
      cls: "bg-emerald-950/60 text-emerald-400 border-emerald-700/40",
    },
    failed: {
      label: "失败",
      icon: <AlertCircle className="h-3 w-3" />,
      cls: "bg-red-950/60 text-red-400 border-red-700/40",
    },
  };

  const { label, icon, cls } = configs[status];

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
}: GridPreviewPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [grid, setGrid] = useState<GridGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);

  const hasGrids = gridIds.length > 0;
  const multipleGrids = gridIds.length > 1;
  const safeIdx = Math.min(selectedIdx, Math.max(0, gridIds.length - 1));
  const selectedGridId = gridIds[safeIdx] ?? null;

  // Clamp selection when grid list changes
  useEffect(() => {
    if (selectedIdx >= gridIds.length && gridIds.length > 0) {
      setSelectedIdx(0);
    }
  }, [gridIds.length, selectedIdx]);

  // Fetch grid data when expanded and selectedGridId is available
  useEffect(() => {
    if (!expanded || !selectedGridId) return;

    let cancelled = false;
    // Only show loading spinner when we have no data for this grid
    if (!grid || grid.id !== selectedGridId) setLoading(true);
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
          setError(err instanceof Error ? err.message : "加载失败");
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
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
        className="flex w-full items-center gap-2 border-t border-amber-800/20 px-4 py-1.5 text-left transition-colors hover:bg-amber-900/10 focus:outline-none"
      >
        <motion.span
          animate={{ rotate: expanded ? 90 : 0 }}
          transition={{ duration: 0.18, ease: "easeInOut" }}
          className="text-amber-600/70"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </motion.span>

        <Film className="h-3.5 w-3.5 text-amber-500/60" />

        <span className="text-xs font-medium text-amber-400/70">宫格预览</span>

        {!hasGrids && (
          <span className="ml-1 text-[10px] text-gray-600">尚未生成</span>
        )}

        {multipleGrids && !expanded && (
          <span className="ml-1 text-[10px] text-gray-600">
            {gridIds.length} 批
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
                  <p className="text-xs text-gray-600">尚未生成宫格</p>
                  <p className="text-[10px] text-gray-700">
                    点击上方「生成宫格」按钮开始
                  </p>
                </div>
              ) : loading && !grid ? (
                /* Loading state (initial) */
                <div className="flex items-center justify-center gap-2 py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-amber-500/50" />
                  <span className="text-xs text-gray-600">加载宫格数据...</span>
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

                    <StatusBadge status={grid.status} />

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
                            setError(err instanceof Error ? err.message : "重新生成失败");
                          })
                          .finally(() => setRegenerating(false));
                      }}
                      className={`ml-auto shrink-0 whitespace-nowrap inline-flex items-center gap-1 rounded border border-amber-800/30 bg-amber-950/30 px-2 py-1 text-[10px] font-medium text-amber-400/80 transition-colors ${
                        regenerating || isInProgress ? "opacity-50 cursor-not-allowed" : "hover:bg-amber-900/40 hover:text-amber-300"
                      }`}
                      whileTap={regenerating || isInProgress ? {} : { scale: 0.95 }}
                    >
                      <RefreshCw className={`h-3 w-3 ${regenerating || isInProgress ? "animate-spin" : ""}`} />
                      {regenerating ? "提交中..." : isInProgress ? "生成中..." : "重新生成"}
                    </motion.button>
                  </div>

                  {/* Composite image + metadata */}
                  {imageUrl ? (
                    <div className="overflow-hidden rounded-md border border-gray-800/50 bg-gray-900/40">
                      <img
                        src={imageUrl}
                        alt="宫格合成图"
                        className="block max-h-64 w-full object-contain bg-black/20"
                      />
                      <div className="flex items-center gap-2 border-t border-gray-800/50 px-2.5 py-1.5">
                        <span className="font-mono text-[10px] text-gray-500">
                          {grid.cell_count} 格 · {grid.grid_size}
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
                          ? "生成中..."
                          : "无图像"}
                      </span>
                    </div>
                  )}

                  {/* Reference images strip */}
                  {refs.length > 0 && (
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[9px] font-medium uppercase tracking-widest text-gray-600">
                        参考图
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
