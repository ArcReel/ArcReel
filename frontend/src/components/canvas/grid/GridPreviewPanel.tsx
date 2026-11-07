import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Grid2x2,
  Loader2,
  MapPin,
  Package,
  RefreshCw,
  Scissors,
  Upload,
  User,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { API } from "@/api";
import { enqueueGridRegenerate } from "@/actions/generation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { errMsg } from "@/utils/async";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, useActiveResourceIds, useTasksStore } from "@/stores/tasks-store";
import type { GridGeneration, ReferenceImage } from "@/types/grid";
import { gridImageAspect, type GridImageAspect } from "./grid-preview-model";

export interface GridPreviewPanelProps {
  projectName: string;
  /** Grids matching this group; multiple when the group exceeds one image's cell limit. */
  gridIds: string[];
  /** Group-card writes such as generation are in flight; disable panel actions too. */
  busy?: boolean;
  /** With generation entry points, empty states direct users to group-card headers. */
  canGenerate?: boolean;
  /** Notify parent to refresh grids after successful regeneration submission. */
  onRegenerated?: () => void;
}

type GridDisplayStatus = GridGeneration["status"] | "interrupted";

/**
 * Preload composite placeholder ratio/width: minimum of column width and aspect-ratio width at 70cqh height;
 * correct ratios match loaded natural-size constraints `max-w-full` / `max-h-[70cqh]`, preventing layout shifts.
 * width/height attributes supply aspect ratio only.
 */
const GRID_IMAGE_BOX: Record<GridImageAspect, { width: number; height: number; className: string }> = {
  "16:9": { width: 1920, height: 1080, className: "w-[min(100%,calc(70cqh*16/9))]" },
  "9:16": { width: 1080, height: 1920, className: "w-[min(100%,calc(70cqh*9/16))]" },
  "4:3": { width: 1920, height: 1440, className: "w-[min(100%,calc(70cqh*4/3))]" },
  "3:4": { width: 1440, height: 1920, className: "w-[min(100%,calc(70cqh*3/4))]" },
};

const STATUS_ICON: Record<Exclude<GridDisplayStatus, "generating">, typeof Clock> = {
  pending: Clock,
  completed: CheckCircle2,
  failed: AlertCircle,
  interrupted: AlertCircle,
};

const STATUS_TONE: Record<GridDisplayStatus, string> = {
  pending: "text-muted-foreground",
  generating: "text-primary",
  completed: "text-good",
  failed: "text-destructive",
  interrupted: "text-warn",
};

