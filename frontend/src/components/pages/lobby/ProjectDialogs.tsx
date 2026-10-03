import { useId, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errMsg } from "@/utils/async";
import { getProjectDisplayName } from "@/utils/project-display";
import type { ProjectSummary } from "@/types";

interface ProjectDialogProps {
  /** 要操作的项目；为 null 时对话框关闭。 */
  project: ProjectSummary | null;
  onClose: () => void;
  /** 改动已保存，列表需要重新加载。 */
  onDone: () => void;
}

/**
 * 关闭动画期间 `project` 已是 null，保留上一次的项目避免内容闪成空白。
 * 每次打开（含再次打开同一个项目）时调用 `onOpen` 重置表单。
 */
function useShownProject(project: ProjectSummary | null, onOpen: (project: ProjectSummary) => void) {
  const [prev, setPrev] = useState<ProjectSummary | null>(null);
  const [shown, setShown] = useState<ProjectSummary | null>(null);
  if (project !== prev) {
    setPrev(project);
    if (project) {
      setShown(project);
      onOpen(project);
    }
  }
  return shown;
}

/** 改项目标题。项目 ID 决定项目的地址与目录，不随标题变化。 */
export function RenameProjectDialog({ project, onClose, onDone }: ProjectDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const formId = useId();
  const inputId = useId();
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shown = useShownProject(project, (opened) => {
    setTitle(opened.title);
    setError(null);
  });
  const trimmed = title.trim();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!shown || !trimmed || submitting) return;
    if (trimmed === shown.title) {
      onClose();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await API.updateProject(shown.name, { title: trimmed });
      onDone();
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={project !== null}
      onOpenChange={(open) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!open && !submitting) onClose();
      }}
    >
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{t("dashboard:lobby_rename_title")}</DialogTitle>
          <DialogDescription>{t("dashboard:lobby_rename_desc", { name: shown?.name ?? "" })}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <form id={formId} className="flex flex-col gap-2" onSubmit={(event) => void submit(event)}>
            <Label htmlFor={inputId}>{t("dashboard:lobby_rename_label")}</Label>
            <Input id={inputId} value={title} onChange={(event) => setTitle(event.target.value)} />
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </form>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" disabled={submitting} onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button type="submit" form={formId} disabled={!trimmed || submitting}>
            {submitting && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {t("common:save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 删除项目不可撤销，先用 AlertDialog 确认。 */
export function DeleteProjectDialog({ project, onClose, onDone }: ProjectDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shown = useShownProject(project, () => setError(null));
  const title = shown ? getProjectDisplayName(shown.title, t("dashboard:untitled_project")) : "";

  const handleDelete = async () => {
    if (!shown) return;
    setSubmitting(true);
    setError(null);
    try {
      await API.deleteProject(shown.name);
      onDone();
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open={project !== null}
      onOpenChange={(open) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!open && !submitting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:lobby_delete_title", { title })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>{t("dashboard:lobby_delete_desc")}</AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void handleDelete()}>
            {submitting && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {t("dashboard:delete_project")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
