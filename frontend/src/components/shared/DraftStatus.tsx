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

interface DiscardDraftDialogProps {
  open: boolean;
  agentOwned: boolean;
  fallbackText: string;
  loading: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export function DiscardDraftDialog({ open, agentOwned, fallbackText, loading, onConfirm, onCancel }: DiscardDraftDialogProps) {
  const { t } = useTranslation("dashboard");
  const confirmLabel = agentOwned ? t("draft_agent_discard") : t("draft_discard_action");
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !loading) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{agentOwned ? t("draft_agent_discard_title") : t("draft_discard_title")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={agentOwned ? t("draft_agent_discard_title") : t("draft_discard_title")}>
          <AlertDialogDescription>{fallbackText}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={loading} onClick={() => void onConfirm()}>
            {loading ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