function GridStatus({ status }: { status: GridDisplayStatus }) {
  const { t } = useTranslation("dashboard");
  const Icon = status === "generating" ? null : STATUS_ICON[status];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${STATUS_TONE[status]}`}>
      {Icon ? (
        <Icon aria-hidden className="size-3.5" />
      ) : (
        <span aria-hidden className="size-1.5 animate-breath rounded-full bg-primary" />
      )}
      {t(`grid_status_${status}`)}
    </span>
  );
}

const REF_ICON: Record<ReferenceImage["ref_type"], typeof User> = {
  character: User,
  scene: MapPin,
  prop: Package,
};

function ReferenceImages({
  references,
  projectName,
}: {
  references: ReferenceImage[];
  projectName: string;
}) {
  const { t } = useTranslation("dashboard");
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-xs font-medium text-muted-foreground">{t("grid_reference_images")}</h4>
      <ul className="flex flex-wrap gap-2">
        {references.map((ref) => {
          const Icon = REF_ICON[ref.ref_type] ?? User;
          return (
            <li key={ref.path} className="flex w-16 flex-col gap-1">
              <img
                src={API.getFileUrl(projectName, ref.path, fingerprints[ref.path] ?? null, { width: 160 })}
                alt=""
                loading="lazy"
                decoding="async"
                className="aspect-square w-full rounded-md border border-border bg-muted object-cover"
              />
              <span className="flex min-w-0 items-center gap-1 text-xs text-subtle-foreground">
                <Icon aria-hidden className="size-3 shrink-0" />
                <TruncatedText text={ref.name} className="min-w-0" />
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * One grid group: composite selection, status/hints, actions (versions/upload/split/regenerate), references, and image.
 * Composite fills remaining media width: below 860px card width, info 1fr/media 300px; otherwise info 280–360px/media remaining.
 */
export function GridPreviewPanel({
  projectName,
  gridIds,
  busy = false,
  canGenerate = false,
  onRegenerated,
}: GridPreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [grid, setGrid] = useState<GridGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [splitting, setSplitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  // Loaded composite URL: use recorded-ratio placeholders before load, natural image sizing afterward.
  const [loadedImageUrl, setLoadedImageUrl] = useState<string | null>(null);
  const project = useProjectsStore((s) => s.currentProjectData);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const multipleGrids = gridIds.length > 1;
  const safeIdx = Math.min(selectedIdx, Math.max(0, gridIds.length - 1));
  const selectedGridId = gridIds[safeIdx] ?? null;

  // Global grid-change signals are the sole refetch trigger.
  const gridsRevision = useAppStore((s) => s.gridsRevision);
  const tasksConnected = useTasksStore((s) => s.connected);

  useEffect(() => {
    if (!selectedGridId) return;
    const controller = new AbortController();
    if (!grid || grid.id !== selectedGridId) {
      // Grid switches clear old data/show loading before asynchronous fetch; clearing also unmounts the
      // version time machine, whose resourceId version fetches lack cancellation. Retaining old data while loading would let
      // prior in-flight version lists enter new panels, restoring with new grid IDs plus old version numbers.
      // eslint-disable-next-line react-hooks/set-state-in-effect -- Grid switches are action-driven resets; clear old data before fetching
      setLoading(true);
      setGrid(null);
    }
    setError(null);

    API.getGrid(projectName, selectedGridId, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setGrid(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(errMsg(err, t("grid_load_failed")));
        setLoading(false);
      });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- grid only detects switching; t is stable
  }, [selectedGridId, projectName, gridsRevision]);

  // Live tasks store occupancy matches same-page sibling controls,
  // replacing grid.status snapshots that update only on fetch and falsely appear idle between submission/refetch.
  const activeGridIds = useActiveResourceIds("grid", projectName);
  const isInProgress = selectedGridId != null && activeGridIds.has(selectedGridId);
  const isInterrupted =
    tasksConnected &&
    selectedGridId != null &&
    grid != null &&
    (grid.status === "pending" || grid.status === "generating") &&
    !isInProgress;
  const displayStatus: GridDisplayStatus = isInterrupted ? "interrupted" : grid?.status ?? "pending";
  // Panel regeneration/split/upload/version-restoration actions are mutually exclusive, writing the same composite/cells;
  // while any is in flight, disable siblings together.
  const actionBusy = busy || regenerating || splitting || uploading || restoring || isInProgress;

  // Fresh submit-time occupancy recheck reads latest tasks store beyond render-time isInProgress snapshots.
  const freshBusy = () =>
    selectedGridId != null && isResourceBusy("grid", projectName, selectedGridId);

  const rejectBusy = () => {
    if (!freshBusy()) return false;
    // Toast instead of setError: whole-panel error state would replace composite/actions;
    // transient occupancy rejection must not destroy current views.
    useAppStore.getState().pushToast(t("grid_regenerate_busy"), "error");
    return true;
  };

  const handleSplit = () => {
    if (!grid || !selectedGridId || actionBusy || rejectBusy()) return;
    setSplitting(true);
    API.splitGrid(projectName, selectedGridId)
      .then((res) => {
        useProjectsStore.getState().updateAssetFingerprints(res.asset_fingerprints);
        // Write back only the grid initiating splitting: actions do not prevent switching, so users may move
        // while requests run; unconditional writeback would stamp split_at on another grid.
        setGrid((prev) => (prev && prev.id === selectedGridId ? { ...prev, split_at: res.split_at } : prev));
        useAppStore
          .getState()
          .pushToast(t("grid_split_success", { count: res.updated_scene_ids.length }), "success");
        if (res.missing_scene_ids.length > 0) {
          useAppStore
            .getState()
            .pushToast(t("grid_split_missing_skipped", { count: res.missing_scene_ids.length }), "error");
        }
      })
      .catch((err: unknown) => {
        // Transient failures toast rather than replacing whole panels through setError.
        useAppStore.getState().pushToast(t("grid_split_failed", { message: errMsg(err) }), "error");
      })
      .finally(() => setSplitting(false));
  };

  const handleUploadFile = (file: File) => {
    if (!selectedGridId || actionBusy || rejectBusy()) return;
    setUploading(true);
    API.uploadGridImage(projectName, selectedGridId, file)
      .then((res) => {
        useProjectsStore.getState().updateAssetFingerprints(res.asset_fingerprints);
        // Composite/grid records (status / split_at) changed; global invalidation refetches details.
        useAppStore.getState().invalidateGrids();
        useAppStore.getState().pushToast(t("grid_upload_success"), "success");
      })
      .catch((err: unknown) => {
        useAppStore.getState().pushToast(t("grid_upload_failed", { message: errMsg(err) }), "error");
      })
      .finally(() => setUploading(false));
  };

  const handleRegenerate = () => {
    if (!selectedGridId || actionBusy || rejectBusy()) return;
    const targetId = selectedGridId;
    setRegenerating(true);
    enqueueGridRegenerate(projectName, targetId, grid?.script_file ?? null)
      .then(() => {
        // As with splitting, requests may finish after switching; update only their originating composite.
        setGrid((prev) => (prev && prev.id === targetId ? { ...prev, status: "pending" } : prev));
        onRegenerated?.();
      })
      .catch((err: unknown) => {
        // Transient failures toast: setError after switching would replace another composite's whole panel.
        useAppStore.getState().pushToast(errMsg(err, t("grid_regenerate_failed")), "error");
      })
      .finally(() => setRegenerating(false));
  };

  // Persisted mtime fingerprints cache-bust across page refreshes. Versioned URLs use immutable caching;
  // versions must come from file fingerprints alone. Until they arrive, omit versions for server-negotiated caching.
  const gridFp = useProjectsStore((s) =>
    grid?.grid_image_path ? (s.assetFingerprints[grid.grid_image_path] ?? null) : null,
  );
  // Panel composite width is at most about 1280px; fetch 1280 thumbnails, not originals.
  const imageUrl = grid?.grid_image_path
    ? API.getFileUrl(projectName, grid.grid_image_path, gridFp, { width: 1280 })
    : null;

  if (gridIds.length === 0) {
    return (
      <div className="flex flex-col items-center gap-1.5 px-4 py-8 text-center">
        <Grid2x2 aria-hidden className="size-6 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">{t("grid_no_grids_yet")}</p>
        {canGenerate && <p className="text-xs text-muted-foreground">{t("grid_generate_instruction")}</p>}
      </div>
    );
  }

  if (error) {
    return (
      <p role="alert" className="m-4 flex items-start gap-2 text-sm text-destructive">
        <AlertCircle aria-hidden className="mt-0.5 size-4 shrink-0" />
        <span className="min-w-0 wrap-anywhere">{error}</span>
      </p>
    );
  }

  if (loading || !grid) {
    return (
      <p className="flex items-center justify-center gap-2 px-4 py-8 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        {t("grid_loading_data")}
      </p>
    );
  }

  const refs = grid.reference_images ?? [];
  const unsplit = grid.status === "completed" && Boolean(grid.grid_image_path) && !grid.split_at;
  const generatingImage = displayStatus === "generating" || displayStatus === "pending";
  const imageBox = GRID_IMAGE_BOX[gridImageAspect(grid, project)];
  const imageLoaded = imageUrl !== null && loadedImageUrl === imageUrl;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_18.75rem] gap-4 p-4 @min-[53.75rem]/grid:grid-cols-[minmax(17.5rem,22.5rem)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        {multipleGrids && (
          <div role="group" aria-label={t("grid_picker_label")} className="flex flex-wrap gap-1">
            {gridIds.map((id, idx) => (
              <Button
                key={id}
                variant={idx === safeIdx ? "secondary" : "ghost"}
                size="xs"
                aria-pressed={idx === safeIdx}
                aria-label={t("grid_picker_item", { index: idx + 1 })}
                onClick={() => setSelectedIdx(idx)}
              >
                <span className="num">{idx + 1}</span>
              </Button>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <GridStatus status={displayStatus} />
          {unsplit && (
            <Badge variant="outline">
              <Scissors aria-hidden data-icon="inline-start" />
              {t("grid_unsplit_hint")}
            </Badge>
          )}
        </div>

        {isInterrupted && <p className="text-xs text-warn">{t("grid_interrupted_hint")}</p>}
        {unsplit && <p className="text-xs text-muted-foreground">{t("grid_unsplit_detail")}</p>}
        {grid.error_message && (
          <p className="text-xs wrap-anywhere text-destructive">{grid.error_message}</p>
        )}

        <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          <span className="shrink-0">
            {t("grid_cell_info", { count: grid.cell_count, rows: grid.rows, cols: grid.cols })}
          </span>
          <span aria-hidden>·</span>
          <TruncatedText text={grid.model} className="min-w-0" />
        </p>

        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="outline" size="sm" disabled={actionBusy} onClick={handleRegenerate}>
            {regenerating || isInProgress ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <RefreshCw aria-hidden data-icon="inline-start" />
            )}
            {regenerating ? t("grid_regenerating") : isInProgress ? t("generating_grid") : t("grid_regenerate_btn")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={actionBusy || !grid.grid_image_path}
            onClick={handleSplit}
          >
            {splitting ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Scissors aria-hidden data-icon="inline-start" />
            )}
            {splitting ? t("grid_splitting") : t("grid_split_btn")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={actionBusy}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploading ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Upload aria-hidden data-icon="inline-start" />
            )}
            {uploading ? t("grid_uploading") : t("grid_upload_btn")}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              // Reset value so reselecting the same file triggers change.
              e.target.value = "";
              if (file) handleUploadFile(file);
            }}
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="grids"
            resourceId={grid.id}
            iconOnly
            busy={actionBusy}
            checkBusy={freshBusy}
            onRestoringChange={setRestoring}
            onRestore={() => {
              // Restoration selects historical composites/resets split state; refetch records to sync split_at.
              useAppStore.getState().invalidateGrids();
            }}
          />
        </div>

        {refs.length > 0 && (
          <ReferenceImages references={refs} projectName={projectName} />
        )}
      </div>

      {imageUrl ? (
        <img
          src={imageUrl}
          alt={t("grid_composite_image_alt")}
          width={imageBox.width}
          height={imageBox.height}
          loading="lazy"
          decoding="async"
          onLoad={() => setLoadedImageUrl(imageUrl)}
          className={cn(
            "block h-auto max-h-[70cqh] max-w-full justify-self-start rounded-md border border-border bg-muted object-contain",
            imageLoaded ? "w-auto" : imageBox.className,
          )}
        />
      ) : (
        <div className="flex min-h-40 items-center justify-center gap-2 rounded-md border border-dashed border-border text-xs text-muted-foreground">
          {generatingImage ? t("generating_grid") : t("grid_no_image")}
        </div>
      )}
    </div>
  );
}
