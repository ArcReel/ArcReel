import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MigrationReportContent } from "@/components/shared/MigrationReportContent";
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
import type { WorkflowMigrationReport } from "@/types/workflow";
import type { EpisodeLedger } from "@/utils/episode-display";

export interface PendingImportReport {
  report: WorkflowMigrationReport;
  episodes: EpisodeLedger;
}

interface ImportReportConfirmDialogProps {
  pending: PendingImportReport | null;
  importing: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ImportReportConfirmDialog({ pending, importing, onConfirm, onCancel }: ImportReportConfirmDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [shown, setShown] = useState(pending);
  if (pending !== null && pending !== shown) setShown(pending);

  return (
    <AlertDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open && !importing) onCancel();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:import_confirm_title")}</AlertDialogTitle>
          <AlertDialogDescription>{t("dashboard:import_confirm_description")}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("dashboard:import_confirm_title")}>
          {shown ? <MigrationReportContent report={shown.report} episodes={shown.episodes} /> : null}
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={importing}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction disabled={importing} onClick={onConfirm}>
            {importing && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {t("dashboard:import_confirm_action")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
