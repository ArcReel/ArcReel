import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Download, Loader2, RotateCcw, Trash2 } from "lucide-react";

import { API } from "@/api";
import { TruncatedText } from "@/components/shared/TruncatedText";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { episodeDisplayName, episodeItemLabel, type EpisodeLedger } from "@/utils/episode-display";
import type { RestoreResult, TrashListing, TrashRecord } from "@/types";
import { errMsg } from "@/utils/async";
import { formatShortDateTime } from "@/utils/date-format";
import { downloadBlob } from "@/utils/download";
import { formatFileSize } from "@/utils/file-size";

type ListingState = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; listing: TrashListing };

const NO_EPISODES: EpisodeLedger = [];

const ASSET_TYPES = ["character", "scene", "prop", "product"] as const;

function isAssetType(value: unknown): value is (typeof ASSET_TYPES)[number] {
  return ASSET_TYPES.includes(value as (typeof ASSET_TYPES)[number]);
}

/**
 * Shots/video units use deletion-time label snapshots: episode name · 5, taking current ledger names.
 * Old records without snapshots return null; callers fall back to IDs.
 */
function itemAtDeletion(t: TFunction<"dashboard">, object: TrashRecord["object"], episodes: EpisodeLedger) {
  if (typeof object.episode !== "number" || typeof object.item_position !== "number") return null;
  return `${episodeDisplayName(episodes, object.episode, t)} · ${object.item_position}`;
}

/** Append deletion-time body excerpts to distinguish same-episode records; names only without excerpts. */
function withExcerpt(t: TFunction<"dashboard">, name: string, object: TrashRecord["object"]): string {
  const excerpt = object.item_excerpt;
  return typeof excerpt === "string" && excerpt !== "" ? t("trash_object_with_excerpt", { name, excerpt }) : name;
}

/** Object name by type; file-only objects use deleted paths. */
function objectTitle(
  t: TFunction<"dashboard">,
  object: TrashRecord["object"],
  episodes: EpisodeLedger,
): string | undefined {
  if (object.kind === "edit_timeline" && typeof object.name === "string") {
    return t("trash_object_edit_timeline", { name: object.name, episode: object.episode });
  }
  if (object.kind === "episode") {
    return typeof object.title === "string" && object.title !== ""
      ? t("trash_object_episode", { name: object.title })
      : t("trash_object_episode_untitled", { episode: object.id });
  }
  if (object.kind === "grid") return t("trash_object_grid", { episode: object.episode });
  if (object.kind === "end_frame") {
    const shot = itemAtDeletion(t, object, episodes);
    return shot === null
      ? t("trash_object_end_frame", { shot: object.shot_id })
      : withExcerpt(t, t("trash_object_end_frame", { shot }), object);
  }
  if (object.kind === "reference_audio") return t("trash_object_reference_audio", { name: object.character });
  if (object.kind === "asset" && isAssetType(object.asset_type) && typeof object.name === "string") {
    return t(`trash_object_asset.${object.asset_type}`, { name: object.name });
  }
  if (object.kind === "asset_derivative" && typeof object.name === "string") {
    return t("trash_object_asset_derivative", { name: object.name, owner: object.owner });
  }
  if (object.kind === "source_file" && typeof object.path === "string") {
    return t("trash_object_source_file", { name: object.path.split("/").pop() });
  }
  if (object.kind === "planning_draft") return t("trash_object_planning_draft", { episode: object.episode });
  if (object.kind === "script_entry") {
    const unit = object.item_kind === "video_units";
    const item = itemAtDeletion(t, object, episodes);
    if (item === null) {
      return t(unit ? "trash_object_script_unit" : "trash_object_script_entry", { id: object.id, episode: object.episode });
    }
    return withExcerpt(t, t(unit ? "trash_object_script_unit_at" : "trash_object_script_entry_at", { item }), object);
  }
  return object.path;
}

function recordTitle(t: TFunction<"dashboard">, record: TrashRecord, episodes: EpisodeLedger): string {
  return objectTitle(t, record.object, episodes) ?? record.files[0] ?? record.id;
}

/** Restore notices explain recovered/download-only content after degradation, new names on rename, and actual destinations after moves. */
function restoredMessage(t: TFunction<"dashboard">, record: TrashRecord, result: RestoreResult): string {
  const { currentScripts, currentProjectData } = useProjectsStore.getState();
  const episodes = currentProjectData?.episodes ?? [];
  const name = recordTitle(t, record, episodes);
  if (result.degraded) {
    return result.download_only.length > 0
      ? t("trash_restored_degraded", { name, count: result.restored.length })
      : t("trash_restored_degraded_files", { name, count: result.restored.length });
  }
  if (record.object.kind === "episode") return t("trash_restored_episode", { name });
  if (record.object.kind === "script_entry") {
    const label = episodeItemLabel(String(result.object.id), currentScripts, episodes, t);
    return t("trash_restored_object", { name: label });
  }
  // Named objects (timelines) describe renaming by name; others describe file destinations.
  if (record.has_snapshot && typeof record.object.name === "string") {
    const landed = result.object.name;
    return typeof landed === "string" && landed !== record.object.name
      ? t("trash_restored_object_renamed", { name, landed })
      : t("trash_restored_object", { name });
  }
  if (result.restored.some((item) => item.as_history)) return t("trash_restored_as_history", { name });
  const renamed = result.restored.find((item) => item.path !== item.original);
  return renamed ? t("trash_restored_renamed", { name, path: renamed.path }) : t("trash_restored", { name });
}

