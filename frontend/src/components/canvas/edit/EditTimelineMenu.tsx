import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, MoreHorizontal, Pencil, Trash2 } from "lucide-react";

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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { EditTimelineSummary } from "@/types/edit-timeline";
import { errMsg } from "@/utils/async";

/** Matches server `TIMELINE_NAME_MAX_LENGTH`. */
const NAME_MAX_LENGTH = 40;

interface EditTimelineMenuProps {
  projectName: string;
  /** Menu target timeline, the currently selected tab. */
  timeline: EditTimelineSummary;
  onRenamed: () => void;
  onDeleted: () => void;
}

type OpenDialog = "rename" | "delete" | null;

/**
 * More-actions menu beside timeline tabs: rename and delete with confirmation. Copy/rollback are Agent-only.
 * Success appears only through tab changes, without toasts; failure reasons stay in dialogs.
 */
export function EditTimelineMenu({ projectName, timeline, onRenamed, onDeleted }: EditTimelineMenuProps) {
  const { t } = useTranslation("dashboard");
  const [dialog, setDialog] = useState<OpenDialog>(null);
  const label = t("edit_view_menu_aria", { name: timeline.name });

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={label} />}>
          <MoreHorizontal aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-40">
          <DropdownMenuItem onClick={() => setDialog("rename")}>
            <Pencil aria-hidden />
            {t("edit_view_menu_rename")}
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={() => setDialog("delete")}>
            <Trash2 aria-hidden />
            {t("edit_view_menu_delete")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {/* Remount on timeline changes; reopening the same timeline lets its dialog clear previous input/errors. */}
      <RenameDialog
        key={`rename-${timeline.id}`}
        open={dialog === "rename"}
        projectName={projectName}
        timeline={timeline}
        onClose={() => setDialog(null)}
        onRenamed={onRenamed}
      />
      <DeleteDialog
        key={`delete-${timeline.id}`}
        open={dialog === "delete"}
        projectName={projectName}
        timeline={timeline}
        onClose={() => setDialog(null)}
        onDeleted={onDeleted}
      />
    </>
  );
}

interface DialogProps {
  open: boolean;
  projectName: string;
  timeline: EditTimelineSummary;
  onClose: () => void;
}

function RenameDialog({ open, projectName, timeline, onClose, onRenamed }: DialogProps & { onRenamed: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const formId = useId();
  const inputId = useId();
  const hintId = useId();
  const [draft, setDraft] = useState(timeline.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setDraft(timeline.name);
      setError(null);
    }
  }
  const name = draft.trim();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name || saving) return;
    if (name === timeline.name) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await API.renameEditTimeline(projectName, timeline.id, name);
      onRenamed();
      onClose();
    } catch (cause) {
      setError(t("edit_view_rename_failed", { message: errMsg(cause) }));
    } finally {
      // Renaming preserves IDs without remounting: reset on success too, or the entire dialog stays disabled next time.
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Ignore closing during submission so dialogs do not disappear before requests finish.
        if (!next && !saving) onClose();
      }}
    >
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{t("edit_view_rename_title")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <form id={formId} className="flex flex-col gap-2" onSubmit={(event) => void submit(event)}>
            <Label htmlFor={inputId}>{t("edit_view_rename_label")}</Label>
            <Input
              id={inputId}
              value={draft}
              maxLength={NAME_MAX_LENGTH}
              disabled={saving}
              aria-describedby={hintId}
              onChange={(event) => setDraft(event.target.value)}
            />
            <p id={hintId} className="text-xs text-muted-foreground">
              {t("edit_view_rename_hint")}
            </p>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </form>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button type="submit" form={formId} disabled={!name || saving}>
            {saving && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {t("edit_view_rename_confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({ open, projectName, timeline, onClose, onDeleted }: DialogProps & { onDeleted: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setError(null);
  }

  const confirm = async () => {
    setDeleting(true);
    setError(null);
    try {
      await API.deleteEditTimeline(projectName, timeline.id);
      onDeleted();
      onClose();
    } catch (cause) {
      setError(t("edit_view_delete_failed", { message: errMsg(cause) }));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // Ignore Escape during submission so the dialog cannot disappear before the request completes
        if (!next && !deleting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("edit_view_delete_title", { name: timeline.name })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("edit_view_delete_title", { name: timeline.name })}>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>{t("edit_view_delete_description")}</AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={deleting} onClick={() => void confirm()}>
            {deleting && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {t("edit_view_delete_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
