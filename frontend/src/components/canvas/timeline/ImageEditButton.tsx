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
  isResourceBusy,
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
  scriptFile?: string | null;
  hasImage: boolean;
  busy?: boolean;
}

function isImageEditBusy({ projectName, resourceType, resourceId, scriptFile }: ImageEditTarget) {
  if (resourceType === "storyboard") {
    return isResourceBusy("storyboard", projectName, resourceId) ||
      Boolean(scriptFile && selectHasActiveTaskForScriptFile(useTasksStore.getState(), "grid", scriptFile, projectName));
  }
  if (resourceType === "character_derivative") {
    return isAssetBusy("derivative_image", "character", projectName, resourceId);
  }
  return isAssetBusy("image", resourceType, projectName, resourceId);
}

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
        onClick={() => {
          if (isImageEditBusy(props)) {
            useAppStore.getState().pushToast(t("image_edit_resource_busy"), "error");
            return;
          }
          setOpen(true);
        }}
      >
        <Wand2 aria-hidden />
      </TooltipIconButton>
      <ImageEditDialog {...props} open={open} onOpenChange={setOpen} />
    </>
  );
}

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
    if (!trimmed || submitting || disabled) return;
    if (isImageEditBusy({ projectName, resourceType, resourceId, scriptFile, hasImage })) {
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
              // eslint-disable-next-line jsx-a11y/no-autofocus -- 对话框打开即聚焦指令输入，符合“点开就写”的心智
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
