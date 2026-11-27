import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { DraftImage } from "@/components/shared/DraftImage";
import { useDraftCountStore } from "@/stores/draft-count-store";
import { API } from "@/api";
import { ROUTE_APP_PROJECTS, WORKSPACE_ROUTE_DRAFTS } from "@/app-routes";
import { isTerminalStatus, type TaskItem } from "@/types/task";
import { buttonVariants } from "@/components/ui/button";

export function FreeImageResult({ projectName, taskId, draftId }: { projectName: string; taskId: string; draftId: string }) {
  const { t } = useTranslation("dashboard");
  const [task, setTask] = useState<TaskItem>();
  const [unreachable, setUnreachable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await API.getTask(taskId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (next.status === "succeeded" && next.result?.id === draftId) {
          useDraftCountStore.getState().invalidate(projectName);
        }
        setTask(next);
        setUnreachable(false);
        if (isTerminalStatus(next.status)) return;
      } catch {
        if (controller.signal.aborted) return;
        setUnreachable(true);
      }
      timer = setTimeout(() => { void poll(); }, 2500);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [taskId, draftId, projectName]);
  const success = task?.status === "succeeded" && task.result?.id === draftId;
  const text = success ? t("image_draft_saved") : task?.status === "cancelled" ? t("image_draft_cancelled")
    : task && isTerminalStatus(task.status) ? task.error_message || t("image_draft_failed")
      : unreachable ? t("image_draft_status_unavailable") : task?.status === "running" ? t("image_draft_running") : t("image_draft_queued");
  return (
    <div className="mx-3 mb-2 flex shrink-0 items-center gap-2 rounded-lg border bg-muted/20 p-2 text-xs">
      {success && <DraftImage projectName={projectName} draftId={draftId} alt={typeof task.payload.prompt === "string" ? task.payload.prompt : t("image_draft_preview")} className="size-12 shrink-0 rounded object-contain" />}
      <p role="status" className="min-w-0 flex-1 break-words">{text}</p>
      {success && <Link className={buttonVariants({ variant: "outline", size: "sm" })} href={`~${ROUTE_APP_PROJECTS}/${encodeURIComponent(projectName)}/${WORKSPACE_ROUTE_DRAFTS}`}>{t("image_draft_library")}</Link>}
    </div>
  );
}
