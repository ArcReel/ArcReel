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
  /** Target projects; null closes dialogs. */
  project: ProjectSummary | null;
  onClose: () => void;
  /** Changes persisted; lists need reloads. */
  onDone: () => void;
}

/**
 * During close animations, `project` is null; retain previous projects to avoid blank-content flashes.
 * Invoke `onOpen` on every opening, including identical projects, to reset forms.
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

/** Change project titles. Project IDs determine addresses/directories and never change with titles. */
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
        // Ignore Escape during submission so the dialog cannot disappear before the request completes
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

/** Confirm project deletion through AlertDialog first; projects move to Deleted projects, restorable within retention periods. */
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
        // Ignore Escape during submission so the dialog cannot disappear before the request completes
        if (!open && !submitting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:lobby_delete_title", { title })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("dashboard:lobby_delete_title", { title })}>
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
