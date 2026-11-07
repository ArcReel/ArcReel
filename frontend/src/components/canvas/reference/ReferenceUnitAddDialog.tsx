import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
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

/**
 * Enter body text before adding a video unit. The body is its sole content source of truth; the server rejects empty bodies with needs_replan,
 * so submission requires nonempty text. The caller owns the body: preserve it on failure and clear it on reopening.
 */
export function ReferenceUnitAddDialog({
  open,
  prompt,
  submitting,
  onPromptChange,
  onSubmit,
  onClose,
}: {
  open: boolean;
  prompt: string;
  submitting: boolean;
  onPromptChange: (next: string) => void;
  onSubmit: (prompt: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const textareaId = useId();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Do not close during submission; keep the dialog unchanged until results arrive
        if (!next && !submitting) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("reference_unit_add_title")}</DialogTitle>
          <DialogDescription>{t("reference_unit_add_desc")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-2">
            <Label htmlFor={textareaId}>{t("reference_unit_add_label")}</Label>
            <Textarea
              id={textareaId}
              value={prompt}
              placeholder={t("reference_editor_placeholder")}
              onChange={(e) => onPromptChange(e.target.value)}
              disabled={submitting}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" disabled={submitting} />}>{t("common:cancel")}</DialogClose>
          <Button disabled={submitting || !prompt.trim()} onClick={() => onSubmit(prompt)}>
            {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {t("reference_unit_add_confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
