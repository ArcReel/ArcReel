import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Wand2 } from "lucide-react";
import { isAssetBusy } from "@/components/canvas/lorebook/assetBusyGuard";
import { enqueueImageEdit } from "@/actions/generation";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import {
  selectHasActiveTaskForScriptFile,
  useTasksStore,
  type ImageEditResourceKind,
} from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { TooltipIconButton } from "./TooltipIconButton";

export type ImageEditResourceType = ImageEditResourceKind;

interface ImageEditTarget {
  projectName: string;
  resourceType: ImageEditResourceType;
  resourceId: string;
  /** Episode script file required for storyboard edits, ignored for other asset types. */
  scriptFile?: string | null;
  /** Editable current image exists; otherwise disable and request generation/upload first. */
  hasImage: boolean;
  /** Generation/edit tasks occupy this resource; disable editing. */
  busy?: boolean;
}

/**
 * Local-edit icon entry on image cards: icon button/instruction dialog.
 */
export function ImageEditButton(props: ImageEditTarget) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const disabled = Boolean(props.busy) || !props.hasImage;
  const label = t("image_edit_action");

  return (
    <>
      <TooltipIconButton
        label={label}
        hint={props.hasImage ? undefined : t("image_edit_no_image_hint")}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <Wand2 aria-hidden />
      </TooltipIconButton>
      <ImageEditDialog {...props} open={open} onOpenChange={setOpen} />
    </>
  );
}

/**
 * Local-edit instructions: current image as base plus instruction prompt enqueue i2i; SSE fingerprints refresh on completion.
 * Controlled opening supports more menus and other entries without dedicated buttons.
 */
export function ImageEditDialog({
  projectName,
  resourceType,
  resourceId,
  scriptFile,
  hasImage,
  busy = false,
  open,
  onOpenChange,
}: ImageEditTarget & { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const fieldId = useId();
  const disabled = busy || !hasImage;

  const handleSubmit = async () => {
    const trimmed = instruction.trim();
    // Still guard disabled (busy||!hasImage): busy includes more than resource-store occupancy, such as episode grid tasks
    // during grid assembly (splits overwrite multiple storyboards; selectHasActiveTaskForScriptFile),
    // or card-local base-image uploads. Neither enters this resource's occupancy, and keyboard submission bypasses button
    // disabled attributes; this is the sole enforcement point for that layer.
    if (!trimmed || submitting || disabled) return;
    // Also recheck fresh getState(): reactive busy updates while dialogs remain open depend on parent
    // rerendering and may lag. Read current stores directly for resourceType/resourceId
    // in the same occupancy slot (taskResourceKind maps image_edit by resource_type).
    if (isAssetBusy(resourceType, projectName, resourceId)) {
      useAppStore.getState().pushToast(t("image_edit_resource_busy"), "error");
      return;
    }
    const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
    // Storyboard occupancy misses grid tasks because resource_id is grid_id; with grid assembly enabled,
    // recheck episode splitting by scriptFile or fresh reads miss this dimension before busy props catch up;
    // see selectHasActiveTaskForScriptFile and GridImageToVideoCanvas.
    // gridActiveForEpisode。
    if (
      resourceType === "storyboard" &&
      scriptFile &&
      selectHasActiveTaskForScriptFile(tasks, "grid", scriptFile, projectName, optimisticActiveScriptFile)
    ) {
      useAppStore.getState().pushToast(t("image_edit_resource_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueImageEdit(projectName, {
        resourceType,
        resourceId,
        instruction: trimmed,
        scriptFile: resourceType === "storyboard" ? scriptFile ?? null : null,
      });
      setInstruction("");
      onOpenChange(false);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Ignore Escape and backdrop clicks while submission is in flight
        if (!next && submitting) return;
        onOpenChange(next);
      }}
    >
      <DialogContent showCloseButton={!submitting}>
        <DialogHeader>
          <DialogTitle>{t("image_edit_modal_title")}</DialogTitle>
          <DialogDescription>{t("image_edit_modal_desc", { name: resourceId })}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={fieldId}>{t("image_edit_instruction_label")}</Label>
            <Textarea
              id={fieldId}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  void handleSubmit();
                }
              }}
              rows={3}
              // eslint-disable-next-line jsx-a11y/no-autofocus -- Opening the dialog immediately focuses instructions, matching open-and-write expectations
              autoFocus
              placeholder={t("image_edit_instruction_placeholder")}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />} disabled={submitting}>
            {t("common:cancel")}
          </DialogClose>
          <Button onClick={() => void handleSubmit()} disabled={submitting || instruction.trim().length === 0 || disabled}>
            {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : <Wand2 aria-hidden data-icon="inline-start" />}
            {submitting ? t("image_edit_submitting") : t("image_edit_submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
