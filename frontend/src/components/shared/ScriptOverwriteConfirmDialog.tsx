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
import type { ScriptOverwrite } from "@/types";
import { itemIdsAsPositionsInText } from "@/utils/episode-display";
import { useProjectsStore } from "@/stores/projects-store";

interface ScriptOverwriteConfirmDialogProps {
  open: boolean;
  overwrite: ScriptOverwrite;
  loading: boolean;
  /** Disable confirmation in the dialog when prerequisites fail, such as an unresolvable video model. */
  confirmDisabled?: boolean;
  /** Title and confirm-button copy; defaults to content confirmation's Overwrite and confirm. */
  title?: string;
  confirmLabel?: string;
  loadingLabel?: string;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/**
 * Irreversible confirmation before replacing a formal script, used by content confirmation and full ad/short-film regeneration: display the server-generated loss list,
 * shortening only item IDs to their episode-local part and matching Agent receipts' counting criteria.
 */
export function ScriptOverwriteConfirmDialog({
  open,
  overwrite,
  loading,
  confirmDisabled = false,
  title,
  confirmLabel,
  loadingLabel,
  onConfirm,
  onCancel,
}: ScriptOverwriteConfirmDialogProps) {
  const { t } = useTranslation("dashboard");
  const scripts = useProjectsStore((s) => s.currentScripts);

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // Ignore Escape during submission so the dialog cannot disappear before the request completes
        if (!next && !loading) onCancel();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{title ?? t("review_overwrite_title")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={title ?? t("review_overwrite_title")}>
          <AlertDialogDescription>
            <span className="whitespace-pre-line wrap-break-word">{itemIdsAsPositionsInText(overwrite.text, scripts, t)}</span>
          </AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={loading || confirmDisabled}
            onClick={() => void onConfirm()}
          >
            {loading ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {loading ? (loadingLabel ?? t("review_confirming")) : (confirmLabel ?? t("review_overwrite_confirm"))}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