/** Results whose targets disappeared before generation completion: explain origin separately, not as someone's deletion. */
function recordMetaKey(record: TrashRecord): string {
  return record.object.note === "target_deleted_during_generation"
    ? "trash_record_meta_target_deleted"
    : `trash_record_meta_${record.origin}`;
}

interface PurgeRequest {
  /** Permanently deleted record; null for emptying, conflicting with every restoring record. */
  recordId?: string;
  title: string;
  description: string;
  confirmLabel: string;
  run: () => Promise<unknown>;
}

/**
 * Files-view Trash lists deletion records for restore, permanent deletion, or emptying.
 * Restore executes directly since it can be deleted again; irreversible permanent deletion/emptying requires AlertDialog confirmation.
 */
export function TrashSection({ projectName, refreshKey }: { projectName: string; refreshKey: number }) {
  const { t } = useTranslation("dashboard");
  const trashRevision = useAppStore((s) => s.trashRevision);
  const episodes = useProjectsStore((s) => s.currentProjectData?.episodes) ?? NO_EPISODES;
  const [state, setState] = useState<ListingState>({ status: "loading" });
  // Multiple records restore concurrently, each owning/releasing only its occupancy.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [purge, setPurge] = useState<PurgeRequest | null>(null);

  // Automatic refetch/retry share one controller: each load cancels prior requests, preventing stale-response overwrites.
  const controllerRef = useRef<AbortController | null>(null);
  const load = useCallback(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const { signal } = controller;
    API.getTrash(projectName, { signal })
      .then((listing) => {
        if (!signal.aborted) setState({ status: "ready", listing });
      })
      .catch((error: unknown) => {
        if (!signal.aborted) setState({ status: "error", message: errMsg(error) });
      });
  }, [projectName]);

  useEffect(() => {
    load();
    const controllers = controllerRef;
    return () => controllers.current?.abort();
  }, [load, trashRevision, refreshKey]);

  const afterChange = () => useAppStore.getState().invalidateTrash();

  /**
   * Immediately remove server-confirmed records rather than awaiting refetch; otherwise restored/deleted records remain actionable after occupancy clears.
   * In-flight lists may predate changes; cancel first and trust subsequent refetches.
   */
  const dropRecords = (keep: (record: TrashRecord) => boolean) => {
    controllerRef.current?.abort();
    setState((current) => {
      if (current.status !== "ready") return current;
      const records = current.listing.records.filter(keep);
      const totalSize = records.reduce((total, record) => total + record.size, 0);
      return { status: "ready", listing: { ...current.listing, records, total_size: totalSize } };
    });
  };

  const restore = async (record: TrashRecord) => {
    setBusyIds((current) => new Set(current).add(record.id));
    try {
      const result = await API.restoreTrashRecord(projectName, record.id);
      // Degraded restores leaving download-only structures retain records; subsequent refetch shows download-only state.
      if (result.download_only.length === 0) dropRecords((item) => item.id !== record.id);
      if (record.object.kind === "script_entry" && !result.degraded) {
        if (await refreshAfterWrite(projectName, t) !== "success") return;
      }
      useAppStore.getState().pushToast(restoredMessage(t, record, result), result.degraded ? "warning" : "success");
    } catch (error) {
      useAppStore.getState().pushToast(t("trash_action_failed", { message: errMsg(error) }), "error");
    } finally {
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(record.id);
        return next;
      });
      afterChange();
    }
  };

  if (state.status === "loading") {
    return <p className="px-6 py-4 text-sm text-muted-foreground">{t("file_view_loading")}</p>;
  }
  if (state.status === "error") {
    return (
      <div className="flex flex-col items-start gap-2 px-6 py-4 text-sm text-destructive">
        <p className="wrap-break-word">{t("trash_load_failed", { message: state.message })}</p>
        <Button variant="outline" size="sm" onClick={() => load()}>
          {t("file_view_retry")}
        </Button>
      </div>
    );
  }

  const { records, total_size: totalSize, retention_days: retentionDays } = state.listing;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Emptying aligns left: narrow-screen Agent panels overlay right canvas controls. */}
      <div className="flex shrink-0 flex-col items-start gap-2 border-b border-border px-6 py-3">
        <p className="min-w-0 text-sm text-subtle-foreground">
          {t("trash_summary", { total: records.length, size: formatFileSize(totalSize), days: retentionDays })}
        </p>
        <Button
          variant="destructive"
          size="sm"
          disabled={records.length === 0 || busyIds.size > 0}
          onClick={() =>
            setPurge({
              title: t("trash_empty_title"),
              description: t("trash_empty_description", { total: records.length }),
              confirmLabel: t("trash_empty"),
              run: () => API.emptyTrash(projectName),
            })
          }
        >
          <Trash2 data-icon="inline-start" aria-hidden />
          {t("trash_empty")}
        </Button>
      </div>
      {records.length === 0 ? (
        <div className="flex flex-1 items-center justify-center">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Trash2 aria-hidden />
              </EmptyMedia>
              <EmptyTitle>{t("trash_empty_state_title")}</EmptyTitle>
              <EmptyDescription>{t("trash_empty_state_description", { days: retentionDays })}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      ) : (
        <ul className="relative flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-2 [scrollbar-gutter:stable]">
          {records.map((record) => {
            const busy = busyIds.has(record.id);
            return (
              <li key={record.id} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-muted/40">
                <div className="flex min-w-0 flex-1 flex-col">
                  {itemAtDeletion(t, record.object, episodes) === null ? (
                    <TruncatedText text={recordTitle(t, record, episodes)} className="text-sm" />
                  ) : (
                    <span className="wrap-break-word text-sm">{recordTitle(t, record, episodes)}</span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {t(recordMetaKey(record), {
                      time: formatShortDateTime(record.deleted_at) ?? record.deleted_at,
                    })}
                    {" · "}
                    <span className="num">{formatFileSize(record.size)}</span>
                  </span>
                  {record.download_only && (
                    <DownloadOnlyFiles projectName={projectName} record={record} />
                  )}
                </div>
                {!record.download_only && (
                  <Button variant="outline" size="sm" disabled={busy} onClick={() => void restore(record)}>
                    {busy ? (
                      <Loader2 data-icon="inline-start" aria-hidden className="animate-spin" />
                    ) : (
                      <RotateCcw data-icon="inline-start" aria-hidden />
                    )}
                    {t("trash_restore")}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    setPurge({
                      recordId: record.id,
                      title: t("trash_purge_title"),
                      description: t("trash_purge_description", { name: recordTitle(t, record, episodes) }),
                      confirmLabel: t("trash_purge"),
                      run: () => API.purgeTrashRecord(projectName, record.id),
                    })
                  }
                >
                  {t("trash_purge")}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <PurgeConfirmDialog
        request={purge}
        onSucceeded={(request) => dropRecords(request.recordId ? (item) => item.id !== request.recordId : () => false)}
        // Restores starting after confirmation opens also block it; recheck current occupancy on submission.
        blocked={purge !== null && (purge.recordId ? busyIds.has(purge.recordId) : busyIds.size > 0)}
        onClose={() => setPurge(null)}
        onDone={afterChange}
      />
    </div>
  );
}

/** Structures left after degraded restoration: explain download-only reasons and provide individual downloads. */
function DownloadOnlyFiles({ projectName, record }: { projectName: string; record: TrashRecord }) {
  const { t } = useTranslation("dashboard");
  const [downloading, setDownloading] = useState<string | null>(null);

  const download = async (path: string) => {
    const name = path.split("/").pop() ?? path;
    setDownloading(path);
    try {
      downloadBlob(await API.downloadTrashRecordFile(projectName, record.id, path), name);
    } catch (error) {
      useAppStore.getState().pushToast(t("file_view_download_failed", { name, message: errMsg(error) }), "error");
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div className="mt-1 flex min-w-0 flex-col items-start gap-1">
      <p className="text-xs text-warn">{t("trash_download_only_hint")}</p>
      <ul className="flex min-w-0 max-w-full flex-col items-start">
        {record.files.map((path) => (
          <li key={path} className="max-w-full">
            <Button
              variant="link"
              size="xs"
              className="max-w-full"
              disabled={downloading !== null}
              aria-label={t("trash_download_file", { path })}
              onClick={() => void download(path)}
            >
              {downloading === path ? (
                <Loader2 data-icon="inline-start" aria-hidden className="animate-spin" />
              ) : (
                <Download data-icon="inline-start" aria-hidden />
              )}
              <TruncatedText text={path} focusable={false} className="font-mono text-xs" />
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Permanent deletion/emptying confirmation, irreversible; null `request` closes. */
function PurgeConfirmDialog({
  request,
  blocked,
  onSucceeded,
  onClose,
  onDone,
}: {
  request: PurgeRequest | null;
  blocked: boolean;
  onSucceeded: (request: PurgeRequest) => void;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Retain the last content during closing animation; clear the previous error when the request changes
  const [shown, setShown] = useState(request);
  if (request !== null && request !== shown) {
    setShown(request);
    setError(null);
  }

  const confirm = async () => {
    if (!shown || running || blocked) return;
    setRunning(true);
    setError(null);
    try {
      await shown.run();
      onSucceeded(shown);
      onDone();
      onClose();
    } catch (err) {
      setError(t("trash_action_failed", { message: errMsg(err) }));
    } finally {
      setRunning(false);
    }
  };

  return (
    <AlertDialog
      open={request !== null}
      onOpenChange={(next) => {
        // Ignore Escape during submission so the dialog cannot disappear before the request completes
        if (!next && !running) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{shown?.title}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>{shown?.description}</AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={running}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={running || blocked} onClick={() => void confirm()}>
            {running && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {shown?.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
