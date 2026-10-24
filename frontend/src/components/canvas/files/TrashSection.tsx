import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RotateCcw, Trash2 } from "lucide-react";

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
import type { TrashListing, TrashRecord } from "@/types";
import { errMsg } from "@/utils/async";
import { formatShortDateTime } from "@/utils/date-format";
import { formatFileSize } from "@/utils/file-size";

type ListingState = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; listing: TrashListing };

/** 记录的对象名：只涉及文件的记录取被删文件的路径。 */
function recordTitle(record: TrashRecord): string {
  return record.object.path ?? record.files[0] ?? record.id;
}

interface PurgeRequest {
  title: string;
  description: string;
  confirmLabel: string;
  run: () => Promise<unknown>;
}

/**
 * 文件视图的「回收站」分区：列出删除记录，可以恢复、彻底删除或清空。
 * 恢复可以再删回去，直接执行；彻底删除与清空不可逆，先用 AlertDialog 确认。
 */
export function TrashSection({ projectName, refreshKey }: { projectName: string; refreshKey: number }) {
  const { t } = useTranslation("dashboard");
  const trashRevision = useAppStore((s) => s.trashRevision);
  const [state, setState] = useState<ListingState>({ status: "loading" });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [purge, setPurge] = useState<PurgeRequest | null>(null);

  const load = useCallback(
    (signal?: AbortSignal) => {
      API.getTrash(projectName, { signal })
        .then((listing) => {
          if (!signal?.aborted) setState({ status: "ready", listing });
        })
        .catch((error: unknown) => {
          if (!signal?.aborted) setState({ status: "error", message: errMsg(error) });
        });
    },
    [projectName],
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load, trashRevision, refreshKey]);

  const afterChange = () => useAppStore.getState().invalidateTrash();

  const restore = async (record: TrashRecord) => {
    setBusyId(record.id);
    try {
      const { restored } = await API.restoreTrashRecord(projectName, record.id);
      const asHistory = restored.some((item) => item.as_history);
      const renamed = restored.find((item) => !item.as_history && item.path !== item.original);
      const text = asHistory
        ? t("trash_restored_as_history", { name: recordTitle(record) })
        : renamed
          ? t("trash_restored_renamed", { name: recordTitle(record), path: renamed.path })
          : t("trash_restored", { name: recordTitle(record) });
      useAppStore.getState().pushToast(text, "success");
    } catch (error) {
      useAppStore.getState().pushToast(t("trash_action_failed", { message: errMsg(error) }), "error");
    } finally {
      setBusyId(null);
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
      {/* 清空按钮靠左：窄屏下 Agent 面板浮在画布右侧，靠右的控件会被盖住 */}
      <div className="flex shrink-0 flex-col items-start gap-2 border-b border-border px-6 py-3">
        <p className="min-w-0 text-sm text-subtle-foreground">
          {t("trash_summary", { total: records.length, size: formatFileSize(totalSize), days: retentionDays })}
        </p>
        <Button
          variant="destructive"
          size="sm"
          disabled={records.length === 0}
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
            const busy = busyId === record.id;
            return (
              <li key={record.id} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-muted/40">
                <div className="flex min-w-0 flex-1 flex-col">
                  <TruncatedText text={recordTitle(record)} className="text-sm" />
                  <span className="text-xs text-muted-foreground">
                    {t(`trash_record_meta_${record.origin}`, {
                      time: formatShortDateTime(record.deleted_at) ?? record.deleted_at,
                    })}
                    {" · "}
                    <span className="num">{formatFileSize(record.size)}</span>
                  </span>
                </div>
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void restore(record)}>
                  {busy ? (
                    <Loader2 data-icon="inline-start" aria-hidden className="animate-spin" />
                  ) : (
                    <RotateCcw data-icon="inline-start" aria-hidden />
                  )}
                  {t("trash_restore")}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    setPurge({
                      title: t("trash_purge_title"),
                      description: t("trash_purge_description", { name: recordTitle(record) }),
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
      <PurgeConfirmDialog request={purge} onClose={() => setPurge(null)} onDone={afterChange} />
    </div>
  );
}

/** 彻底删除与清空的确认：不可恢复。`request` 为 null 时关闭。 */
function PurgeConfirmDialog({
  request,
  onClose,
  onDone,
}: {
  request: PurgeRequest | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 关闭动画期间沿用上一次的内容；换了请求时清掉上一次的错误
  const [shown, setShown] = useState(request);
  if (request !== null && request !== shown) {
    setShown(request);
    setError(null);
  }

  const confirm = async () => {
    if (!shown || running) return;
    setRunning(true);
    setError(null);
    try {
      await shown.run();
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
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
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
          <AlertDialogAction variant="destructive" disabled={running} onClick={() => void confirm()}>
            {running && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {shown?.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
