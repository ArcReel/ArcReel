import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
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
import type { PromptOverwrite } from "@/types";

interface PromptOverwriteDialogProps {
  overwrite: PromptOverwrite | null;
  text: string | null;
  submitting: boolean;
  confirmDisabled?: boolean;
  onConfirm: (revision: string | null) => void;
  onClose: () => void;
}

export function PromptOverwriteDialog({
  overwrite,
  text,
  submitting,
  confirmDisabled = false,
  onConfirm,
  onClose,
}: PromptOverwriteDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  return (
    <AlertDialog
      open={overwrite !== null}
      onOpenChange={(next) => {
        if (!next && !submitting) onClose();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{t("prompt_authoring_overwrite_title")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("prompt_authoring_overwrite_title")}>
          <AlertDialogDescription>
            <span className="whitespace-pre-line wrap-break-word">{text}</span>
          </AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={submitting || confirmDisabled}
            onClick={() => {
              if (overwrite) onConfirm(overwrite.revision);
            }}
          >
            {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {t("prompt_authoring_ai_rewrite")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
