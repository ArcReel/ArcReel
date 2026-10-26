import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
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
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { useAppStore } from "@/stores/app-store";
import type { RefreshProjectResult } from "@/stores/projects-store";
import type { DeletedProject, DeletedProjectListing } from "@/types";
import { errMsg } from "@/utils/async";
import { formatShortDateTime } from "@/utils/date-format";
import { formatFileSize } from "@/utils/file-size";
import { getProjectDisplayName } from "@/utils/project-display";

type ListingState = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; listing: DeletedProjectListing };

interface DeletedProjectsDialogProps {
  open: boolean;
  onClose: () => void;
  /** 有项目恢复回来，重新加载项目列表并返回结算值；刷新失败时不报告恢复成功。 */
  onRestored: () => Promise<RefreshProjectResult>;
}

/**
 * 项目列表页的「已删除项目」：可以整体恢复或彻底删除。恢复直接执行（可以再删回去），彻底删除先二次确认。
 * 组件常驻在页面上、只切换对话框开合，关闭对话框时在途操作的占用不会丢。
 */
export function DeletedProjectsDialog({ open, onClose, onRestored }: DeletedProjectsDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [state, setState] = useState<ListingState>({ status: "loading" });
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [purging, setPurging] = useState<DeletedProject | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const { signal } = controller;
    API.listDeletedProjects({ signal })
      .then((listing) => {
        if (!signal.aborted) setState({ status: "ready", listing });
      })
      .catch((error: unknown) => {
        if (!signal.aborted) setState({ status: "error", message: errMsg(error) });
      });
  }, []);

  useEffect(() => {
    if (!open) return;
    load();
    const controllers = controllerRef;
    return () => controllers.current?.abort();
  }, [open, load]);

  const titleOf = (project: DeletedProject) => getProjectDisplayName(project.title, project.name);

  /**
   * 服务端已确认移走的项目立即从列表里拿掉，不等重拉，免得解除占用后又能被操作一次。
   * 再重拉一次：作废的在途列表可能带着这期间新删除的项目。
   */
  const dropProject = (id: string) => {
    setState((current) =>
      current.status === "ready"
        ? { status: "ready", listing: { ...current.listing, projects: current.listing.projects.filter((p) => p.id !== id) } }
        : current,
    );
    load();
  };

  const setBusy = (id: string, busy: boolean) =>
    setBusyIds((current) => {
      const next = new Set(current);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });

  const restore = async (project: DeletedProject) => {
    setBusy(project.id, true);
    try {
      const { name } = await API.restoreDeletedProject(project.id);
      dropProject(project.id);
      // 成功提示只在项目列表刷新成功后给：失败时已有刷新失败的提示，被新一轮刷新或离开页面作废时静默。
      if ((await onRestored()) !== "success") return;
      const title = titleOf(project);
      useAppStore
        .getState()
        .pushToast(
          name === project.name
            ? t("dashboard:deleted_projects_restored", { title })
            : t("dashboard:deleted_projects_restored_renamed", { title, name }),
          "success",
        );
    } catch (error) {
      useAppStore.getState().pushToast(t("dashboard:trash_action_failed", { message: errMsg(error) }), "error");
    } finally {
      setBusy(project.id, false);
    }
  };

  let body;
  if (state.status === "loading") {
    body = <p className="text-sm text-muted-foreground">{t("dashboard:deleted_projects_loading")}</p>;
  } else if (state.status === "error") {
    body = (
      <div className="flex flex-col items-start gap-2 text-sm text-destructive">
        <p className="wrap-break-word">{t("dashboard:deleted_projects_load_failed", { message: state.message })}</p>
        <Button variant="outline" size="sm" onClick={() => load()}>
          {t("dashboard:file_view_retry")}
        </Button>
      </div>
    );
  } else if (state.listing.projects.length === 0) {
    body = (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>{t("dashboard:deleted_projects_empty")}</EmptyTitle>
        </EmptyHeader>
      </Empty>
    );
  } else {
    body = (
      <ul className="flex flex-col">
        {state.listing.projects.map((project) => {
          const busy = busyIds.has(project.id);
          const title = titleOf(project);
          return (
            <li key={project.id} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-muted/40">
              <div className="flex min-w-0 flex-1 flex-col">
                <TruncatedText text={title} className="text-sm" />
                <span className="text-xs text-muted-foreground">
                  {t("dashboard:deleted_projects_meta", {
                    time: formatShortDateTime(project.deleted_at) ?? project.deleted_at,
                    expires: formatShortDateTime(project.expires_at) ?? project.expires_at,
                    size: formatFileSize(project.size),
                  })}
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                aria-label={t("dashboard:deleted_projects_restore_label", { title })}
                onClick={() => void restore(project)}
              >
                {busy ? (
                  <Loader2 data-icon="inline-start" aria-hidden className="animate-spin" />
                ) : (
                  <RotateCcw data-icon="inline-start" aria-hidden />
                )}
                {t("dashboard:trash_restore")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                aria-label={t("dashboard:deleted_projects_purge_label", { title })}
                onClick={() => setPurging(project)}
              >
                {t("dashboard:trash_purge")}
              </Button>
            </li>
          );
        })}
      </ul>
    );
  }

  const retentionDays = state.status === "ready" ? state.listing.retention_days : 30;
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{t("dashboard:lobby_deleted_projects")}</DialogTitle>
          <DialogDescription>{t("dashboard:deleted_projects_description", { days: retentionDays })}</DialogDescription>
        </DialogHeader>
        <DialogBody>{body}</DialogBody>
      </DialogContent>
      <PurgeDeletedProjectDialog
        project={purging}
        title={purging ? titleOf(purging) : ""}
        blocked={purging !== null && busyIds.has(purging.id)}
        onClose={() => setPurging(null)}
        onPurged={dropProject}
      />
    </Dialog>
  );
}

/** 彻底删除已删除项目的二次确认：不可恢复。`project` 为 null 时关闭。 */
function PurgeDeletedProjectDialog({
  project,
  title,
  blocked,
  onClose,
  onPurged,
}: {
  project: DeletedProject | null;
  title: string;
  blocked: boolean;
  onClose: () => void;
  onPurged: (id: string) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 关闭动画期间沿用上一次的内容；换了项目时清掉上一次的错误
  const [shown, setShown] = useState({ project, title });
  if (project !== null && project !== shown.project) {
    setShown({ project, title });
    setError(null);
  }

  const confirm = async () => {
    if (!shown.project || running || blocked) return;
    setRunning(true);
    setError(null);
    try {
      await API.purgeDeletedProject(shown.project.id);
      onPurged(shown.project.id);
      onClose();
    } catch (err) {
      setError(t("dashboard:trash_action_failed", { message: errMsg(err) }));
    } finally {
      setRunning(false);
    }
  };

  return (
    <AlertDialog
      open={project !== null}
      onOpenChange={(next) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!next && !running) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:deleted_projects_purge_title", { title: shown.title })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>{t("dashboard:deleted_projects_purge_description")}</AlertDialogDescription>
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
            {t("dashboard:trash_purge")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
